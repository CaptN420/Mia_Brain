#!/usr/bin/env python
"""wd-40 scanner daemon — the shield that runs forever in a loop.

A self-contained watchdog that repeatedly runs the full detection chain
on an interval and never exits on its own. Stays entirely inside wd-40/.

Chain per tick:
    watchdog (who is online?) -> netcheck (C2 / malware hosts?)
    filecheck (hash reputation, + ClamAV if present)
Every MATCH / BLOCKED / new-process event is appended to logs/findings.jsonl
with a UTC timestamp. NOTHING is quarantined or killed automatically.

Stop it:
    - press Ctrl+C, OR
    - write anything to wd-40/stop.flag  (e.g. touch wd-40/stop.flag)

Usage:
    python wd-40/daemon.py              # loop forever, default 300s interval
    python wd-40/daemon.py --interval 120
    python wd-40/daemon.py --once      # single pass, then exit
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
    # CREATE_NO_WINDOW keeps the per-tick subprocesses (watchdog/netcheck/
    # filecheck) from flashing a console window, regardless of how the daemon
    # was launched. Without it, a console python parent makes every tick pop a
    # shell. No-op on non-Windows.
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=WD40, env=env, timeout=280,
                           creationflags=flags)
    except subprocess.TimeoutExpired:
        return "[daemon] TIMEOUT in " + mod
    return (p.stdout + p.stderr).strip()


def _extract(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[wd-40] !!"):
            out.append(("match", s[len("[wd-40] !!]"):].strip()))
        elif s.startswith("[wd-40] BLOCKED"):
            out.append(("blocked", s[len("[wd-40] "):].strip()))
        elif "UNFAMILIAR" in s and s.startswith("NEW on network"):
            # watchdog flagged a new/unfamiliar process touching the network
            out.append(("weird", s))
    return out


def _auto_respond(finding_kind, detail):
    """Opt-in autonomous containment (no-op unless enabled in config.json)."""
    try:
        import autorespond as ar
    except Exception:
        return
    if finding_kind == "match":
        # detail looks like: "1.2.3.4 (evil.exe, pid 8848) -> C2 SERVER (Feodo)"
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
    # 1) watchdog: new processes on the network
    out = _run("watchdog.py")
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

    # Redirect stdout/stderr to a log file so the daemon survives when launched
    # detached (windowless / task scheduler / no console). Without this, the first
    # print after detach hits a broken pipe and the process dies on the next tick.
    _log = os.path.join(LOGS, "daemon.log")
    try:
        _lf = open(_log, "a", buffering=1, encoding="utf-8")
        sys.stdout = _lf
        sys.stderr = _lf
    except OSError:
        pass  # best-effort; if it fails we just keep the console

    _notify_ps = os.path.join(WD40, "_notify.ps1")

    def _toast(title, body, level="info"):
        """Show a Windows toast (no window). Silent if PowerShell/API missing."""
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", _notify_ps, title, body, level],
                creationflags=0x08000000 if os.name == "nt" else 0,
                timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    if os.path.exists(STOP):
        os.remove(STOP)

    # Single-instance guard: refuse to start if another live shield owns the
    # lock. Prevents the duplicate-scanner stacking we hit when start_shield.bat
    # (or a manual launch) runs while one is already up.
    other = _other_shield_pid()
    if other is not None:
        print(f"[daemon] refused to start: another shield already running "
              f"(PID {other}) — exiting {_ts()}", flush=True)
        return 1
    with open(LOCK, "w", encoding="utf-8") as _lf2:
        _lf2.write(str(os.getpid()))

    print(f"[daemon] wd-40 scanner {'single pass' if once else 'loop'} "
          f"start {_ts()} interval={interval}s", flush=True)
    if not once:
        _toast("wd-40 Shield", f"Active — scanning every {interval}s", "info")
    try:
        while True:
            if os.path.exists(STOP):
                # NOTE: do NOT delete STOP here. Every concurrent instance polls
                # it; deleting on first sight lets others miss the signal (the
                # bug that left a duplicate running). It is cleared at startup.
                print(f"[daemon] stop flag seen — exiting {_ts()}", flush=True)
                break
            t0 = time.time()
            try:
                found = tick()
            except Exception as e:  # never let the loop die
                print(f"[daemon] tick error: {e}", flush=True)
                found = []
            if found:
                with open(FINDINGS, "a", encoding="utf-8") as f:
                    for kind, detail in found:
                        f.write(json.dumps({"ts": _ts(), "type": kind, "detail": detail}) + "\n")
                for kind, detail in found:
                    print(f"[daemon] FINDING [{kind}] {detail}", flush=True)
                    # opt-in autonomous containment (no-op unless enabled)
                    _auto_respond(kind, detail)
                _toast("wd-40 Shield ALERT", f"{len(found)} finding(s): {found[0][1][:80]}", "warning")
            else:
                print(f"[daemon] tick clean @ {_ts()}", flush=True)
            if once:
                break
            # sleep but wake if stop.flag appears
            slept = 0
            while slept < interval:
                if os.path.exists(STOP):
                    break
                time.sleep(min(5, interval - slept))
                slept += 5
    finally:
        # release the single-instance lock on any exit path
        try:
            os.remove(LOCK)
        except OSError:
            pass
    return 0


import json as json  # noqa: E402  (placed late to keep _run fast)

if __name__ == "__main__":
    sys.exit(main())
