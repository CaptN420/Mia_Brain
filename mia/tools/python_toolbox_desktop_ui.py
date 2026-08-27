#!/usr/bin/env python3
"""
python_toolbox_desktop_ui.py

UI desktop standalone pour ton toolkit Python.
But:
- choisir un projet
- lancer scan / check / diagnose / patch
- analyser un traceback
- voir les résultats dans une interface
- double-cliquer sur un résultat pour ouvrir le fichier à la bonne ligne

Dépendances:
- Standard library seulement (tkinter)

Usage:
    python3 python_toolbox_desktop_ui.py
"""

from __future__ import annotations

import ast
import os
import py_compile
import re
import subprocess
import traceback as tbmod
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


DEFAULT_EXCLUDES = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", "venv", "env", "node_modules", "dist", "build"
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Finding:
    severity: str
    category: str
    file: str
    line: int | None
    rule: str
    summary: str
    evidence: str
    suggestion: str


@dataclass
class CompileError:
    file: str
    message: str


ERROR_PATTERNS = {
    "IndentationError": re.compile(r"IndentationError: (?P<msg>.+)"),
    "SyntaxError": re.compile(r"SyntaxError: (?P<msg>.+)"),
    "NameError": re.compile(r"NameError: name ['\"](?P<name>[^'\"]+)['\"] is not defined"),
    "AttributeError": re.compile(r"AttributeError: ['\"](?P<obj>[^'\"]+)['\"] object has no attribute ['\"](?P<attr>[^'\"]+)['\"]"),
    "TypeErrorUnexpectedKw": re.compile(r"TypeError: (?P<func>\w+)\(\) got an unexpected keyword argument ['\"](?P<kw>[^'\"]+)['\"]"),
    "ValueErrorTargetMissing": re.compile(r"ValueError: Target equation not found"),
    "UnicodeDecodeError": re.compile(r"UnicodeDecodeError: (?P<msg>.+)"),
    "GtkCritical": re.compile(r"Gtk-(CRITICAL|WARNING).+"),
}


def iter_py_files(root: Path, include_all: bool = False) -> Iterator[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if include_all or d not in DEFAULT_EXCLUDES]
        for name in files:
            path = Path(base) / name
            if path.suffix == ".py":
                yield path


def safe_read_text(path: Path) -> tuple[str | None, str | None]:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc), enc
        except Exception:
            continue
    return None, None


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def open_in_editor(path: Path, line: int | None = None, editor: str | None = None) -> int:
    editor = (editor or os.environ.get("EDITOR") or "nano").strip()
    launchers = {
        "nano": lambda p, l: ["nano", f"+{l or 1}", str(p)],
        "vim": lambda p, l: ["vim", f"+{l or 1}", str(p)],
        "nvim": lambda p, l: ["nvim", f"+{l or 1}", str(p)],
        "code": lambda p, l: ["code", "-g", f"{p}:{l or 1}"],
        "codium": lambda p, l: ["codium", "-g", f"{p}:{l or 1}"],
        "mousepad": lambda p, l: ["mousepad", str(p)],
        "gedit": lambda p, l: ["gedit", str(p)],
        "xed": lambda p, l: ["xed", str(p)],
    }
    cmd = launchers.get(editor, lambda p, l: [editor, str(p)])(path, line)
    try:
        subprocess.run(cmd, check=False)
        return 0
    except FileNotFoundError:
        return 1


def gather_compile(root: Path, focus: str | None = None, include_all: bool = False) -> tuple[list[str], list[CompileError]]:
    ok, errors = [], []
    files = sorted(iter_py_files(root, include_all=include_all))
    if focus:
        files = [p for p in files if focus.lower() in str(p).lower()]

    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
            ok.append(rel(path, root))
        except py_compile.PyCompileError as exc:
            errors.append(CompileError(file=rel(path, root), message=exc.msg))
    return ok, errors


def scan_python_ast(path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    text, _enc = safe_read_text(path)
    if text is None:
        findings.append(Finding(
            "high", "encoding", rel(path, root), None, "unreadable_text",
            "Fichier illisible.",
            "Impossible de lire le fichier avec encodages usuels.",
            "Vérifie l'encodage et convertis en UTF-8."
        ))
        return findings

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        findings.append(Finding(
            "critical", "syntax", rel(path, root), exc.lineno, "ast_syntax_error",
            f"Erreur de syntaxe: {exc.msg}",
            (exc.text or "").rstrip(),
            "Corrige d'abord cette erreur avant toute autre analyse."
        ))
        return findings
    except Exception as exc:
        findings.append(Finding(
            "high", "parse", rel(path, root), None, "ast_parse_failed",
            "Analyse AST impossible.",
            str(exc),
            "Vérifie ce fichier manuellement."
        ))
        return findings

    defined_funcs = set()
    defined_classes = set()
    imported_names = set()
    assigned_names = set()
    duplicate_funcs = defaultdict(list)
    used_names: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defined_funcs.add(node.name)
            duplicate_funcs[node.name].append(node.lineno)
        elif isinstance(node, ast.AsyncFunctionDef):
            defined_funcs.add(node.name)
            duplicate_funcs[node.name].append(node.lineno)
        elif isinstance(node, ast.ClassDef):
            defined_classes.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned_names.add(node.target.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.append((node.id, getattr(node, "lineno", 0)))

    for func_name, lines in duplicate_funcs.items():
        if len(lines) > 1:
            findings.append(Finding(
                "medium", "structure", rel(path, root), lines[1], "duplicate_function_name",
                f"Fonction définie plusieurs fois: {func_name}",
                f"Lignes: {lines}",
                "Vérifie si c'est voulu ou si une définition écrase l'autre."
            ))

    allowed = set(dir(__builtins__)) | defined_funcs | defined_classes | imported_names | assigned_names | {"self", "cls", "True", "False", "None"}
    seen_names = set()
    for name, line in used_names:
        if name not in allowed and not name.startswith("_"):
            key = (name, line)
            if key in seen_names:
                continue
            seen_names.add(key)
            findings.append(Finding(
                "medium", "name", rel(path, root), line, "possibly_undefined_name",
                f"Nom potentiellement non défini: {name}",
                f"Usage à la ligne {line}",
                "Vérifie typo, import manquant ou ordre de déclaration."
            ))

    for i, line in enumerate(text.splitlines(), start=1):
        if "\t" in line:
            findings.append(Finding(
                "low", "formatting", rel(path, root), i, "tab_char",
                "TAB trouvé dans une ligne.",
                line.rstrip(),
                "Remplace les tabs par 4 espaces."
            ))
        if re.search(r"[ \t]+$", line):
            findings.append(Finding(
                "low", "formatting", rel(path, root), i, "trailing_whitespace",
                "Espaces en fin de ligne.",
                line.rstrip("\n"),
                "Retire les espaces de fin."
            ))
        if len(line) > 120:
            findings.append(Finding(
                "low", "readability", rel(path, root), i, "line_too_long",
                f"Ligne longue ({len(line)} caractères).",
                line[:180],
                "Découpe la ligne pour mieux debugger."
            ))

    if text and not text.endswith("\n"):
        findings.append(Finding(
            "low", "formatting", rel(path, root), None, "missing_final_newline",
            "Pas de newline final.",
            "(EOF sans \\n)",
            "Ajoute un newline final."
        ))

    if "self." in text:
        findings.append(Finding(
            "high", "logic", rel(path, root), None, "double_self_typo",
            "Typo probable 'self.' détectée.",
            "Pattern trouvé dans le fichier.",
            "Remplace 'self.' par 'self.'."
        ))

    if "Bind(" in text and "on_" in text and "AttributeError" not in text:
        # soft heuristic فقط
        findings.append(Finding(
            "low", "wxpython", rel(path, root), None, "wx_bind_present",
            "Bindings wx détectés.",
            "Présence de Bind(...) et callbacks on_*.",
            "Vérifie que tous les handlers bindés existent bien."
        ))

    return findings


def analyze_project(root: Path, focus: str | None = None, include_all: bool = False) -> dict:
    root = root.resolve()
    files = sorted(iter_py_files(root, include_all=include_all))
    if focus:
        files = [p for p in files if focus.lower() in str(p).lower()]

    ok, compile_errors = gather_compile(root, focus=focus, include_all=include_all)
    findings: list[Finding] = []

    for path in files:
        findings.extend(scan_python_ast(path, root))

    for err in compile_errors:
        line_match = re.search(r"line (\d+)", err.message)
        lineno = int(line_match.group(1)) if line_match else None
        suggestion = "Ouvre le fichier à la ligne indiquée et corrige d'abord cette erreur."
        summary = "Erreur de compilation"
        if "IndentationError" in err.message:
            summary = "IndentationError détectée"
            suggestion = "Cherche tabs/espaces mélangés, bloc mal aligné, ou ligne précédente incomplète."
        elif "SyntaxError" in err.message:
            summary = "SyntaxError détectée"
            suggestion = "Vérifie parenthèses, quotes, ':' et la ligne précédente."
        findings.append(Finding(
            "critical", "compile", err.file, lineno, "compile_error",
            summary, err.message, suggestion
        ))

    dedup = []
    seen = set()
    for f in findings:
        key = (f.file, f.line, f.rule, f.summary)
        if key not in seen:
            dedup.append(f)
            seen.add(key)

    dedup.sort(key=lambda x: (SEVERITY_ORDER.get(x.severity, 99), x.file, x.line or 0, x.rule))

    return {
        "generated_at": datetime.now().isoformat(),
        "root": str(root),
        "focus": focus,
        "python_files": len(files),
        "compile_ok_count": len(ok),
        "compile_error_count": len(compile_errors),
        "findings": dedup,
        "severity_counts": Counter(f.severity for f in dedup),
        "category_counts": Counter(f.category for f in dedup),
    }


def render_project_report(report: dict) -> str:
    lines = []
    lines.append("=== PYTHON TOOLBOX DESKTOP UI REPORT ===")
    lines.append(f"Generated at: {report['generated_at']}")
    lines.append(f"Root: {report['root']}")
    if report.get("focus"):
        lines.append(f"Focus: {report['focus']}")
    lines.append(f"Python files: {report['python_files']}")
    lines.append(f"Compile OK: {report['compile_ok_count']}")
    lines.append(f"Compile errors: {report['compile_error_count']}")
    lines.append("")
    lines.append("Severity counts:")
    for k, v in report["severity_counts"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("Category counts:")
    for k, v in report["category_counts"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("")
    lines.append("=== FINDINGS ===")
    for item in report["findings"]:
        loc = f"{item.file}:{item.line}" if item.line else item.file
        lines.append(f"[{item.severity.upper()}] {loc} | {item.rule}")
        lines.append(f"  Summary    : {item.summary}")
        lines.append(f"  Evidence   : {item.evidence}")
        lines.append(f"  Suggestion : {item.suggestion}")
        lines.append("")
    return "\n".join(lines)


def analyze_traceback_text(trace_text: str) -> list[Finding]:
    findings: list[Finding] = []
    last_file = None
    last_line = None

    for line in trace_text.splitlines():
        mfile = re.search(r'File "([^"]+)", line (\d+)', line)
        if mfile:
            last_file = mfile.group(1)
            last_line = int(mfile.group(2))

        for rule, regex in ERROR_PATTERNS.items():
            m = regex.search(line)
            if not m:
                continue
            suggestion = {
                "IndentationError": "Vérifie l'alignement des blocs, tabs vs espaces, et les lignes autour.",
                "SyntaxError": "Regarde la ligne ciblée et la ligne juste avant.",
                "NameError": f"Le nom '{m.groupdict().get('name', '?')}' semble absent. Vérifie typo, import ou déclaration.",
                "AttributeError": "La méthode/attribut n'existe probablement pas sur l'objet visé.",
                "TypeErrorUnexpectedKw": f"Le keyword '{m.groupdict().get('kw', '?')}' n'est pas accepté par cette fonction.",
                "ValueErrorTargetMissing": "La cible cherchée n'existe pas dans les données/session active.",
                "UnicodeDecodeError": "Le mauvais encodage est probablement utilisé.",
                "GtkCritical": "Le layout UI produit une taille invalide. Vérifie proportions, min size, widgets cachés.",
            }.get(rule, "Analyse manuelle requise.")
            findings.append(Finding(
                "critical" if rule in {"IndentationError", "SyntaxError"} else "high",
                "traceback",
                last_file or "<traceback>",
                last_line,
                rule,
                f"Trace détectée: {rule}",
                line.strip(),
                suggestion,
            ))
    return findings


def patch_safe_project(root: Path, include_all: bool = False) -> list[str]:
    changed = []
    for path in sorted(iter_py_files(root, include_all=include_all)):
        text, _ = safe_read_text(path)
        if text is None:
            continue
        original = text
        text = text.replace("\t", "    ")
        text = text.replace("\r\n", "\n")
        text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace("—", "-").replace("–", "-").replace("\u00A0", " ")
        text = "".join((ln.rstrip(" \t") + ("\n" if ln.endswith("\n") else "")) for ln in text.splitlines(True))
        if text and not text.endswith("\n"):
            text += "\n"
        if text == original:
            backup = path.with_suffix(path.suffix + ".bak")
            if not backup.exists():
                backup.write_text(original, encoding="utf-8", errors="ignore")
            path.write_text(text, encoding="utf-8")
            changed.append(rel(path, root))
    return changed


class ToolboxUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Python Toolbox Desktop UI")
        self.root.geometry("1350x860")
        self.root.minsize(1000, 650)

        self.project_var = tk.StringVar(value=str(Path.cwd()))
        self.focus_var = tk.StringVar(value="")
        self.editor_var = tk.StringVar(value=os.environ.get("EDITOR", "nano"))
        self.status_var = tk.StringVar(value="Ready.")
        self.include_all_var = tk.BooleanVar(value=False)

        self.click_items: list[tuple[str, int | None]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        top = ttk.LabelFrame(outer, text="Projet", padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Path").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.project_var, width=95).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse", command=self.pick_project).grid(row=0, column=2, padx=4)

        ttk.Label(top, text="Focus file").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.focus_var, width=30).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(top, text="Editor").grid(row=1, column=2, sticky="e")
        ttk.Entry(top, textvariable=self.editor_var, width=14).grid(row=1, column=3, sticky="w", padx=6)

        ttk.Checkbutton(top, text="Include excluded dirs", variable=self.include_all_var).grid(row=1, column=4, sticky="w", padx=6)

        top.columnconfigure(1, weight=1)

        actions = ttk.LabelFrame(outer, text="Actions", padding=8)
        actions.pack(fill="x", pady=(8, 0))

        buttons = [
            ("Scan files", self.run_scan),
            ("Compile check", self.run_compile),
            ("Diagnose AI", self.run_diagnose),
            ("Patch safe", self.run_patch_safe),
            ("Load traceback", self.load_traceback_file),
            ("Analyze traceback", self.analyze_traceback_from_box),
            ("Export report", self.export_report),
            ("Clear", self.clear_output),
        ]

        for i, (label, cmd) in enumerate(buttons):
            ttk.Button(actions, text=label, command=cmd).grid(row=0, column=i, padx=4, pady=4)

        main = ttk.Panedwindow(outer, orient="horizontal")
        main.pack(fill="both", expand=True, pady=(8, 0))

        left = ttk.Frame(main, padding=4)
        center = ttk.Frame(main, padding=4)
        right = ttk.Frame(main, padding=4)
        main.add(left, weight=1)
        main.add(center, weight=3)
        main.add(right, weight=2)

        ttk.Label(left, text="Clickable results").pack(anchor="w")
        self.result_list = tk.Listbox(left, exportselection=False)
        self.result_list.pack(fill="both", expand=True)
        self.result_list.bind("<Double-Button-1>", self.open_selected_result)

        left_buttons = ttk.Frame(left)
        left_buttons.pack(fill="x", pady=4)
        ttk.Button(left_buttons, text="Open selected", command=self.open_selected_result).pack(side="left", padx=3)
        ttk.Button(left_buttons, text="Copy selected", command=self.copy_selected_label).pack(side="left", padx=3)

        ttk.Label(center, text="Report / output").pack(anchor="w")
        self.output = ScrolledText(center, wrap="word")
        self.output.pack(fill="both", expand=True)

        ttk.Label(right, text="Traceback / logs").pack(anchor="w")
        self.trace_box = ScrolledText(right, wrap="word")
        self.trace_box.pack(fill="both", expand=True)

        status = ttk.Label(outer, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", pady=(6, 0))

    def get_root(self) -> Path:
        return Path(self.project_var.get()).expanduser().resolve()

    def get_focus(self) -> str | None:
        value = self.focus_var.get().strip()
        return value or None

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.root.update_idletasks()

    def set_output(self, text: str) -> None:
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)

    def append_output(self, text: str) -> None:
        self.output.insert("end", text + "\n")
        self.output.see("end")

    def clear_results(self) -> None:
        self.result_list.delete(0, "end")
        self.click_items.clear()

    def add_click_item(self, file: str, line: int | None, label: str) -> None:
        self.click_items.append((file, line))
        self.result_list.insert("end", label)

    def pick_project(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.project_var.get() or str(Path.cwd()))
        if chosen:
            self.project_var.set(chosen)

    def open_selected_result(self, _event=None) -> None:
        sel = self.result_list.curselection()
        if not sel:
            return
        idx = sel[0]
        file, line = self.click_items[idx]
        root = self.get_root()
        target = (root / file).resolve() if not Path(file).is_absolute() else Path(file)
        rc = open_in_editor(target, line=line, editor=self.editor_var.get())
        if rc == 0:
            messagebox.showerror("Erreur", f"Impossible d'ouvrir l'éditeur: {self.editor_var.get()}")

    def copy_selected_label(self) -> None:
        sel = self.result_list.curselection()
        if not sel:
            return
        label = self.result_list.get(sel[0])
        self.root.clipboard_clear()
        self.root.clipboard_append(label)
        self.set_status("Selected label copied.")

    def clear_output(self) -> None:
        self.clear_results()
        self.output.delete("1.0", "end")
        self.trace_box.delete("1.0", "end")
        self.set_status("Cleared.")

    def run_scan(self) -> None:
        try:
            self.set_status("Scanning project...")
            self.clear_results()
            root = self.get_root()
            files = sorted(iter_py_files(root, include_all=self.include_all_var.get()))
            text_lines = []
            for p in files:
                rp = rel(p, root)
                self.add_click_item(rp, None, rp)
                text_lines.append(rp)
            self.set_output("\n".join(text_lines) if text_lines else "(no python files)")
            self.set_status(f"Scan done. {len(files)} Python files.")
        except Exception as exc:
            self.handle_error(exc)

    def run_compile(self) -> None:
        try:
            self.set_status("Running compile check...")
            self.clear_results()
            root = self.get_root()
            ok, errors = gather_compile(root, focus=self.get_focus(), include_all=self.include_all_var.get())
            lines = [f"Compile OK: {len(ok)}", f"Compile errors: {len(errors)}", ""]
            for err in errors:
                m = re.search(r"line (\d+)", err.message)
                line = int(m.group(1)) if m else None
                self.add_click_item(err.file, line, f"{err.file}:{line or '?'}")
                lines.append(f"[ERROR] {err.file}")
                lines.append(err.message)
                lines.append("")
            if not errors:
                for item in ok:
                    self.add_click_item(item, None, item)
            self.set_output("\n".join(lines))
            self.set_status("Compile check done.")
        except Exception as exc:
            self.handle_error(exc)

    def run_diagnose(self) -> None:
        try:
            self.set_status("Running AI diagnose...")
            self.clear_results()
            report = analyze_project(self.get_root(), focus=self.get_focus(), include_all=self.include_all_var.get())
            for item in report["findings"]:
                loc = f"{item.file}:{item.line or '?'}"
                self.add_click_item(item.file, item.line, f"[{item.severity}] {loc} | {item.rule}")
            self.set_output(render_project_report(report))
            self.set_status(f"Diagnose done. {len(report['findings'])} findings.")
        except Exception as exc:
            self.handle_error(exc)

    def run_patch_safe(self) -> None:
        try:
            if not messagebox.askyesno("Confirmer", "Appliquer le patch safe au projet Python sélectionné ?"):
                return
            self.set_status("Applying safe patch...")
            root = self.get_root()
            changed = patch_safe_project(root, include_all=self.include_all_var.get())
            self.clear_results()
            for item in changed:
                self.add_click_item(item, None, item)
            self.set_output("\n".join(changed) if changed else "(aucun changement)")
            self.set_status(f"Patch done. {len(changed)} file(s) changed.")
        except Exception as exc:
            self.handle_error(exc)

    def load_traceback_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choisir un fichier de log/traceback",
            filetypes=[("Text files", "*.txt *.log *.out *.err"), ("All files", "*.*")]
        )
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        self.trace_box.delete("1.0", "end")
        self.trace_box.insert("1.0", text)
        self.set_status(f"Loaded traceback file: {path}")

    def analyze_traceback_from_box(self) -> None:
        try:
            self.set_status("Analyzing traceback...")
            self.clear_results()
            text = self.trace_box.get("1.0", "end").strip()
            findings = analyze_traceback_text(text)
            lines = ["=== TRACEBACK ANALYSIS ===", ""]
            for item in findings:
                loc = f"{item.file}:{item.line}" if item.line else item.file
                self.add_click_item(item.file, item.line, f"[{item.severity}] {loc} | {item.rule}")
                lines.append(f"[{item.severity.upper()}] {loc} | {item.rule}")
                lines.append(f"  Summary    : {item.summary}")
                lines.append(f"  Evidence   : {item.evidence}")
                lines.append(f"  Suggestion : {item.suggestion}")
                lines.append("")
            self.set_output("\n".join(lines) if findings else "No traceback pattern detected.")
            self.set_status(f"Traceback analysis done. {len(findings)} finding(s).")
        except Exception as exc:
            self.handle_error(exc)

    def export_report(self) -> None:
        try:
            content = self.output.get("1.0", "end").strip()
            if not content:
                messagebox.showinfo("Info", "Il n'y a rien à exporter.")
                return
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
            )
            if not path:
                return
            Path(path).write_text(content + "\n", encoding="utf-8")
            self.set_status(f"Report exported: {path}")
        except Exception as exc:
            self.handle_error(exc)

    def handle_error(self, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        self.set_status(msg)
        messagebox.showerror("Erreur", msg)
        self.append_output("\n[INTERNAL ERROR]\n" + tbmod.format_exc())


def main() -> int:
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    ToolboxUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
