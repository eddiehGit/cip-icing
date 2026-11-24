#!/usr/bin/env python3
"""
Download the latest NOAA Current Icing Product (CIP v2, 3 km) from NOMADS and
reduce it to a compact grid the VisionProFlight app can read.

Output (written to --out):
  cip_YYYYMMDDHH.bin   raw-deflate compressed bytes (Apple Compression COMPRESSION_ZLIB)
  cip_latest.json      metadata, names the .bin to read

Grid in the .bin: regular lat/lon boxes of RES degrees; each box keeps the worst
value of all 3 km points inside it. Layout after decompression, uint8, C order:
  field[3][level][lat][lon]
  field 0 = severity  0 none, 1 trace, 2 light, 3 moderate, 4 heavy, 5 severe;
                      255 = below ground / no data
  field 1 = probability, percent 0..100
  field 2 = SLD potential, percent 0..100
Level k covers (k*LEVEL_FT, (k+1)*LEVEL_FT] feet MSL, i.e. level 0 = surface..1000 ft.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request
import zlib

import eccodes
import numpy as np

NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/cip/prod"
UA = "cip-icing-extract/1.0"

# Output grid (covers the CIP domain)
LAT0, LAT1 = 20.0, 54.0
LON0, LON1 = -135.0, -59.0
RES = 0.1
LEVEL_FT = 1000
TOP_FT = 30000

PARAM_SEVERITY = 37
PARAM_PROB = 233
PARAM_SLD = 217


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def latest_url(max_hours_back=6):
    now = dt.datetime.now(dt.timezone.utc)
    for back in range(max_hours_back + 1):
        t = now - dt.timedelta(hours=back)
        url = f"{NOMADS}/cip.{t:%Y%m%d}/cip.t{t:%H}z.3km.grib2"
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    return url, t.replace(minute=0, second=0, microsecond=0)
        except Exception:
            pass
    raise SystemExit("No CIP file found in the last %d hours" % max_hours_back)


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def extract(grib_path):
    nlat = int(round((LAT1 - LAT0) / RES))
    nlon = int(round((LON1 - LON0) / RES))
    nlev = TOP_FT // LEVEL_FT
    sev = np.full((nlev, nlat * nlon), 255, dtype=np.uint8)
    prob = np.zeros((nlev, nlat * nlon), dtype=np.uint8)
    sld = np.zeros((nlev, nlat * nlon), dtype=np.uint8)
    # A box stays 255 only if every point in it is missing at that level.
    have = np.zeros((nlev, nlat * nlon), dtype=bool)

    box = order = starts = None
    valid = None
    with open(grib_path, "rb") as f:
        while True:
            h = eccodes.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                if box is None:
                    lats = eccodes.codes_get_array(h, "latitudes")
                    lons = eccodes.codes_get_array(h, "longitudes")
                    lons = np.where(lons > 180, lons - 360, lons)
                    iy = np.floor((lats - LAT0) / RES).astype(np.int64)
                    ix = np.floor((lons - LON0) / RES).astype(np.int64)
                    inside = (iy >= 0) & (iy < nlat) & (ix >= 0) & (ix < nlon)
                    box = np.where(inside, iy * nlon + ix, -1)
                    # Sort points by box once; reduceat then gives a per-box max quickly.
                    sel = np.nonzero(box >= 0)[0]
                    order = sel[np.argsort(box[sel], kind="stable")]
                    sorted_box = box[order]
                    starts = np.r_[0, np.nonzero(np.diff(sorted_box))[0] + 1]
                    box_ids = sorted_box[starts]
                    d, t = eccodes.codes_get(h, "dataDate"), eccodes.codes_get(h, "dataTime")
                    valid = dt.datetime.strptime(f"{d}{t:04d}", "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)

                pn = eccodes.codes_get(h, "parameterNumber")
                if pn not in (PARAM_SEVERITY, PARAM_PROB, PARAM_SLD):
                    continue
                height_ft = eccodes.codes_get(h, "level") / 0.3048
                k = int(np.ceil(height_ft / LEVEL_FT - 1e-6)) - 1   # 500 ft -> 0, 1000 ft -> 0, 1500 ft -> 1
                if k < 0 or k >= nlev:
                    continue

                v = eccodes.codes_get_values(h)
                missing = eccodes.codes_get(h, "missingValue")
                v = v[order]
                ok = v != missing
                vv = np.where(ok, v, -1.0)
                boxmax = np.maximum.reduceat(vv, starts)
                present = boxmax >= 0

                if pn == PARAM_SEVERITY:
                    cur = sev[k, box_ids]
                    cur = np.where(cur == 255, 0, cur)
                    newv = np.clip(np.rint(boxmax), 0, 5).astype(np.uint8)
                    merged = np.maximum(cur, newv)
                    sev[k, box_ids] = np.where(present | have[k, box_ids], merged, 255)
                    have[k, box_ids] |= present
                else:
                    pct = np.clip(np.rint(boxmax * 100), 0, 100).astype(np.uint8)
                    target = prob if pn == PARAM_PROB else sld
                    target[k, box_ids] = np.maximum(target[k, box_ids], np.where(present, pct, 0))
            finally:
                eccodes.codes_release(h)

    if box is None:
        raise SystemExit("GRIB file had no messages")
    # Points with probability/SLD but no severity anywhere keep severity 255 only when truly empty.
    grid = np.stack([sev, prob, sld]).reshape(3, nlev, nlat, nlon)
    return grid, valid, nlat, nlon, nlev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--grib", help="use a local GRIB2 file instead of downloading")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    if args.grib:
        grib, source = args.grib, os.path.basename(args.grib)
    else:
        url, _ = latest_url()
        grib, source = os.path.join(args.out, "cip.grib2"), url
        log("downloading", url)
        download(url, grib)

    log("extracting")
    grid, valid, nlat, nlon, nlev = extract(grib)

    comp = zlib.compressobj(9, zlib.DEFLATED, -15)   # raw deflate, readable by Apple's COMPRESSION_ZLIB
    payload = comp.compress(grid.tobytes(order="C")) + comp.flush()
    name = f"cip_{valid:%Y%m%d%H}.bin"
    with open(os.path.join(args.out, name), "wb") as f:
        f.write(payload)

    meta = {
        "product": "NOAA CIP v2 (3 km), reduced",
        "source": source,
        "validTime": valid.strftime("%Y-%m-%dT%H:%MZ"),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "file": name,
        "bytes": len(payload),
        "rawBytes": int(grid.size),
        "compression": "deflate-raw",
        "lat0": LAT0, "lon0": LON0, "res": RES,
        "nlat": nlat, "nlon": nlon,
        "levelFt": LEVEL_FT, "nlev": nlev,
        "fields": ["severity", "probabilityPct", "sldPct"],
        "severityNames": ["none", "trace", "light", "moderate", "heavy", "severe"],
        "missing": 255,
    }
    with open(os.path.join(args.out, "cip_latest.json"), "w") as f:
        json.dump(meta, f, indent=1)

    if not args.grib:
        os.remove(grib)
    log("wrote", name, f"{len(payload)/1e6:.2f} MB", "valid", meta["validTime"])


if __name__ == "__main__":
    sys.exit(main())
