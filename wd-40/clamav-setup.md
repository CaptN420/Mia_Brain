# ClamAV setup for wd-40

ClamAV is the optional on-disk scan engine (step 3 of the shield). The
`winget install` step needs **admin rights** — it failed earlier with exit
code 1602 because the installer's UAC prompt was cancelled in silent mode.

## Install (one-time, as Administrator)

1. Open a terminal **as Administrator** (Win+X → Terminal (Admin)).
2. Run:

   ```powershell
   winget install --id Cisco.ClamAV --accept-source-agreements --accept-package-agreements
   ```

3. Reload the PATH in any new terminal, then confirm:

   ```powershell
   where clamscan; where freshclam
   ```

4. Download signatures (needs network):

   ```powershell
   freshclam
   ```

## After install

`python wd-40/filecheck.py <file>` will auto-detect ClamAV and run a deep
scan in addition to the hash check. Until then, filecheck still works on the
MalwareBazaar hash feed alone and prints a "ClamAV not installed" note.

## Note
ClamAV is **defense-in-depth only**. The hash feed (URLhaus/MalwareBazaar)
and the network IOC check (Feodo/URLhaus) are the primary detections and
need no admin and no install — they run out of the box.
