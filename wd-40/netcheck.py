"""wd-40 IOC checker — step 2: network reputation.

Cross-checks network activity against free abuse.ch feeds:
  - Feodo Tracker : botnet C2 server IPs
  - URLhaus       : malware distribution URLs / hostnames

Feeds are cached in wd-40/feeds/ and refreshed at most every 30 min.

Usage:
    python wd-40/netcheck.py                 # check current connections (from watchdog baseline)
    python wd-40/netcheck.py <ip> [ip ...]   # check specific IPs
    python wd-40/netcheck.py <domain>        # anything non-IP is treated as a hostname
"""
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FEEDS = os.path.join(HERE, "feeds")
BASELINE = os.path.join(HERE, "logs", "baseline.json")
os.makedirs(FEEDS, exist_ok=True)

FEED_TTL = 30 * 60  # seconds

FEODO_URL = "https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt"
URLHAUS_URL = "https://urlhaus.abuse.ch/downloads/text_recent/"


def fetch(url, dest, ttl=FEED_TTL):
    if os.path.exists(dest) and time.time() - os.path.getmtime(dest) < ttl:
        with open(dest) as f:
            return f.read()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wd-40-shield"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode("utf-8", errors="replace")
        with open(dest, "w") as f:
            f.write(data)
        return data
    except Exception as e:
        if os.path.exists(dest):
            with open(dest) as f:
                return f.read()  # stale cache better than nothing
        print(f"[wd-40] WARNING: could not update feed {url}: {e}")
        return ""


def load_feeds():
    feodo_raw = fetch(FEODO_URL, os.path.join(FEEDS, "feodo_ips.txt"))
    c2_ips = set(re.sub(r"^#", "", feodo_raw, flags=re.M).split())
    urlhaus_raw = fetch(URLHAUS_URL, os.path.join(FEEDS, "urlhaus_urls.txt"))
    bad_hosts = set()
    for line in urlhaus_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"https?://([^/:]+)", line)
        if m:
            bad_hosts.add(m.group(1).lower())
    return c2_ips, bad_hosts


def local_connections():
    """Remote endpoints from the watchdog baseline (if present)."""
    if not os.path.exists(BASELINE):
        return []
    with open(BASELINE) as f:
        procs = json.load(f)["procs"]
    out = set()
    for pid, info in procs.items():
        for proto, local, remote, state in info["conns"]:
            host = remote.rsplit(":", 1)[0].strip("[]")
            if state in ("LISTEN", "") or host in ("0.0.0.0", "*", "::"):
                continue
            out.add((host, pid, info["name"]))
    return sorted(out)


def is_private(host):
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    c2_ips, bad_hosts = load_feeds()
    print(f"[wd-40] feeds loaded: {len(c2_ips)} C2 IPs, {len(bad_hosts)} malware hosts")

    targets = []
    if args:
        targets = [(a, "-", "cli") for a in args]
    else:
        conns = local_connections()
        if not conns:
            print("[wd-40] no baseline yet — run wd-40/watchdog.py first, or pass an IP/host")
            return
        targets = [(h, pid, name) for h, pid, name in conns if not is_private(h)]

    hits = 0
    for host, pid, name in targets:
        verdict = None
        try:
            if ipaddress.ip_address(host.strip("[]")) and host in c2_ips:
                verdict = "C2 SERVER (Feodo Tracker)"
        except ValueError:
            if host.lower() in bad_hosts:
                verdict = "MALWARE HOST (URLhaus)"
        if verdict:
            hits += 1
            print(f"[wd-40] !! MATCH {host} ({name}, pid {pid}) -> {verdict}")
    if hits:
        print(f"[wd-40] {hits} suspicious endpoint(s). Ask before quarantine — nothing killed automatically.")
    else:
        print("[wd-40] all checked endpoints clean.")


if __name__ == "__main__":
    main()
