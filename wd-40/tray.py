#!/usr/env python
"""wd-40 tray controller — a view-only system-tray UI for the shield.

Run:  pythonw wd-40/tray.py        (windowless; icon lives in the taskbar)

Right-click menu:
    Shield: ACTIVE/DOWN ...   (live status, read-only label)
    Run scan now              (runs center.py scan, shows result)
    Findings (last 20)        (center.py report 20)
    Feed freshness            (center.py feed-age)
    Live daemon log           (tails logs/daemon.log)
    Status details            (center.py status + shield state)
    About
    Quit tray (shield keeps running)

Design rules (per the crew brief):
  * VIEW-ONLY. This app NEVER starts or stops the daemon. Start/stop the
    shield with start_shield.bat / wd-40\\stop.flag. The shield's own
    single-instance lock (.shield.pid) is read here ONLY to display status.
  * Same runtime as the daemon: uses sys.executable so it runs under the
    Local Python that launched the shield, keeping one dependency set.
  * No network port, no elevated actions. Everything is local to this host.
"""
import os
import sys
import time
import json
import threading
import subprocess

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception as e:  # hard deps for the tray
    sys.stderr.write(f"[tray] missing deps (pip install pystray pillow): {e}\n")
    sys.exit(2)

import tkinter as tk
from tkinter import scrolledtext

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, ".shield.pid")          # daemon single-instance lock
CENTER = os.path.join(HERE, "center.py")
DAEMON_LOG = os.path.join(HERE, "logs", "daemon.log")
FINDINGS = os.path.join(HERE, "logs", "findings.jsonl")

ABOUT = (
    "wd-40 shield — tray controller\n\n"
    "This is a VIEW-ONLY control panel. It does not start or stop the\n"
    "shield daemon; use start_shield.bat / wd-40\\stop.flag for that.\n\n"
    "It reads the daemon's .shield.pid lock to show live status and\n"
    "drives center.py (status / scan / report / feed-age).\n\n"
    "Network detection is a checker, not a sniffer: it reads THIS machine's\n"
    "own socket table (netstat) and compares endpoints to abuse.ch blocklists.\n"
    "No packet capture, no promiscuous mode.\n"
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _run(*args, timeout=320):
    """Run a sibling tool via center.py; return (rc, text). UTF-8 safe."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    try:
        p = subprocess.run([sys.executable, CENTER, *args],
                           capture_output=True, text=True,
                           cwd=HERE, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "[tray] command timed out"
    except Exception as e:
        return -1, f"[tray] error: {e}"
    return p.returncode, (p.stdout + p.stderr).strip()


def shield_state():
    """Return ('ACTIVE'|'DOWN', detail) from the daemon's PID lock."""
    if not os.path.exists(LOCK):
        return "DOWN", "not started (no .shield.pid)"
    try:
        pid = int(open(LOCK, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return "DOWN", "stale lock file"
    try:
        os.kill(pid, 0)            # existence probe, sends no signal
        return "ACTIVE", f"PID {pid}"
    except OSError:
        return "DOWN", f"lock PID {pid} not running (stale)"


def make_icon(active):
    """Draw a small shield icon (green=active, grey=down)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (46, 204, 113, 255) if active else (149, 165, 166, 255)
    pts = [(32, 6), (54, 16), (54, 38), (32, 58), (10, 38), (10, 16)]
    d.polygon(pts, fill=color)
    d.ellipse((26, 26, 38, 38), fill=(255, 255, 255, 255))
    return img


# --------------------------------------------------------------------------
# tkinter side-channel (separate thread; all UI marshalled via .after)
# --------------------------------------------------------------------------
_tk_root = None
_tk_ready = threading.Event()


def _tk_thread():
    global _tk_root
    root = tk.Tk()
    root.withdraw()               # hidden root; we only use Toplevel windows
    _tk_root = root
    _tk_ready.set()
    root.mainloop()


def _popup(title, text):
    def _build():
        w = tk.Toplevel(_tk_root)
        w.title(title)
        w.geometry("760x500")
        txt = scrolledtext.ScrolledText(w, wrap="word")
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        tk.Button(w, text="Close", command=w.destroy).pack(pady=4)
        w.focus_set()
    _tk_root.after(0, _build)


def _live_log():
    def _build():
        w = tk.Toplevel(_tk_root)
        w.title("wd-40 — live daemon log")
        w.geometry("860x540")
        txt = scrolledtext.ScrolledText(w, wrap="none")
        txt.pack(fill="both", expand=True)
        stop = {"go": True}

        def closer():
            stop["go"] = False
            w.destroy()
        tk.Button(w, text="Close", command=closer).pack(pady=4)

        def tail():
            size = 0
            try:
                if os.path.exists(DAEMON_LOG):
                    with open(DAEMON_LOG, "r", encoding="utf-8",
                              errors="replace") as f:
                        lines = f.read().splitlines()
                    txt.insert("1.0", "\n".join(lines[-200:]) + "\n")
                    txt.see("end")
                    size = os.path.getsize(DAEMON_LOG)
                while stop["go"]:
                    time.sleep(1)
                    if not os.path.exists(DAEMON_LOG):
                        continue
                    cur = os.path.getsize(DAEMON_LOG)
                    if cur < size:           # log was rotated/truncated
                        size = 0
                    if cur > size:
                        with open(DAEMON_LOG, "r", encoding="utf-8",
                                  errors="replace") as f:
                            f.seek(size)
                            chunk = f.read()
                        txt.insert("end", chunk)
                        txt.see("end")
                        size = cur
            except Exception:
                pass
        threading.Thread(target=tail, daemon=True).start()
    _tk_root.after(0, _build)


# --------------------------------------------------------------------------
# menu actions
# --------------------------------------------------------------------------
def act_scan(icon, item):
    box = {}

    def _build():
        w = tk.Toplevel(_tk_root)
        w.title("wd-40 Scan")
        w.geometry("760x500")
        t = scrolledtext.ScrolledText(w, wrap="word")
        t.insert("1.0", "Running scan… (watchdog → netcheck → filecheck)\n")
        t.pack(fill="both", expand=True)
        tk.Button(w, text="Close", command=w.destroy).pack(pady=4)
        box["t"] = t
    _tk_root.after(0, _build)

    def worker():
        rc, out = _run("scan", timeout=320)

        def upd():
            if "t" in box:
                box["t"].insert("end", "\n\n" + (out or "(no output)") +
                                f"\n\n[exit code {rc}]")
                box["t"].see("end")
        _tk_root.after(0, upd)
    threading.Thread(target=worker, daemon=True).start()


def act_findings(icon, item):
    rc, out = _run("report", "20")
    _popup("wd-40 Findings", out or "(no findings yet)")


def act_feed(icon, item):
    rc, out = _run("feed-age")
    _popup("wd-40 Feed freshness", out or "(no feeds fetched yet)")


def act_live(icon, item):
    _live_log()


def act_status(icon, item):
    state, detail = shield_state()
    rc, out = _run("status")
    _popup("wd-40 Status", f"Shield: {state} ({detail})\n\n" + out)


def act_about(icon, item):
    _popup("About wd-40 tray", ABOUT)


def act_undo(icon, item):
    def worker():
        try:
            import autorespond as ar
        except Exception as e:
            _popup("Undo failed", f"could not load autorespond: {e}")
            return
        aid = ar.last_action_id()
        if aid is None:
            _popup("Undo", "no auto-actions recorded")
            return
        rc = ar.undo(aid)
        _popup("Undo auto-action", f"action {aid} undone (rc={rc}).\n"
                "Firewall rule removed; quarantined file(s) restored via "
                "quarantine.py release if applicable.")
    threading.Thread(target=worker, daemon=True).start()


def act_quit(icon, item):
    # stops ONLY the tray; the shield daemon is untouched
    icon.stop()


def build_menu(icon):
    state, detail = shield_state()
    status_label = f"Shield: {state} ({detail})"
    try:
        import autorespond as ar
        armed = "ARMED" if ar._enabled() else "OFF (report-only)"
    except Exception:
        armed = "?"
    auto_label = f"Auto-respond: {armed}"
    return pystray.Menu(
        pystray.MenuItem(status_label, None, enabled=False),
        pystray.MenuItem(auto_label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Run scan now", act_scan),
        pystray.MenuItem("Findings (last 20)", act_findings),
        pystray.MenuItem("Feed freshness", act_feed),
        pystray.MenuItem("Live daemon log", act_live),
        pystray.MenuItem("Status details", act_status),
        pystray.MenuItem("Undo last auto-action", act_undo),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("About", act_about),
        pystray.MenuItem("Quit tray (shield keeps running)", act_quit),
    )


def _refresher(icon):
    while True:
        time.sleep(10)
        try:
            icon.icon = make_icon(shield_state()[0] == "ACTIVE")
            icon.menu = build_menu(icon)   # rebuild concrete menu w/ fresh status
            icon.update_menu()
        except Exception:
            pass


def main():
    threading.Thread(target=_tk_thread, daemon=True).start()
    _tk_ready.wait(timeout=10)
    icon = pystray.Icon("wd40-shield", make_icon(False), "wd-40 Shield")
    icon.menu = build_menu(icon)
    threading.Thread(target=_refresher, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
