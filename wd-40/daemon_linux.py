#!/usr/bin/env python3
"""wd-40 scanner daemon (Linux) — the shield that runs forever in a loop.

A self-contained watchdog that repeatedly runs the full detection chain
on an interval and never exits on its own. Stays entirely inside wd-40/.

Chain per tick:
    watchdog_linux (who is online?) -> netcheck (C2 / malware hosts?)
    filecheck (hash reputation, + ClamAV if present)
Every MATCH / BLOCKED / new-process event is appended to logs/findings.jsonl
with a UTC timestamp. NOTHING is quarantined or killed automatically.

Stop it:
    - press Ctrl+C, OR
    - write anything to wd-40/stop.flag  (e.g. touch wd-40/stop.flag)

Usage:
    python wd-40/daemon_linux.py              # loop forever, default 300s interval
    python wd-40/daemon_linux.py --interval 120
    python wd-40/daemon_linux.py --once      # single pass, then exit
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

WD40 = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(WD40, "logs")
FINDINGS = os.path.join(LOGS, "findings.jsonl")
STOP = os.path.join(WD40, "stop.flag")
LOCK = os.path.join(WD40, ".shield.pid")  # single-instance guard
os.makedirs(LOGS, exist_ok=True)


def _other_shield_pid():
    """Return PID of a live wd-40 daemon if one is already running, else None.

    Uses a PID lockfile. The lock is only considered live if the recorded PID
    still exists (os.kill(pid, 0) is an existence probe that sends no signal);
    a stale lock from a crashed previous run is safely ignored.
    """
    if not os.path.exists(LOCK):
        return None
    try:
        with open(LOCK, encoding="utf-8") as _f:
            pid = int(_f.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None  # stale lock
    return pid


def _ts():
    return datetime.now(timezone.utc).isoformat()


def _run(mod, *args):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, os.path.join(WD40, mod), *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=WD40, env=env, timeout=280)
    except subprocess.TimeoutExpired:
        return "[daemon] TIMEOUT in " + mod
    return (p.stdout + p.stderr).strip()


def _extract(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[wd-40] !!"):
            out.append(("match", s[len("[wd-40] !!"):].strip()))
        elif s.startswith("[wd-40] BLOCKED"):
            out.append(("blocked", s[len("[wd-40] "):].strip()))
        elif "UNFAMILIAR" in s and s.startswith("NEW on network"):
            # watchdog flagged a new/unfamiliar process touching the network
            out.append(("weird", s))
    return out


def _auto_respond(finding_kind, detail):
    """Opt-in autonomous containment (no-op unless enabled in config.json)."""
    try:
        import autorespond_linux as ar
    except Exception:
        return
    if finding_kind == "match":
        # detail looks like: "1.2.3.4 (evil, pid 8848) -> C2 SERVER (Feodo)"
        import re as _re
        m = _re.search(r"([0-9a-fA-F:.]+)\s*\(([^,]+),\s*pid\s*(\d+)\)", detail)
        host = m.group(1) if m else detail.split()[0]
        name = m.group(2).strip() if m else "?"
        pid = m.group(3) if m else None
        ar.respond_to_match(host, int(pid) if pid else None, name, detail)
    elif finding_kind == "weird":
        m = _re_search_pid(detail)
        ar.respond_to_weird(m["pid"], m["name"], detail)


def _re_search_pid(detail):
    import re as _re
    m = _re.search(r"\[(\d+)\]\s*([^\s<]+)", detail)
    return {"pid": int(m.group(1)) if m else None,
            "name": m.group(2) if m else "?"}


def tick():
    findings = []
    # 1) watchdog_linux: new processes on the network
    out = _run("watchdog_linux.py")
    wd = _extract(out)
    findings += wd
    # 2) netcheck: are live endpoints malicious
    nc = _extract(_run("netcheck.py"))
    findings += nc
    # 3) filecheck: sandbox + any dropped files
    findings += _extract(_run("filecheck.py", "--dir", os.path.join(WD40, "sandbox")))
    return findings


def main():
    args = sys.argv[1:]
    once = "--once" in args
    interval = 300
    if "--interval" in args:
        try:
            interval = int(args[args.index("--interval") + 1])
        except (ValueError, IndexError):
            pass

    # Check for existing instance
    other = _other_shield_pid()
    if other:
        print(f"[wd-40] another shield daemon is running (pid {other}). Exiting.")
        return 1

    # Write our PID
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))

    # Redirect stdout/stderr to a log file so the daemon survives when launched
    # detached (windowless / systemd / cron / no console). Without this, the first
    # print after detach hits a broken pipe and kills the process.
    log_path = os.path.join(LOGS, "daemon.out")
    sys.stdout = open(log_path, "a", buffering=1, encoding="utf-8")
    sys.stderr = sys.stdout

    print(f"\n[wd-40] daemon starting at {_ts()} (interval={interval}s, once={once})")
    sys.stdout.flush()

    try:
        while True:
            if os.path.exists(STOP):
                print(f"[wd-40] stop.flag found — shutting down at {_ts()}")
                break

            tick_findings = tick()
            if tick_findings:
                with open(FINDINGS, "a", encoding="utf-8") as f:
                    for kind, detail in tick_findings:
                        f.write(json.dumps({
                            "ts": _ts(), "kind": kind, "detail": detail
                        }) + "\n")
                # Opt-in autorespond
                for kind, detail in tick_findings:
                    _auto_respond(kind, detail)

            if once:
                print(f"[wd-40] single pass done at {_ts()}")
                break

            time.sleep(interval)
    finally:
        # Clean up lock
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    import json
    main()