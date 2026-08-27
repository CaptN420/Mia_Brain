#!/usr/bin/env python
"""quarantine.py - human-approved quarantine for confirmed-bad files.

Rule: NOTHING is quarantined without explicit human approval.
This tool only moves files a human has already confirmed as bad.

Usage:
    python quarantine.py move <file>     # move confirmed-bad file into quarantine/
    python quarantine.py list            # show quarantined items
    python quarantine.py release <name>  # restore (interactive y/N confirm)
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone

WD40 = os.path.dirname(os.path.abspath(__file__))
QUARANTINE_DIR = os.path.join(WD40, "quarantine")
LOG = os.path.join(QUARANTINE_DIR, "log.jsonl")


def _load_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _append(entry):
    os.makedirs(QUARANTINE_DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def cmd_move(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print(f"error: not a file: {path}")
        return 1
    name = os.path.basename(path)
    dest = os.path.join(QUARANTINE_DIR, name)
    if os.path.exists(dest):
        print(f"error: '{name}' already exists in quarantine")
        return 1
    os.makedirs(QUARANTINE_DIR, exist_ok=True)

    stat = os.stat(path)
    entry = {
        "name": name,
        "original_path": path,
        "moved_at": datetime.now(timezone.utc).isoformat(),
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }
    shutil.copy2(path, dest)  # copy2 preserves mtime; then remove original
    # verify before removing original
    if os.stat(dest).st_size != stat.st_size:
        print("error: copy verification failed, aborting")
        return 1
    os.remove(path)
    _append(entry)
    print(f"quarantined: {name} -> {dest}")
    return 0


def cmd_list():
    entries = _load_log()
    if not entries:
        print("quarantine is empty")
        return 0
    for e in entries:
        print(f"{e['name']}  moved={e['moved_at']}  from={e['original_path']}")
    return 0


def cmd_release(name):
    entries = [e for e in _load_log() if e["name"] == name]
    if not entries:
        print(f"error: '{name}' not found in quarantine log")
        return 1
    e = entries[-1]
    qpath = os.path.join(QUARANTINE_DIR, name)
    orig = e["original_path"]
    if not os.path.isfile(qpath):
        print(f"error: {qpath} missing")
        return 1
    if not os.path.isdir(os.path.dirname(orig)):
        print(f"error: original directory no longer exists: {os.path.dirname(orig)}")
        return 1
    if os.path.exists(orig):
        print(f"error: something already exists at original path: {orig}")
        return 1

    # Interactive confirmation — release ONLY after explicit 'y'
    print(f"Release quarantined file?")
    print(f"  quarantined: {qpath}")
    print(f"      back to: {orig}")
    try:
        answer = input("Confirm release? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer != "y":
        print("release aborted (nothing changed)")
        return 1

    shutil.copy2(qpath, orig)
    if os.stat(orig).st_size != e["size"]:
        print("error: copy verification failed, aborting")
        return 1
    os.remove(qpath)
    _append({
        "action": "released",
        "name": name,
        "restored_to": orig,
        "released_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"released: {name} -> {orig}")
    return 0


def main():
    argv = sys.argv[1:]
    usage = __doc__
    if not argv:
        print(usage)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "move" and len(rest) == 1:
        return cmd_move(rest[0])
    if cmd == "list" and not rest:
        return cmd_list()
    if cmd == "release" and len(rest) == 1:
        return cmd_release(rest[0])
    print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main())
