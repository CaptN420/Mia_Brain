from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
ROOT_TOOLS_DIR = PROJECT_ROOT / "tools"
LEGACY_TOOLS_DIR = BASE_DIR / "tools"

def launch_py(path: Path, cwd: Path | None = None) -> None:
    if not path.exists():
        messagebox.showerror("Missing file", f"Tool not found:\n{path}")
        return
    try:
        subprocess.Popen([sys.executable, str(path)], cwd=str(cwd or path.parent))
    except Exception as exc:
        messagebox.showerror("Launch failed", str(exc))

GUI_TOOLS = [
    ("UI Analiser", ROOT_TOOLS_DIR / "ui_analiser.py"),
    ("Py Analiser", ROOT_TOOLS_DIR / "py_analiser.py"),
    ("Code Search UI", LEGACY_TOOLS_DIR / "code_search_ui.py"),
    ("Python Toolbox Desktop UI", LEGACY_TOOLS_DIR / "python_toolbox_desktop_ui.py"),
    ("Tkinter Skeleton Demo", LEGACY_TOOLS_DIR / "squelette.py"),
]

BROKEN_OR_CLI_INFO = [
    "py_debug_helper_ui_v3.py -> broken (main() missing)",
    "python_toolbox.py -> CLI only",
    "python_toolbox_v2.py -> CLI only",
    "python_toolbox_v3_ai_debugger.py -> CLI only",
    "logger.py -> utility / not normal GUI app",
]

class ToolHub(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AiCaptN - MIA Tool Hub")
        self.geometry("920x620")
        self.minsize(780, 520)

        wrap = ttk.Frame(self, padding=16)
        wrap.pack(fill="both", expand=True)

        ttk.Label(
            wrap,
            text="AiCaptN - Tool Hub",
            font=("TkDefaultFont", 14, "bold")
        ).pack(anchor="w")

        ttk.Label(
            wrap,
            text="Only launchers that make sense as desktop tools are shown as buttons.",
            justify="left"
        ).pack(anchor="w", pady=(6, 14))

        main_box = ttk.LabelFrame(wrap, text="Main", padding=12)
        main_box.pack(fill="x", pady=(0, 12))

        ttk.Button(
            main_box,
            text="Launch MIA UI",
            command=lambda: launch_py(BASE_DIR / "ui_launcher.py", BASE_DIR),
        ).pack(fill="x", pady=4)

        ttk.Button(
            main_box,
            text="Launch MIA CLI",
            command=lambda: launch_py(BASE_DIR / "launcher.py", BASE_DIR),
        ).pack(fill="x", pady=4)

        ttk.Button(
            main_box,
            text="Launch Root AiCaptN Launcher",
            command=lambda: launch_py(PROJECT_ROOT / "launcher.py", PROJECT_ROOT),
        ).pack(fill="x", pady=4)

        tools_box = ttk.LabelFrame(wrap, text="GUI Tools", padding=12)
        tools_box.pack(fill="both", expand=True, pady=(0, 12))

        grid = ttk.Frame(tools_box)
        grid.pack(fill="both", expand=True)

        row = 0
        col = 0
        for label, path in GUI_TOOLS:
            exists = path.exists()
            state = "normal" if exists else "disabled"
            text = label if exists else f"{label} [MISSING]"

            ttk.Button(
                grid,
                text=text,
                state=state,
                command=lambda p=path: launch_py(p, p.parent),
            ).grid(row=row, column=col, sticky="ew", padx=6, pady=6)

            col += 1
            if col > 1:
                col = 0
                row += 1

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        info_box = ttk.LabelFrame(wrap, text="Hidden / not launched directly", padding=12)
        info_box.pack(fill="both", expand=False)

        info = tk.Text(info_box, height=8, wrap="word")
        info.pack(fill="both", expand=True)
        info.insert("1.0", "\n".join(f"- {line}" for line in BROKEN_OR_CLI_INFO))
        info.configure(state="disabled")

if __name__ == "__main__":
    ToolHub().mainloop()
