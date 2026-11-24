#!/usr/bin/env python3
"""
Upload the newest cip_*.bin and cip_latest.json to the web host over FTPS,
then remove older cip_*.bin files there (keeps the newest KEEP).

Settings come from environment variables (GitHub Actions secrets):
  FTP_HOST      e.g. ftp.roberturschel.com
  FTP_USER
  FTP_PASSWORD
  FTP_DIR       folder on the host, e.g. /public_html/Icing
  FTP_PLAIN     set to "1" only if the host refuses FTPS (sends the password unencrypted)
"""

import ftplib
import glob
import os
import sys

KEEP = 3


def main(out_dir):
    host = os.environ["FTP_HOST"]
    user = os.environ["FTP_USER"]
    password = os.environ["FTP_PASSWORD"]
    remote = os.environ.get("FTP_DIR", "/public_html/Icing")

    if os.environ.get("FTP_PLAIN") == "1":
        ftp = ftplib.FTP(host, timeout=60)
        ftp.login(user, password)
    else:
        ftp = ftplib.FTP_TLS(host, timeout=60)
        ftp.login(user, password)
        ftp.prot_p()   # encrypt the data connection too

    # Create the folder if needed, one level at a time.
    for part in [p for p in remote.split("/") if p]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)
    if remote.startswith("/"):
        ftp.cwd(remote)

    bins = sorted(glob.glob(os.path.join(out_dir, "cip_*.bin")))
    if not bins:
        sys.exit("nothing to upload")
    newest = bins[-1]
    name = os.path.basename(newest)

    # Data file first; the metadata that points at it goes last, via a temporary
    # name and a rename, so the app never sees metadata for a half-written file.
    with open(newest, "rb") as f:
        ftp.storbinary(f"STOR {name}", f)
    with open(os.path.join(out_dir, "cip_latest.json"), "rb") as f:
        ftp.storbinary("STOR cip_latest.json.tmp", f)
    try:
        ftp.delete("cip_latest.json")
    except ftplib.error_perm:
        pass
    ftp.rename("cip_latest.json.tmp", "cip_latest.json")

    old = sorted(n for n in ftp.nlst() if n.startswith("cip_") and n.endswith(".bin"))
    for n in old[:-KEEP]:
        try:
            ftp.delete(n)
        except ftplib.error_perm:
            pass

    ftp.quit()
    print("uploaded", name, "to", remote)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
