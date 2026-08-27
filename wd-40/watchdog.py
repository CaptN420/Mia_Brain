"""wd-40 watchdog — watches which programs are using the wifi/network.

Each run:
  1. snapshots every process with open TCP/UDP endpoints (netstat)
  2. compares against the last snapshot (wd-40/logs/baseline.json)
  3. prints ONLY what is new or changed (stable output = silent when quiet)
     - new processes touching the network
     - processes that disappeared
     - new remote endpoints an existing process started talking to
  4. saves the new snapshot as baseline

Run manually:
    python wd-40/watchdog.py            # one check, diff vs baseline
    python wd-40/watchdog.py --reset    # adopt current state as clean baseline

Quarantine: nothing is killed automatically. When something looks weird,
it gets reported here and WE decide together — then it goes to
wd-40/quarantine (see quarantine.py).
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
QUARANTINE = os.path.join(HERE, "quarantine")
BASELINE = os.path.join(LOGS, "baseline.json")
os.makedirs(LOGS, exist_ok=True)
os.makedirs(QUARANTINE, exist_ok=True)

# Local/private ranges we never flag as "remote"
PRIVATE = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|\[::1\]|0\.0\.0\.0|\*:)")

# Well-known system services allowed to talk out without being flagged
TRUSTED_NAMES = ("svchost.exe", "services.exe", "lsass.exe", "system",
                 "winlogon.exe", "csrss.exe", "smss.exe", "dwm.exe")


def _pid_names():
    """PID -> process name via tasklist (no admin needed)."""
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return {}
    names = {}
    import csv as _csv
    for row in _csv.reader(out.splitlines()):
        if len(row) >= 2:
            names[row[1]] = row[0]
    return names


def netstat_rows():
    """Parse `netstat -ano` output; resolve names via tasklist."""
    out = subprocess.run(["netstat", "-ano"], capture_output=True,
                         text=True, timeout=30).stdout
    names = _pid_names()
    procs = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() not in ("TCP", "UDP"):
            continue
        proto, local, remote = parts[0].upper(), parts[1], parts[2]
        state = parts[3] if proto == "TCP" else ""
        pid = parts[-1]
        if not pid.isdigit():
            continue
        procs.setdefault(pid, {"name": names.get(pid, "?"), "conns": set()})
        procs[pid]["conns"].add((proto, local, remote, state))
    return {p: {"name": v["name"], "conns": sorted(v["conns"])}
            for p, v in procs.items()}


def remotes(conns):
    r = set()
    for proto, local, remote, state in conns:
        host = remote.rsplit(":", 1)[0]
        if PRIVATE.match(remote):
            continue
        r.add(f"{host}")
    return r


def main():
    reset = "--reset" in sys.argv
    snap = netstat_rows()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = []
    if reset or not os.path.exists(BASELINE):
        with open(BASELINE, "w") as f:
            json.dump({"ts": now, "procs": snap}, f, indent=1)
        print(f"[wd-40] baseline set ({len(snap)} procs with sockets)"
              if reset else f"[wd-40] first run: baseline set ({len(snap)} procs)")
        return

    with open(BASELINE) as f:
        old = json.load(f)["procs"]

    old_pids, new_pids = set(old), set(snap)
    for pid in sorted(new_pids - old_pids, key=int):
        name = snap[pid]["name"]
        trusted = any(t in name.lower() for t in TRUSTED_NAMES)
        tag = "" if trusted else "  <-- UNFAMILIAR"
        lines.append(f"NEW on network: [{pid}] {name}{tag}")
        for r in sorted(remotes(snap[pid]["conns"])):
            lines.append(f"    -> {r}")
    for pid in sorted(old_pids - new_pids, key=int):
        lines.append(f"GONE from network: [{pid}] {old[pid]['name']}")
    for pid in sorted(old_pids & new_pids):
        before = remotes(old[pid]["conns"])
        after = remotes(snap[pid]["conns"])
        fresh = after - before
        if fresh:
            lines.append(f"NEW endpoints: [{pid}] {snap[pid]['name']}")
            for r in sorted(fresh):
                lines.append(f"    -> {r}")

    with open(BASELINE, "w") as f:
        json.dump({"ts": now, "procs": snap}, f, indent=1)

    if lines:
        print(f"[wd-40] network changes at {now}")
        print("\n".join(lines))
    # silent when nothing changed


if __name__ == "__main__":
    main()
