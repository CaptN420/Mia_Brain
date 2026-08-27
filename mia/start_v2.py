from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
UI_FILE = BASE_DIR / "ui_launcher.py"
TOOL_HUB_FILE = BASE_DIR / "tool_hub.py"
LAUNCHER_FILE = BASE_DIR / "launcher.py"


def _run(path: Path) -> int:
    return subprocess.run([sys.executable, "-u", str(path)], cwd=str(BASE_DIR), check=False).returncode


def main() -> int:
    if UI_FILE.exists():
        try:
            import wx  # noqa: F401
            return _run(UI_FILE)
        except Exception as exc:
            print(f"[INFO] wx UI indisponible: {type(exc).__name__}: {exc}")

    if TOOL_HUB_FILE.exists():
        print("[INFO] Fallback vers tool_hub.py")
        return _run(TOOL_HUB_FILE)

    if LAUNCHER_FILE.exists():
        print("[INFO] Fallback vers launcher.py")
        return _run(LAUNCHER_FILE)

    print("[ERREUR] Aucun point d'entrée trouvé (ui_launcher.py, tool_hub.py, launcher.py).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
