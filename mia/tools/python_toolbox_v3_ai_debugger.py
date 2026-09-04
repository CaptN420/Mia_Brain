#!/usr/bin/env python3

#python_toolbox_v3_ai_debugger.py

#Boîte à outils Python V3 orientée debug intelligent:
#- audit de projet Python
#- détection d'erreurs fréquentes
#- suggestions de patch ciblées
#- mini "IA debugger" heuristique locale
#- rapport JSON/TXT
#- ouverture fichier à ligne précise
#- mini UI Tkinter
"""
Exemples:
    python3 python_toolbox_v3_ai_debugger.py diagnose .
    python3 python_toolbox_v3_ai_debugger.py diagnose . --focus ui_launcher.py
    python3 python_toolbox_v3_ai_debugger.py traceback . --file crash.txt
    python3 python_toolbox_v3_ai_debugger.py patch . --mode safe
    python3 python_toolbox_v3_ai_debugger.py gui
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import py_compile
import re
import subprocess
import sys
import traceback as tbmod
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterator

DEFAULT_EXCLUDES = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", "venv", "env", "node_modules", "dist", "build"
}

TEXT_EXTENSIONS = {".py", ".pyw", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log"}

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

def iter_files(root: Path, suffixes: tuple[str, ...] = (".py",), include_all: bool = False) -> Iterator[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if include_all or d not in DEFAULT_EXCLUDES]
        for name in files:
            path = Path(base) / name
            if include_all or path.suffix in suffixes:
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

def write_backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{stamp}")
    backup.write_bytes(path.read_bytes())
    return backup

def print_header(title: str) -> None:
    print(f"\n=== {title} ===")

def open_in_editor(path: Path, line: int | None = None, editor: str | None = None) -> int:
    editor = editor or os.environ.get("EDITOR") or "nano"
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
        print(f"[ERREUR] Éditeur introuvable: {editor}")
        return 1

def gather_compile(root: Path, include_all: bool = False, focus: str | None = None) -> tuple[list[str], list[CompileError]]:
    ok, errors = [], []
    files = sorted(iter_files(root, include_all=include_all))
    if focus:
        files = [p for p in files if focus in str(p)]
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
            ok.append(rel(path, root))
        except py_compile.PyCompileError as exc:
            errors.append(CompileError(file=rel(path, root), message=exc.msg))
    return ok, errors

def scan_python_ast(path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    text, enc = safe_read_text(path)
    if text is None:
        findings.append(Finding("high", "encoding", rel(path, root), None, "unreadable_text",
                                "Fichier texte illisible.", "Impossible de lire le fichier avec encodages usuels.",
                                "Vérifie l'encodage puis convertis en UTF-8."))
        return findings

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        findings.append(Finding("critical", "syntax", rel(path, root), exc.lineno, "ast_syntax_error",
                                f"Erreur de syntaxe: {exc.msg}",
                                (exc.text or "").rstrip(),
                                "Corrige d'abord cette erreur avant toute autre analyse."))
        return findings
    except Exception as exc:
        findings.append(Finding("high", "parse", rel(path, root), None, "ast_parse_failed",
                                "Analyse AST impossible.", str(exc),
                                "Vérifie le fichier manuellement."))
        return findings

    defined_funcs = set()
    defined_classes = set()
    imported_names = set()
    assigned_names = set()
    used_names: list[tuple[str, int]] = []
    method_calls: list[tuple[str, str, int]] = []
    duplicate_funcs = defaultdict(list)

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
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                method_calls.append((node.func.value.id, node.func.attr, getattr(node, "lineno", 0)))

    for func_name, lines in duplicate_funcs.items():
        if len(lines) > 1:
            findings.append(Finding("medium", "structure", rel(path, root), lines[1], "duplicate_function_name",
                                    f"Fonction définie plusieurs fois: {func_name}",
                                    f"Lignes: {lines}",
                                    "Vérifie si c'est un écrasement involontaire."))

    probably_undefined = []
    allowed = set(dir(__builtins__)) | defined_funcs | defined_classes | imported_names | assigned_names | {"self", "cls", "True", "False", "None"}
    for name, line in used_names:
        if name not in allowed and not name.startswith("_"):
            probably_undefined.append((name, line))
    seen = set()
    for name, line in probably_undefined:
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        findings.append(Finding("medium", "name", rel(path, root), line, "possibly_undefined_name",
                                f"Nom potentiellement non défini: {name}",
                                f"Usage à la ligne {line}",
                                "Vérifie si ce nom devait être importé, déclaré, ou s'il y a une faute de frappe."))

    lower_text = text.lower()
    if "wx." in text or "import wx" in text:
        if "Bind(" in text and "def on_" not in text and "def _on_" not in text:
            findings.append(Finding("medium", "wxpython", rel(path, root), None, "wx_bind_handler_suspect",
                                    "Bindings wx détectés sans handlers évidents.",
                                    "Présence de Bind(...) mais peu de callbacks nommés on_*.",
                                    "Vérifie que chaque Bind référence bien une méthode existante."))
        if "AppendItem(self." in text:
            findings.append(Finding("high", "wxpython", rel(path, root), None, "double_self_typo",
                                    "Typo probable 'self.' détectée.",
                                    "Pattern 'AppendItem(self.' trouvé.",
                                    "Remplace 'self.' par 'self.'."))

    if "Gtk-CRITICAL" in text or "Gtk-WARNING" in text:
        findings.append(Finding("medium", "gtk", rel(path, root), None, "gtk_runtime_log_present",
                                "Logs GTK présents dans le fichier.",
                                "Des traces GTK sont stockées ici.",
                                "Ce n'est pas forcément un bug de code ici, mais garde ces logs pour le diagnostic UI."))

    return findings

def detect_text_smells(path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    text, enc = safe_read_text(path)
    if text is None:
        return findings
    lines = text.splitlines()

    for i, line in enumerate(lines, start=1):
        if "\t" in line:
            findings.append(Finding("low", "formatting", rel(path, root), i, "tab_indent_or_tab_char",
                                    "TAB trouvé dans une ligne.",
                                    line.rstrip(),
                                    "Remplace les tabs par 4 espaces pour éviter des erreurs d'indentation."))
        if re.search(r"[ \t]+$", line):
            findings.append(Finding("low", "formatting", rel(path, root), i, "trailing_whitespace",
                                    "Espaces en fin de ligne.", line.rstrip("\n"),
                                    "Retire les espaces de fin."))
        if len(line) > 120:
            findings.append(Finding("low", "readability", rel(path, root), i, "line_too_long",
                                    f"Ligne longue ({len(line)} caractères).", line[:160],
                                    "Découpe la ligne pour faciliter le debug."))

    if text and not text.endswith("\n"):
        findings.append(Finding("low", "formatting", rel(path, root), len(lines), "missing_final_newline",
                                "Pas de newline final.", "(EOF sans \\n)",
                                "Ajoute un newline final."))

    fancy = ["“", "”", "‘", "’", "—", "–", "\u00A0"]
    if any(ch in text for ch in fancy):
        findings.append(Finding("medium", "encoding", rel(path, root), None, "typographic_quotes_or_symbols",
                                "Symboles typographiques trouvés.", "Guillemets / tirets typographiques présents.",
                                "Normalise en ASCII simple si ces caractères cassent le code ou le terminal."))

    return findings

def analyze_traceback_text(trace_text: str, root: Path | None = None) -> list[Finding]:
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
            file_display = last_file or "<traceback>"
            suggestion = {
                "IndentationError": "Vérifie l'alignement des blocs, tabs vs espaces, et les retours à la ligne précédents.",
                "SyntaxError": "Inspecte la ligne indiquée et la ligne juste avant. Parenthèse, virgule, deux-points ou indentation manquants sont fréquents.",
                "NameError": f"Le nom '{m.groupdict().get('name','?')}' semble absent. Vérifie typo, import ou ordre de déclaration.",
                "AttributeError": "La méthode/attribut appelé n'existe probablement pas. Vérifie le nom exact et la classe ciblée.",
                "TypeErrorUnexpectedKw": f"Le paramètre '{m.groupdict().get('kw','?')}' n'est pas accepté. Compare l'appel et la signature réelle.",
                "ValueErrorTargetMissing": "La cible cherchée n'existe pas dans les données actives. Vérifie session, normalisation, et correspondance exacte.",
                "UnicodeDecodeError": "Le fichier ou flux n'est pas décodé avec le bon encodage. Essaie UTF-8, UTF-8-SIG, latin-1 ou cp1252.",
                "GtkCritical": "Le layout UI produit une taille invalide. Vérifie sizers, proportions, min size et widgets cachés.",
            }.get(rule, "Analyse manuelle requise.")
            findings.append(Finding(
                "critical" if rule in {"IndentationError", "SyntaxError"} else "high",
                "traceback",
                file_display,
                last_line,
                rule,
                f"Trace détectée: {rule}",
                line.strip(),
                suggestion
            ))
    return findings

def project_diagnose(root: Path, include_all: bool = False, focus: str | None = None) -> dict:
    root = root.resolve()
    files = sorted(iter_files(root, include_all=include_all))
    if focus:
        files = [p for p in files if focus in str(p)]

    ok, compile_errors = gather_compile(root, include_all=include_all, focus=focus)
    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_python_ast(path, root))
        findings.extend(detect_text_smells(path, root))

    # Higher-value heuristics from compile errors
    for err in compile_errors:
        msg = err.message
        line_match = re.search(r"line (\d+)", msg)
        lineno = int(line_match.group(1)) if line_match else None
        summary = "Erreur de compilation"
        suggestion = "Ouvre le fichier à la ligne indiquée et corrige d'abord cette erreur."
        if "IndentationError" in msg:
            summary = "IndentationError détectée"
            suggestion = "Cherche tabs/espaces mélangés, bloc mal aligné, ou ligne précédente incomplète."
        elif "SyntaxError" in msg:
            summary = "SyntaxError détectée"
            suggestion = "Vérifie parenthèses, virgules, quotes, ':' et la ligne précédente."
        findings.append(Finding("critical", "compile", err.file, lineno, "compile_error", summary, msg, suggestion))

    # Deduplicate
    dedup = []
    seen = set()
    for f in findings:
        key = (f.file, f.line, f.rule, f.summary)
        if key not in seen:
            dedup.append(f)
            seen.add(key)

    counts = Counter(f.severity for f in dedup)
    by_cat = Counter(f.category for f in dedup)
    return {
        "generated_at": datetime.now().isoformat(),
        "root": str(root),
        "focus": focus,
        "python_files": len(files),
        "compile_ok_count": len(ok),
        "compile_error_count": len(compile_errors),
        "compile_errors": [asdict(x) for x in compile_errors],
        "findings": [asdict(x) for x in dedup],
        "summary": {
            "severity_counts": dict(counts),
            "category_counts": dict(by_cat),
        },
    }

def render_diagnosis_text(report: dict) -> str:
    lines = []
    lines.append("=== PYTHON TOOLBOX V3 | AI DEBUGGER REPORT ===")
    lines.append(f"Generated at: {report['generated_at']}")
    lines.append(f"Root: {report['root']}")
    if report.get("focus"):
        lines.append(f"Focus: {report['focus']}")
    lines.append(f"Python files: {report['python_files']}")
    lines.append(f"Compile OK: {report['compile_ok_count']}")
    lines.append(f"Compile errors: {report['compile_error_count']}")
    lines.append("")
    lines.append("Severity counts:")
    for k, v in report["summary"]["severity_counts"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("Category counts:")
    for k, v in report["summary"]["category_counts"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("")
    lines.append("=== TOP FINDINGS ===")
    ordered = sorted(
        report["findings"],
        key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x["severity"], 9)
    )
    for item in ordered[:400]:
        loc = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
        lines.append(f"[{item['severity'].upper()}] {loc} | {item['rule']}")
        lines.append(f"  Summary    : {item['summary']}")
        lines.append(f"  Evidence   : {item['evidence']}")
        lines.append(f"  Suggestion : {item['suggestion']}")
        lines.append("")
    return "\n".join(lines)

def patch_text(text: str, mode: str) -> tuple[str, int]:
    changes = 0
    new = text
    if mode in {"tabs", "safe"}:
        count = new.count("\t")
        if count:
            new = new.replace("\t", "    ")
            changes += count
    if mode in {"trim", "safe"}:
        out = []
        for line in new.splitlines(True):
            if line.endswith("\r\n"):
                core, nl = line[:-2], "\r\n"
            elif line.endswith("\n"):
                core, nl = line[:-1], "\n"
            else:
                core, nl = line, ""
            new_core = core.rstrip(" \t")
            if new_core != core:
                changes += 1
            out.append(new_core + nl)
        new = "".join(out)
    if mode in {"newline", "safe"}:
        if new and not new.endswith("\n"):
            new += "\n"
            changes += 1
    if mode in {"crlf", "safe"}:
        count = new.count("\r\n")
        if count:
            new = new.replace("\r\n", "\n")
            changes += count
    if mode in {"quotes", "safe"}:
        replacements = {"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-", "\u00A0": " "}
        for old, rep in replacements.items():
            c = new.count(old)
            if c:
                new = new.replace(old, rep)
                changes += c
    return new, changes

def patch_file(path: Path, mode: str, backup: bool = True) -> dict:
    text, enc = safe_read_text(path)
    if text is None:
        return {"file": str(path), "changed": False, "changes": 0, "error": "Unreadable"}
    new, changes = patch_text(text, mode)
    if new != text:
        backup_path = str(write_backup(path)) if backup else None
        path.write_text(new, encoding="utf-8")
        return {"file": str(path), "changed": True, "changes": changes, "backup": backup_path}
    return {"file": str(path), "changed": False, "changes": 0, "backup": None}

def cmd_diagnose(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    report = project_diagnose(root, include_all=args.all, focus=args.focus)
    text = render_diagnosis_text(report)
    if args.output:
        out = Path(args.output).resolve()
        if args.format == "json":
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            out.write_text(text, encoding="utf-8")
        print(f"[OK] Rapport écrit: {out}")
    else:
        if args.format == "json":
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(text)
    return 0

def cmd_traceback(args: argparse.Namespace) -> int:
    if args.file:
        trace_text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        trace_text = sys.stdin.read()
    findings = analyze_traceback_text(trace_text)
    if args.format == "json":
        print(json.dumps([asdict(x) for x in findings], indent=2, ensure_ascii=False))
    else:
        print("=== TRACEBACK ANALYSIS ===")
        for item in findings:
            loc = f"{item.file}:{item.line}" if item.line else item.file
            print(f"[{item.severity.upper()}] {loc} | {item.rule}")
            print(f"  {item.summary}")
            print(f"  Evidence   : {item.evidence}")
            print(f"  Suggestion : {item.suggestion}\n")
    return 0

def cmd_patch(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    changed = 0
    total_changes = 0
    for path in sorted(iter_files(root, include_all=args.all)):
        result = patch_file(path, args.mode, backup=not args.no_backup)
        if result["changed"]:
            changed += 1
            total_changes += result["changes"]
            print(f"[PATCHED] {rel(Path(result['file']), root)} | changes={result['changes']} | backup={result['backup']}")
        elif args.verbose:
            print(f"[SKIP] {rel(Path(result['file']), root)}")
    print(f"\nRésumé: {changed} fichier(s) modifié(s) | {total_changes} changement(s)")
    return 0

def cmd_open(args: argparse.Namespace) -> int:
    return open_in_editor(Path(args.file).resolve(), line=args.line, editor=args.editor)

def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        print(f"[ERREUR] Tkinter indisponible: {exc}")
        return 1

    class App:
        def __init__(self, root_window: "tk.Tk") -> None:
            self.root = root_window
            self.root.title("Python Toolbox V3 - AI Debugger")
            self.root.geometry("1220x820")
            self.items: list[tuple[str, int | None]] = []

            top = ttk.Frame(self.root, padding=8)
            top.pack(fill="x")

            self.path_var = tk.StringVar(value=str(Path.cwd()))
            self.focus_var = tk.StringVar(value="")
            self.editor_var = tk.StringVar(value=os.environ.get("EDITOR", "nano"))

            ttk.Label(top, text="Projet").grid(row=0, column=0, sticky="w")
            ttk.Entry(top, textvariable=self.path_var, width=92).grid(row=0, column=1, sticky="ew", padx=6)
            ttk.Button(top, text="Browse", command=self.pick_dir).grid(row=0, column=2, padx=3)
            ttk.Label(top, text="Focus").grid(row=1, column=0, sticky="w")
            ttk.Entry(top, textvariable=self.focus_var, width=28).grid(row=1, column=1, sticky="w", padx=6)
            ttk.Label(top, text="Editor").grid(row=1, column=2, sticky="e")
            ttk.Entry(top, textvariable=self.editor_var, width=14).grid(row=1, column=3, sticky="w")
            top.columnconfigure(1, weight=1)

            btns = ttk.Frame(self.root, padding=(8, 0, 8, 8))
            btns.pack(fill="x")
            for i, (label, cmd) in enumerate([
                ("Diagnose", self.do_diagnose),
                ("Patch safe", self.do_patch),
                ("Compile only", self.do_compile),
                ("Export TXT", self.do_export_txt),
                ("Export JSON", self.do_export_json),
                ("Clear", self.clear_all),
            ]):
                ttk.Button(btns, text=label, command=cmd).grid(row=0, column=i, padx=4, pady=3)

            paned = ttk.Panedwindow(self.root, orient="horizontal")
            paned.pack(fill="both", expand=True, padx=8, pady=8)

            left = ttk.Frame(paned, padding=4)
            right = ttk.Frame(paned, padding=4)
            paned.add(left, weight=1)
            paned.add(right, weight=3)

            ttk.Label(left, text="Findings cliquables").pack(anchor="w")
            self.listbox = tk.Listbox(left, exportselection=False)
            self.listbox.pack(fill="both", expand=True)
            self.listbox.bind("<Double-Button-1>", self.open_selected)
            ctl = ttk.Frame(left)
            ctl.pack(fill="x", pady=4)
            ttk.Button(ctl, text="Open selected", command=self.open_selected).pack(side="left", padx=2)

            ttk.Label(right, text="Rapport / sortie").pack(anchor="w")
            self.output = ScrolledText(right, wrap="word")
            self.output.pack(fill="both", expand=True)

        def clear_all(self) -> None:
            self.listbox.delete(0, "end")
            self.output.delete("1.0", "end")
            self.items.clear()

        def pick_dir(self) -> None:
            chosen = filedialog.askdirectory(initialdir=self.path_var.get() or str(Path.cwd()))
            if chosen:
                self.path_var.set(chosen)

        def set_output(self, text: str) -> None:
            self.output.delete("1.0", "end")
            self.output.insert("1.0", text)

        def add_item(self, file: str, line: int | None, label: str) -> None:
            self.items.append((file, line))
            self.listbox.insert("end", label)

        def root_path(self) -> Path:
            return Path(self.path_var.get()).expanduser().resolve()

        def focus(self) -> str | None:
            value = self.focus_var.get().strip()
            return value or None

        def open_selected(self, _event=None) -> None:
            sel = self.listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            file, line = self.items[idx]
            target = self.root_path() / file if not Path(file).is_absolute() else Path(file)
            rc = open_in_editor(target.resolve(), line=line, editor=self.editor_var.get().strip() or "nano")
            if rc != 0:
                messagebox.showerror("Erreur", "Impossible d'ouvrir le fichier dans l'éditeur.")

        def do_compile(self) -> None:
            root = self.root_path()
            ok, errors = gather_compile(root, focus=self.focus())
            self.listbox.delete(0, "end")
            self.items.clear()
            out = [f"Compile OK: {len(ok)}", f"Compile errors: {len(errors)}", ""]
            for err in errors:
                m = re.search(r"line (\d+)", err.message)
                line = int(m.group(1)) if m else None
                self.add_item(err.file, line, f"{err.file}:{line or '?'}")
                out.append(f"[ERROR] {err.file}")
                out.append(err.message)
                out.append("")
            self.set_output("\n".join(out))

        def do_diagnose(self) -> None:
            root = self.root_path()
            report = project_diagnose(root, focus=self.focus())
            self.listbox.delete(0, "end")
            self.items.clear()
            for item in sorted(report["findings"], key=lambda x: {"critical":0,"high":1,"medium":2,"low":3}.get(x["severity"], 9)):
                loc = f"{item['file']}:{item['line'] or '?'}"
                self.add_item(item["file"], item["line"], f"[{item['severity']}] {loc} | {item['rule']}")
            self.set_output(render_diagnosis_text(report))

        def do_patch(self) -> None:
            root = self.root_path()
            changed_lines = []
            for path in sorted(iter_files(root)):
                result = patch_file(path, "safe", backup=True)
                if result["changed"]:
                    changed_lines.append(f"{rel(Path(result['file']), root)} | changes={result['changes']} | backup={result['backup']}")
            self.set_output("\n".join(changed_lines) if changed_lines else "(aucun changement)")
            self.listbox.delete(0, "end")
            self.items.clear()
            for line in changed_lines:
                file = line.split(" | ", 1)[0]
                self.add_item(file, None, file)

        def do_export_txt(self) -> None:
            root = self.root_path()
            report = project_diagnose(root, focus=self.focus())
            content = render_diagnosis_text(report)
            output = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
            if output:
                Path(output).write_text(content, encoding="utf-8")
                messagebox.showinfo("OK", f"Rapport écrit:\n{output}")

        def do_export_json(self) -> None:
            root = self.root_path()
            report = project_diagnose(root, focus=self.focus())
            output = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
            if output:
                Path(output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                messagebox.showinfo("OK", f"Rapport écrit:\n{output}")

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0

def cmd_gui(_args: argparse.Namespace) -> int:
    return launch_gui()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python Toolbox V3 - AI debugger heuristique local.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("path", help="Chemin du projet à analyser")
    common.add_argument("--all", action="store_true", help="Inclure aussi les dossiers exclus")
    common.add_argument("--focus", help="Filtre fichier/sous-chemin pour cibler l'analyse")

    p_diag = sub.add_parser("diagnose", parents=[common], help="Lancer le diagnostic intelligent")
    p_diag.add_argument("--format", choices=("txt", "json"), default="txt")
    p_diag.add_argument("--output", help="Fichier de sortie")
    p_diag.set_defaults(func=cmd_diagnose)

    p_trace = sub.add_parser("traceback", help="Analyser un traceback depuis un fichier ou stdin")
    p_trace.add_argument("--file", help="Fichier texte de traceback/log")
    p_trace.add_argument("--format", choices=("txt", "json"), default="txt")
    p_trace.set_defaults(func=cmd_traceback)

    p_patch = sub.add_parser("patch", parents=[common], help="Patch safe de formatting")
    p_patch.add_argument("--mode", choices=("tabs", "trim", "newline", "crlf", "quotes", "safe"), default="safe")
    p_patch.add_argument("--no-backup", action="store_true")
    p_patch.add_argument("-v", "--verbose", action="store_true")
    p_patch.set_defaults(func=cmd_patch)

    p_open = sub.add_parser("open", help="Ouvrir un fichier à une ligne")
    p_open.add_argument("file")
    p_open.add_argument("--line", type=int, default=1)
    p_open.add_argument("--editor")
    p_open.set_defaults(func=cmd_open)

    p_gui = sub.add_parser("gui", help="Mini interface Tkinter")
    p_gui.set_defaults(func=cmd_gui)

    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[INFO] Annulé par l'utilisateur.")
        return 130
    except Exception as exc:
        print(f"[ERREUR] {type(exc).__name__}: {exc}")
        tbmod.print_exc()
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
