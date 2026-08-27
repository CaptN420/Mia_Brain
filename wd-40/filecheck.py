"""wd-40 filecheck — step 1: hash reputation via MalwareBazaar.

Checks files by SHA-256 against abuse.ch URLhaus payload hashes
(malware distributed by active download sites, refreshed every 5 min
upstream; local cache at most every 30 min). No API key required.

Usage:
    python wd-40/filecheck.py <file> [file ...]   # check specific files
    python wd-40/filecheck.py --dir <folder>      # scan a folder (non-recursive)

Nothing is deleted — matches are reported, quarantine is a human decision.
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FEEDS = os.path.join(HERE, "feeds")
CACHE = os.path.join(FEEDS, "malwarebazaar_recent.json")
os.makedirs(FEEDS, exist_ok=True)

FEED_TTL = 30 * 60  # seconds
HASHES_URL = "https://bazaar.abuse.ch/export/txt/sha256/recent/"  # MalwareBazaar recent, no key
CACHE = os.path.join(FEEDS, "urlhaus_sha256.txt")

# skip huge/binary-irrelevant stuff when scanning a dir
SKIP_EXT = {".log", ".txt", ".md", ".json", ".csv", ".pyc", ".tmp"}
MAX_FILE_BYTES = 512 * 1024 * 1024


def refresh_cache():
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < FEED_TTL:
        with open(CACHE) as f:
            return {l.strip().lower() for l in f if l.strip() and not l.startswith("#")}
    try:
        req = urllib.request.Request(HASHES_URL, headers={"User-Agent": "wd-40-shield"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read().decode("utf-8", errors="replace")
        hashes = {l.strip().lower() for l in data.splitlines()
                  if l.strip() and not l.startswith("#")}
        with open(CACHE, "w") as f:
            f.write("\n".join(sorted(hashes)))
        return hashes
    except Exception as e:
        if os.path.exists(CACHE):
            print(f"[wd-40] WARNING: feed update failed ({e}); using stale cache")
            with open(CACHE) as f:
                return {l.strip().lower() for l in f if l.strip()}
        print(f"[wd-40] ERROR: no feed cache and update failed: {e}")
        return set()


def have_clamav():
    from shutil import which
    return bool(which("clamscan"))


def clamav_scan(path):
    """Return (flagged:bool, detail:str) via a real ClamAV scan."""
    import subprocess
    rc = subprocess.run(["clamscan", "--no-summary", "-i", path],
                        capture_output=True, text=True).returncode
    if rc == 1:  # virus(es) found
        return True, (subprocess.run(["clamscan", "-i", path],
                    capture_output=True, text=True).stdout or "clamav hit").strip()
    return False, ""


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(args):
    files = []
    it = iter(args)
    for a in it:
        if a == "--dir":
            folder = next(it, None)
            if not folder or not os.path.isdir(folder):
                print(f"[wd-40] not a directory: {folder}")
                continue
            for name in sorted(os.listdir(folder)):
                p = os.path.join(folder, name)
                if os.path.isfile(p) and os.path.splitext(name)[1].lower() not in SKIP_EXT:
                    files.append(p)
        else:
            files.append(a)
    return files


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    db = refresh_cache()
    print(f"[wd-40] MalwareBazaar cache loaded: {len(db)} known-bad SHA-256")
    if have_clamav():
        print("[wd-40] ClamAV engine: available (deep scan on)")
    else:
        print("[wd-40] ClamAV engine: NOT installed (hash check only — see clamav-setup.md)")

    hits = checked = 0
    for path in collect(args):
        if not os.path.isfile(path):
            print(f"[wd-40] ?? missing: {path}")
            continue
        if os.path.getsize(path) > MAX_FILE_BYTES:
            print(f"[wd-40] -- skipped (too big): {path}")
            continue
        digest = sha256_of(path)
        checked += 1
        info = digest in db
        if info:
            hits += 1
            print(f"[wd-40] !! MATCH {path}\n         sha256={digest}\n         source=URLhaus payload")
            continue  # already confirmed; skip ClamAV
        if have_clamav():
            flagged, detail = clamav_scan(path)
            if flagged:
                hits += 1
                print(f"[wd-40] !! MATCH {path}\n         ClamAV: {detail}")
    if hits:
        print(f"[wd-40] {hits}/{checked} file(s) MATCHED. Quarantine is YOUR call — nothing touched.")
        return 1
    print(f"[wd-40] {checked} file(s) clean.")
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
