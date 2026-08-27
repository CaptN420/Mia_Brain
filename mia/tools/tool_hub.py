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


def launch_tool(path: Path, cwd: Path | None = None) -> None:
    if not path.exists():
        messagebox.showerror("Missing file", f"Tool not found\n{path}")
        return
    try:
        subprocess.Popen([sys.executable, str(path)], cwd=str(cwd or path.parent))
    except Exception as exc:
        messagebox.showerror("Launch failed", str(exc))


def launch_shell(path: Path, cwd: Path | None = None) -> None:
    if not path.exists():
        messagebox.showerror("Missing file", f"Script not found\n{path}")
        return
    try:
        subprocess.Popen(["bash", str(path)], cwd=str(cwd or path.parent))
    except Exception as exc:
        messagebox.showerror("Launch failed", str(exc))


TOOLS = [
    ("UI Analiser", ROOT_TOOLS_DIR / "ui_analiser.py"),
    ("Py Analiser", ROOT_TOOLS_DIR / "py_analiser.py"),
    ("Code Search UI", LEGACY_TOOLS_DIR / "code_search_ui.py"),
    ("Py Debug Helper UI", LEGACY_TOOLS_DIR / "py_debug_helper_ui_v3.py"),
    ("Python Toolbox CLI V1", LEGACY_TOOLS_DIR / "python_toolbox.py"),
    ("Python Toolbox CLI V2", LEGACY_TOOLS_DIR / "python_toolbox_v2.py"),
    ("Python Toolbox AI Debugger V3", LEGACY_TOOLS_DIR / "python_toolbox_v3_ai_debugger.py"),
    ("Python Toolbox Desktop UI", LEGACY_TOOLS_DIR / "python_toolbox_desktop_ui.py"),
    ("System Scan Logger", LEGACY_TOOLS_DIR / "logger.py"),
    ("Tkinter Skeleton Demo", LEGACY_TOOLS_DIR / "squelette.py"),
]


class ToolHub(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AiCaptN - MIA Tool Hub")
        self.geometry("860x560")
        self.minsize(760, 500)

        wrap = ttk.Frame(self, padding=16)
        wrap.pack(fill="both", expand=True)

        title = ttk.Label(wrap, text="AiCaptN - Integrated Tools", font=("TkDefaultFont", 14, "bold"))
        title.pack(anchor="w")

        desc = ttk.Label(
            wrap,
            text="Launch MIA, the analyzers, and helper tools from one clean hub.",
            justify="left",
        )
        desc.pack(anchor="w", pady=(6, 14))

        main_actions = ttk.LabelFrame(wrap, text="Main Actions", padding=12)
        main_actions.pack(fill="x", pady=(0, 12))

        ttk.Button(
            main_actions,
            text="Launch MIA UI",
            command=lambda: launch_tool(BASE_DIR / "ui_launcher.py", BASE_DIR),
        ).pack(fill="x", pady=4)

        ttk.Button(
            main_actions,
            text="Launch MIA Engine CLI",
            command=lambda: launch_tool(BASE_DIR / "launcher.py", BASE_DIR),
        ).pack(fill="x", pady=4)

        ttk.Button(
            main_actions,
            text="Launch Root AiCaptN Launcher",
            command=lambda: launch_tool(PROJECT_ROOT / "launcher.py", PROJECT_ROOT),
        ).pack(fill="x", pady=4)

        grid = ttk.LabelFrame(wrap, text="Tools", padding=12)
        grid.pack(fill="both", expand=True)

        row = 0
        col = 0
        for label, path in TOOLS:
            exists = path.exists()
            button_text = f"{label}{'' if exists else ' [MISSING]'}"
            state = "normal" if exists else "disabled"

            ttk.Button(
                grid,
                text=button_text,
                state=state,
                command=lambda p=path: launch_tool(p, p.parent),
            ).grid(row=row, column=col, sticky="ew", padx=6, pady=6)

            col += 1
            if col > 1:
                col = 0
                row += 1

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        footer = ttk.Label(
            wrap,
            text="The root analyzers now live in ~/AiCaptN/tools, while legacy MIA helper tools stay under apps/mia/tools when present.",
            justify="left",
        )
        footer.pack(anchor="w", pady=(12, 0))


if __name__ == "__main__":
    ToolHub().mainloop()
