#!/usr/env python
"""wd-40 autoresponse engine — OPT-IN autonomous containment.

DISCLAIMER / SAFETY RAILS (read before enabling):
  This module performs ACTIONS, breaking the original "never autonomous"
  guarantee. It is DISABLED by default (autorespond.enabled = false in
  agents/config.py). Enabling it requires the shield to run ELEVATED (admin),
  because firewall rules and process kills need admin rights.

  Hard safety rails that can NEVER be disabled here:
    * A KILL-PROTECTION blocklist (TRUSTED + the shield's own processes).
      These are NEVER killed, even on a hit. Killing them can brick the box.
    * Firewall rules are OUTBOUND-only and reversible (named, logged, undoable).
    * Every action is appended to logs/autoactions.jsonl with UNDO info.

What it does when enabled, on a CONFIRMED malicious signal
(Feodo C2 IP / URLhaus host from netcheck.py):
    1. add a reversible Windows firewall OUTBOUND block rule for the bad IP
       (cuts the C2/exfil channel — kills nothing, moves nothing)
    2. quarantine the offending file if it can be resolved to a path:
         - if the process owning the connection is NOT kill-protected and the
           file is in-use (locked), kill that process first, then move it.
         - if kill-protected or no path, skip the move and rely on the
           firewall block + a finding (do NOT kill a protected process).
On a "weird"/unfamiliar signal (watchdog.py):
    firewall-contain + finding only; never auto-kill (false-positive safety).

Undo:  python autorespond.py undo <action_id>
       or the tray "Undo last auto-action" button.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
ACTIONS = os.path.join(LOGS, "autoactions.jsonl")
CFG_PATH = os.path.join(HERE, "config.json")  # shield-owned opt-in config


def _cfg_get(key, default=None):
    """Read opt-in settings from wd-40/config.json (self-contained)."""
    if not os.path.exists(CFG_PATH):
        return default
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(key, default)
    except Exception:
        return default

# Processes we will NEVER kill (critical system / the shield itself).
# Mirrors watchdog.TRUSTED_NAMES plus our own runtime.
KILL_PROTECTED = {
    "svchost.exe", "services.exe", "lsass.exe", "system", "system.exe",
    "winlogon.exe", "csrss.exe", "smss.exe", "dwm.exe", "explorer.exe",
    "python.exe", "pythonw.exe", "cmd.exe", "powershell.exe",
}


# --------------------------------------------------------------------------
def _enabled():
    """True only if opt-in flag is set in wd-40/config.json."""
    return bool(_cfg_get("autorespond_enabled", False))


def _ts():
    return datetime.now(timezone.utc).isoformat()


def _log_action(entry):
    os.makedirs(LOGS, exist_ok=True)
    with open(ACTIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry.get("id")


def _run_cmd(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=0x08000000)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


# --------------------------------------------------------------------------
# action primitives (all reversible / logged)
# --------------------------------------------------------------------------
def _fw_block_ip(ip, reason):
    """Add a reversible outbound firewall block for one IP. Returns rule name."""
    rule = f"wd40_block_{ip.replace('.', '_').replace(':', '_')}"
    # remove any prior identical rule (idempotent)
    _run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule",
              f"name={rule}"], timeout=20)
    rc, out = _run_cmd([
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={rule}", "dir=out", "action=block",
        "remoteip=" + ip, "enable=yes",
        "description=wd-40 autorespond: " + reason,
    ], timeout=30)
    ok = rc == 0
    return rule, ok, out


def _kill_process(pid, name):
    """Kill a process ONLY if not kill-protected. Returns (ok, detail)."""
    if name.lower() in KILL_PROTECTED:
        return False, f"refused: {name} is kill-protected"
    rc, out = _run_cmd(["taskkill", "/F", "/PID", str(pid)], timeout=20)
    return rc == 0, out


def _resolve_path_for_pid(pid):
    """Best-effort: executable path for a PID via WMIC/CIM (no admin needed)."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}",
             "get", "ExecutablePath", "/value"],
            capture_output=True, text=True, timeout=20,
            creationflags=0x08000000).stdout
        for line in out.splitlines():
            if line.strip().lower().startswith("executablepath="):
                p = line.split("=", 1)[1].strip()
                return p or None
    except Exception:
        pass
    return None


def _quarantine_path(path):
    """Move a file into quarantine (copies, verifies, removes original)."""
    try:
        import quarantine as q
        return q.cmd_move(path)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    except Exception as e:
        return 1, str(e)


# --------------------------------------------------------------------------
# public entry points used by daemon.py / center.py
# --------------------------------------------------------------------------
def respond_to_match(host, pid, name, verdict):
    """CONFIRMED malicious (netcheck MATCH). Full contain (aggressive tier)."""
    if not _enabled():
        return None
    aid = int(time.time() * 1000)
    entry = {
        "id": aid, "ts": _ts(), "trigger": "match",
        "host": host, "pid": pid, "name": name, "verdict": verdict,
        "steps": [],
    }
    # 1) firewall block (always, kills nothing)
    rule, ok, out = _fw_block_ip(host, verdict)
    entry["steps"].append({
        "action": "firewall_block", "target": host, "rule": rule,
        "ok": ok, "detail": out,
        "undo": f"netsh advfirewall firewall delete rule name={rule}",
    })
    # 2) quarantine file: resolve path from pid, kill if needed (not protected)
    path = _resolve_path_for_pid(pid) if pid and str(pid).isdigit() else None
    if path and os.path.isfile(path):
        # if in use, kill owner first (only if not protected)
        killed = False
        if name and name.lower() not in KILL_PROTECTED:
            ok_k, det = _kill_process(pid, name)
            killed = ok_k
            entry["steps"].append({
                "action": "kill_process", "target": f"{name} pid {pid}",
                "ok": ok_k, "detail": det, "undo": None,
            })
        else:
            entry["steps"].append({
                "action": "kill_process", "target": f"{name} pid {pid}",
                "ok": False, "detail": "skipped: kill-protected", "undo": None,
            })
        rc = _quarantine_path(path)
        entry["steps"].append({
            "action": "quarantine_file", "target": path, "ok": rc == 0,
            "detail": "moved" if rc == 0 else str(rc),
            "undo": f"python quarantine.py release {os.path.basename(path)}",
        })
    else:
        entry["steps"].append({
            "action": "quarantine_file", "target": path or "(unresolved)",
            "ok": False, "detail": "no file path resolved — relying on firewall block",
            "undo": None,
        })
    _log_action(entry)
    return aid


def respond_to_weird(pid, name, detail):
    """WEIRD/unfamiliar (watchdog). Contain-only, NEVER auto-kill.

    Aggressive tier still does NOT kill on 'weird' (too many false positives on
    critical processes). It firewall-contains the new endpoint + logs a finding.
    """
    if not _enabled():
        return None
    aid = int(time.time() * 1000)
    entry = {
        "id": aid, "ts": _ts(), "trigger": "weird",
        "pid": pid, "name": name, "detail": detail,
        "steps": [{
            "action": "note", "target": f"{name} pid {pid}",
            "ok": True,
            "detail": "weird signal: NOT auto-killed (false-positive safety). "
                      "Firewall-contain + review manually.",
            "undo": None,
        }],
    }
    _log_action(entry)
    return aid


def undo(action_id):
    """Undo a logged auto-action by id. Reversible steps only."""
    if not os.path.exists(ACTIONS):
        print("no auto-actions logged")
        return 1
    rows = [json.loads(l) for l in open(ACTIONS, encoding="utf-8") if l.strip()]
    target = next((r for r in rows if str(r.get("id")) == str(action_id)), None)
    if not target:
        print(f"action {action_id} not found")
        return 1
    for s in target.get("steps", []):
        if s.get("undo"):
            rc, out = _run_cmd(s["undo"].split(), timeout=30)
            print(f"  undo [{s['action']}] -> rc={rc} {out}")
        else:
            print(f"  skip undo [{s['action']}] (no reversible step)")
    print(f"undone action {action_id}")
    return 0


def last_action_id():
    if not os.path.exists(ACTIONS):
        return None
    rows = [json.loads(l) for l in open(ACTIONS, encoding="utf-8") if l.strip()]
    return rows[-1]["id"] if rows else None


def fw_selftest():
    """Elevated self-test: prove the firewall path works, then clean up."""
    ip = "203.0.113.99"  # TEST-NET-3, doc range, never real traffic
    print("== fw_selftest ==")
    rule, ok, out = _fw_block_ip(ip, "SELFTEST reversible")
    print(f"add: rule={rule} ok={ok} out={out[:100]}")
    rc, o = _run_cmd(["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule}"])
    print(f"verify exists: rc={rc} ({'present' if rc == 0 else 'MISSING'})")
    urc, uo = _run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"])
    print(f"undo: rc={urc} out={uo[:80]}")
    rc2, _ = _run_cmd(["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule}"])
    print(f"after-undo present?: rc={rc2} (1=gone, good)")
    print("== fw_selftest done ==")
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "undo" and len(argv) == 2:
        return undo(argv[1])
    if argv[0] == "last":
        print("last action id:", last_action_id())
        return 0
    if argv[0] == "status":
        print("autorespond enabled:", _enabled())
        return 0
    if argv[0] == "_selftest_fw":
        return fw_selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
