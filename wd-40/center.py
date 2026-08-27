#!/usr/bin/env python
"""wd-40 Security Center — the shield's command hub.

Unifies every tool in wd-40/ into one workflow:

    python center.py status            # shield status, feed freshness, last findings
    python center.py scan [--dir PATH] # run watchdog -> netcheck -> (filecheck)
                                       # appends every MATCH/BLOCKED event to log
    python center.py report [N]        # print last N findings (default 20)
    python center.py feed-age          # show how fresh each abuse.ch feed is

Flow (the SYSTEM):
    watchdog (who is online?)  --+--> netcheck (are they C2/malware hosts?)
    filecheck (hash reputation) --+--> findings.jsonl (timestamped evidence)
                                                  |
                                                  v
                                      quarantine.py move <f>   (YOUR approval)
                                      quarantine.py release <f> (YOUR approval)

Design rules (from the crew brief):
  * Nothing is deleted, killed, or quarantined automatically.
  * Every detection is an append-only finding with a UTC timestamp.
  * All state stays under wd-40/ (except the sys temp dir, which feeds may use).
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

WD40 = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(WD40, "logs")
FINDINGS = os.path.join(LOGS, "findings.jsonl")
BASELINE = os.path.join(LOGS, "baseline.json")
FEEDS = os.path.join(WD40, "feeds")
os.makedirs(LOGS, exist_ok=True)


def _ts():
    return datetime.now(timezone.utc).isoformat()


def _log(findings):
    """Append detection events to findings.jsonl (chronological)."""
    if not findings:
        return
    with open(FINDINGS, "a", encoding="utf-8") as f:
        for ev in findings:
            ev.setdefault("ts", _ts())
            f.write(json.dumps(ev) + "\n")


def _run(module, *args):
    """Run a sibling tool, return (rc, stdout). PYTHONUTF8 for cp1252 safety."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, os.path.join(WD40, module), *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=WD40, env=env, timeout=300)
    except subprocess.TimeoutExpired:
        return (-1, "[center] timed out")
    return p.returncode, (p.stdout + p.stderr).strip()


def _parse_findings(text):
    """Extract MATCH/BLOCKED lines and structure them as findings."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[wd-40] !!"):
            out.append({"type": "match", "detail": s[len("[wd-40] !!"):].strip()})
        elif s.startswith("[wd-40] BLOCKED"):
            out.append({"type": "blocked", "detail": s[len("[wd-40] "):].strip()})
    return out


def cmd_scan(extra_args):
    print("[center] === wd-40 scan started ===")
    findings = []

    # 1) watchdog: who just came online / new endpoints
    rc, out = _run("watchdog.py")
    if rc != 0:
        print(out)
    findings += _parse_findings(out)

    # 2) netcheck: are current remote endpoints C2 / malware hosts
    rc, out = _run("netcheck.py")
    print(out)
    findings += _parse_findings(out)

    # 3) optional file hash check (default: the sandbox folder)
    if "--dir" in extra_args:
        d = extra_args[extra_args.index("--dir") + 1]
        rc, out = _run("filecheck.py", "--dir", d)
    else:
        rc, out = _run("filecheck.py", "--dir", os.path.join(WD40, "sandbox"))
    print(out)
    findings += _parse_findings(out)

    _log(findings)
    print(f"[center] === scan done: {len(findings)} finding(s) logged ===")
    print("[center] review with:  python center.py report")
    print("[center] quarantine (YOUR call):  python quarantine.py move <file>")
    return 0 if not findings else 1


def _findings_all():
    if not os.path.exists(FINDINGS):
        return []
    with open(FINDINGS, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def cmd_report(n=20):
    rows = _findings_all()
    if not rows:
        print("[center] no findings yet.")
        return 0
    print(f"[center] last {min(n, len(rows))} of {len(rows)} finding(s):")
    for ev in rows[-n:]:
        print(f"  {ev.get('ts','?')}  [{ev.get('type','?')}]  {ev.get('detail','')}")
    return 0


def cmd_status():
    print("[center] === wd-40 Security Center status ===")
    # shield on?
    print(f"  shield runner : wd-40/run.py")
    # baseline
    if os.path.exists(BASELINE):
        age = time.time() - os.path.getmtime(BASELINE)
        print(f"  watchdog base : {int(age)}s old")
    else:
        print("  watchdog base : (none — run a scan)")
    # feed ages
    feeds = {
        "feodo_ips.txt": "Feodo C2 IPs",
        "urlhaus_urls.txt": "URLhaus hosts",
        "urlhaus_sha256.txt": "MalwareBazaar hashes",
    }
    print("  feeds:")
    for fn, label in feeds.items():
        p = os.path.join(FEEDS, fn)
        if os.path.exists(p):
            age = int(time.time() - os.path.getmtime(p))
            print(f"    - {label}: {age}s old ({os.path.getsize(p)} bytes)")
        else:
            print(f"    - {label}: (not fetched yet)")
    # quarantine
    qlog = os.path.join(WD40, "quarantine", "log.jsonl")
    qcount = 0
    if os.path.exists(qlog):
        with open(qlog) as f:
            qcount = sum(1 for l in f if l.strip())
    print(f"  quarantine    : {qcount} entry(ies)")
    n = len(_findings_all())
    print(f"  findings log  : {n} total")
    return 0


def cmd_feed_age():
    for fn in sorted(os.listdir(FEEDS)):
        p = os.path.join(FEEDS, fn)
        if os.path.isfile(p):
            age = int(time.time() - os.path.getmtime(p))
            print(f"  {fn}: {age}s old, {os.path.getsize(p)} bytes")
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        return cmd_status()
    if cmd == "scan":
        return cmd_scan(rest)
    if cmd == "report":
        n = int(rest[0]) if rest and rest[0].isdigit() else 20
        return cmd_report(n)
    if cmd == "feed-age":
        return cmd_feed_age()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
