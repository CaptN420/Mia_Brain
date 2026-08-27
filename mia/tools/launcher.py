import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MENU = {
    "MIA": {
        "mia_start": ROOT / "apps" / "mia" / "start_v2.py",
        "mia_launcher": ROOT / "apps" / "mia" / "launcher.py",
        "mia_ui": ROOT / "apps" / "mia" / "ui_launcher.py",
        "mia_tool_hub": ROOT / "apps" / "mia" / "tool_hub.py",
    },
    "Music": {
        "music_launcher": ROOT / "apps" / "music" / "launcher_music.py",
        "music_main": ROOT / "apps" / "music" / "main.py",
    },
    "Tools": {
        "ui_analiser": ROOT / "tools" / "ui_analiser.py",
        "py_analiser": ROOT / "tools" / "py_analiser.py",
    },
    "Utils": {
        "blue_light_toggle": ROOT / "apps" / "utils" / "blue_light_toggle.sh",
        "youtube_extractor": ROOT / "apps" / "utils" / "youtube_extractor",
    },
}

def run_item(path: Path):
    if not path.exists():
        print(f"[ERROR] Missing: {path}")
        return

    if path.is_dir():
        print(path)
        return

    try:
        if path.suffix.lower() == ".py":
            subprocess.Popen([sys.executable, str(path)], cwd=str(path.parent))
        elif path.suffix.lower() == ".sh":
            subprocess.Popen(["bash", str(path)], cwd=str(path.parent))
        elif os.access(path, os.X_OK):
            subprocess.Popen([str(path)], cwd=str(path.parent))
        else:
            print(path)
    except Exception as e:
        print(f"[ERROR] {e}")

def submenu(title: str, items: dict):
    keys = list(items.keys())

    while True:
        print(f"\n=== {title} ===")
        for i, key in enumerate(keys, start=1):
            status = "OK" if items[key].exists() else "MISSING"
            print(f"{i}. {key} [{status}]")
        print("0. back")

        choice = input("Select: ").strip()

        if choice == "0":
            return
        if not choice.isdigit():
            print("[ERROR] Enter a number")
            continue

        idx = int(choice) - 1
        if idx < 0 or idx >= len(keys):
            print("[ERROR] Invalid choice")
            continue

        key = keys[idx]
        print(f"[RUN] {key}")
        run_item(items[key])

def main():
    groups = list(MENU.keys())

    while True:
        print("\n=== AiCaptN Launcher ===")
        for i, group in enumerate(groups, start=1):
            print(f"{i}. {group}")
        print("0. quit")

        choice = input("Select: ").strip()

        if choice == "0":
            break
        if not choice.isdigit():
            print("[ERROR] Enter a number")
            continue

        idx = int(choice) - 1
        if idx < 0 or idx >= len(groups):
            print("[ERROR] Invalid choice")
            continue

        group = groups[idx]
        submenu(group, MENU[group])

if __name__ == "__main__":
    main()
