# cip-icing

Every hour, a GitHub Actions job downloads NOAA's **Current Icing Product (CIP v2, 3 km)** from NOMADS and shrinks it to a small grid (about 1.5 MB). It then uploads the result to the web host over FTPS for the VisionProFlight 3D icing view.

What gets uploaded to the host folder:
- `cip_latest.json`: metadata (valid time, grid layout, name of the data file)
- `cip_YYYYMMDDHH.bin`: the grid, compressed with raw deflate. The newest 3 are kept.

The file format is described at the top of `cip_extract.py`.

## One-time setup

1. **Create the repository.** On github.com, click **New repository** and name it `cip-icing`. Make it **Public**, because scheduled jobs are free and unlimited in public repositories. Nothing secret is stored in the code: the FTP login lives in encrypted secrets.
2. **Upload these files.** Upload the contents of this folder, keeping the `.github/workflows/cip.yml` path. Either:
   - drag them into the "uploading an existing file" page, or
   - run these commands from this folder:
     ```bash
     git init && git add . && git commit -m "CIP icing job" && git branch -M main
     git remote add origin https://github.com/<your-account>/cip-icing.git && git push -u origin main
     ```
3. **Set up an FTP account on GoDaddy.** In cPanel, open **FTP Accounts**. It's best to create a separate account limited to one folder, for example `public_html/Icing`. Note the FTP server name (usually `ftp.yourdomain.com`), the user name and the password.
4. **Add secrets on GitHub.** In the repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   | Name | Value |
   |---|---|
   | `FTP_HOST` | e.g. `ftp.roberturschel.com` |
   | `FTP_USER` | the FTP account's user name (cPanel shows the full form, e.g. `cip@roberturschel.com`) |
   | `FTP_PASSWORD` | its password |
   | `FTP_DIR` | the folder **as seen by that FTP account**. If the account was created with the folder `public_html/Icing`, its root already *is* that folder, so use `/`. If it's your main cPanel FTP login, use `/public_html/Icing`. |
5. **Test it.** Go to **Actions → CIP icing → Run workflow**. After about 2 minutes, open `https://www.roberturschel.com/Icing/cip_latest.json` in a browser. You should see the metadata with a recent `validTime`.

The workflow then runs every hour at about :25. Once a month it makes a tiny commit so that GitHub doesn't pause the schedule.

## Notes
- If GoDaddy refuses an encrypted (FTPS) connection, add the secret `FTP_PLAIN` = `1`. That sends the password unencrypted, so use it only as a last resort.
- The app reads from `IcingRouteService.cipBaseURL`, which defaults to `https://www.roberturschel.com/Icing/`. If you use a different folder, change it there.
- Local test with a GRIB2 file you already have: `python cip_extract.py --grib cip.t21z.3km.grib2 --out out`
