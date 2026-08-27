#!/usr/bin/env python3
"""
python_toolbox_v2.py
Boîte à outils Python pratique pour audit rapide + patch simple + mini UI Tkinter.

Fonctions CLI:
- scan       : liste les fichiers Python
- check      : compile les .py pour détecter les erreurs de syntaxe
- grep       : cherche un texte/regex
- todos      : trouve TODO / FIXME / HACK / BUG / NOTE
- longlines  : détecte les lignes trop longues
- utf8       : teste quels fichiers ne sont pas en UTF-8
- stats      : statistiques du projet
- report     : exporte un rapport .txt ou .json
- patch      : applique des patchs simples et sûrs
- gui        : ouvre une mini interface Tkinter

Patchs simples disponibles:
- tabs       : remplace TAB par 4 espaces
- trim       : retire espaces de fin de ligne
- newline    : ajoute newline final si absent
- crlf       : convertit CRLF -> LF
- utf8       : réécrit un fichier texte en UTF-8 (best effort)
- quotes     : remplace guillemets typographiques par guillemets ASCII
- all-safe   : applique tabs + trim + newline + crlf + quotes

Exemples:
    python3 python_toolbox_v2.py check .
    python3 python_toolbox_v2.py grep . "session"
    python3 python_toolbox_v2.py patch . --mode all-safe
    python3 python_toolbox_v2.py report . --format json --output report.json
    python3 python_toolbox_v2.py gui

Ouverture éditeur:
    python3 python_toolbox_v2.py open /chemin/fichier.py --line 42 --editor nano
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import traceback
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterator

DEFAULT_EXCLUDES = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
}

TEXT_EXTENSIONS = {".py", ".pyw", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}


@dataclass
class MatchItem:
    file: str
    line: int
    text: str


@dataclass
class ErrorItem:
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
    encodings = ("utf-8", "utf-8-sig", "latin-1", "cp1252")
    for enc in encodings:
        try:
            return path.read_text(encoding=enc), enc
        except Exception:
            continue
    return None, None


def print_header(title: str) -> None:
    print(f"\n=== {title} ===")


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


def gather_scan(root: Path, include_all: bool = False) -> list[str]:
    return [rel(p, root) for p in sorted(iter_files(root, include_all=include_all))]


def gather_check(root: Path, include_all: bool = False) -> tuple[list[str], list[ErrorItem]]:
    ok = []
    errors = []
    for path in sorted(iter_files(root, include_all=include_all)):
        try:
            py_compile.compile(str(path), doraise=True)
            ok.append(rel(path, root))
        except py_compile.PyCompileError as exc:
            errors.append(ErrorItem(file=rel(path, root), message=exc.msg))
    return ok, errors


def gather_grep(root: Path, pattern: str, ignore_case: bool = False, include_all: bool = False) -> list[MatchItem]:
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern, flags)
    results = []
    for path in sorted(iter_files(root, include_all=include_all)):
        text, _ = safe_read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                results.append(MatchItem(file=rel(path, root), line=lineno, text=line.rstrip()))
    return results


def gather_todos(root: Path, include_all: bool = False) -> list[MatchItem]:
    pattern = re.compile(r"\b(TODO|FIXME|HACK|BUG|NOTE)\b", re.IGNORECASE)
    results = []
    for path in sorted(iter_files(root, include_all=include_all)):
        text, _ = safe_read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                results.append(MatchItem(file=rel(path, root), line=lineno, text=line.rstrip()))
    return results


def gather_longlines(root: Path, max_len: int = 88, include_all: bool = False) -> list[dict]:
    results = []
    for path in sorted(iter_files(root, include_all=include_all)):
        text, _ = safe_read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > max_len:
                results.append({"file": rel(path, root), "line": lineno, "length": len(line)})
    return results


def gather_utf8(root: Path, include_all: bool = False) -> tuple[list[str], list[ErrorItem]]:
    ok = []
    bad = []
    for path in sorted(iter_files(root, include_all=include_all)):
        try:
            path.read_text(encoding="utf-8")
            ok.append(rel(path, root))
        except Exception as exc:
            bad.append(ErrorItem(file=rel(path, root), message=str(exc)))
    return ok, bad


def gather_stats(root: Path, include_all: bool = False) -> dict:
    file_count = 0
    line_count = 0
    blank_count = 0
    comment_count = 0
    function_count = 0
    class_count = 0
    imports = Counter()

    for path in sorted(iter_files(root, include_all=include_all)):
        file_count += 1
        text, _ = safe_read_text(path)
        if text is None:
            continue

        for line in text.splitlines():
            line_count += 1
            stripped = line.strip()
            if not stripped:
                blank_count += 1
            elif stripped.startswith("#"):
                comment_count += 1

        function_count += len(re.findall(r"^\s*def\s+\w+\s*\(", text, flags=re.MULTILINE))
        class_count += len(re.findall(r"^\s*class\s+\w+\s*[:\(]", text, flags=re.MULTILINE))

        for match in re.findall(r"^\s*import\s+([a-zA-Z_][\w]*)", text, flags=re.MULTILINE):
            imports[match] += 1
        for match in re.findall(r"^\s*from\s+([a-zA-Z_][\w\.]*)\s+import\s+", text, flags=re.MULTILINE):
            imports[match.split(".")[0]] += 1

    return {
        "files_python": file_count,
        "lines_total": line_count,
        "lines_blank": blank_count,
        "lines_comments": comment_count,
        "functions": function_count,
        "classes": class_count,
        "top_imports": imports.most_common(20),
    }


def run_report(root: Path, include_all: bool = False, grep_pattern: str | None = None, grep_ignore_case: bool = False, max_len: int = 88) -> dict:
    ok, errors = gather_check(root, include_all=include_all)
    utf8_ok, utf8_bad = gather_utf8(root, include_all=include_all)
    report = {
        "generated_at": datetime.now().isoformat(),
        "root": str(root.resolve()),
        "scan": gather_scan(root, include_all=include_all),
        "syntax_check": {
            "ok_count": len(ok),
            "error_count": len(errors),
            "errors": [asdict(x) for x in errors],
        },
        "todos": [asdict(x) for x in gather_todos(root, include_all=include_all)],
        "longlines": gather_longlines(root, max_len=max_len, include_all=include_all),
        "utf8": {
            "ok_count": len(utf8_ok),
            "bad_count": len(utf8_bad),
            "bad_files": [asdict(x) for x in utf8_bad],
        },
        "stats": gather_stats(root, include_all=include_all),
    }
    if grep_pattern:
        report["grep"] = [asdict(x) for x in gather_grep(root, grep_pattern, ignore_case=grep_ignore_case, include_all=include_all)]
    return report


def render_report_text(report: dict) -> str:
    lines = []
    lines.append("=== PYTHON TOOLBOX REPORT ===")
    lines.append(f"Generated at: {report['generated_at']}")
    lines.append(f"Root: {report['root']}")
    lines.append("")
    lines.append(f"Python files: {len(report['scan'])}")
    lines.append("")
    lines.append("=== SYNTAX ===")
    lines.append(f"OK: {report['syntax_check']['ok_count']}")
    lines.append(f"Errors: {report['syntax_check']['error_count']}")
    for item in report["syntax_check"]["errors"]:
        lines.append(f"- {item['file']}: {item['message']}")
    lines.append("")
    lines.append("=== TODOS ===")
    for item in report["todos"][:300]:
        lines.append(f"- {item['file']}:{item['line']}: {item['text']}")
    lines.append("")
    lines.append("=== LONG LINES ===")
    for item in report["longlines"][:300]:
        lines.append(f"- {item['file']}:{item['line']} len={item['length']}")
    lines.append("")
    lines.append("=== UTF8 ===")
    lines.append(f"UTF-8 OK: {report['utf8']['ok_count']}")
    lines.append(f"UTF-8 bad: {report['utf8']['bad_count']}")
    for item in report["utf8"]["bad_files"]:
        lines.append(f"- {item['file']}: {item['message']}")
    lines.append("")
    lines.append("=== STATS ===")
    for k, v in report["stats"].items():
        lines.append(f"{k}: {v}")
    if "grep" in report:
        lines.append("")
        lines.append("=== GREP ===")
        for item in report["grep"][:300]:
            lines.append(f"- {item['file']}:{item['line']}: {item['text']}")
    return "\n".join(lines) + "\n"


def patch_text(text: str, mode: str) -> tuple[str, int]:
    changes = 0
    new = text

    if mode in {"tabs", "all-safe"}:
        count = new.count("\t")
        if count:
            new = new.replace("\t", "    ")
            changes += count

    if mode in {"trim", "all-safe"}:
        lines = new.splitlines(True)
        trimmed = []
        for line in lines:
            if line.endswith("\r\n"):
                core = line[:-2]
                nl = "\r\n"
            elif line.endswith("\n"):
                core = line[:-1]
                nl = "\n"
            else:
                core = line
                nl = ""
            new_core = core.rstrip(" \t")
            if new_core != core:
                changes += 1
            trimmed.append(new_core + nl)
        new = "".join(trimmed)

    if mode in {"crlf", "all-safe"}:
        count = new.count("\r\n")
        if count:
            new = new.replace("\r\n", "\n")
            changes += count

    if mode in {"quotes", "all-safe"}:
        replacements = {
            "“": '"', "”": '"', "„": '"',
            "‘": "'", "’": "'", "‚": "'",
            "—": "-", "–": "-",
            "\u00A0": " ",
        }
        for old, repl in replacements.items():
            count = new.count(old)
            if count:
                new = new.replace(old, repl)
                changes += count

    if mode in {"newline", "all-safe"}:
        if new and not new.endswith("\n"):
            new += "\n"
            changes += 1

    return new, changes


def patch_file(path: Path, mode: str, backup: bool = True) -> dict:
    text, encoding = safe_read_text(path)
    if text is None:
        return {"file": str(path), "changed": False, "changes": 0, "encoding": None, "error": "Unreadable text file"}

    original = text
    chosen_encoding = encoding or "utf-8"

    if mode == "utf8":
        new = original
        changes = 1 if chosen_encoding.lower() != "utf-8" else 0
    else:
        new, changes = patch_text(original, mode)

    if new != original or mode == "utf8":
        backup_path = str(write_backup(path)) if backup else None
        path.write_text(new, encoding="utf-8")
        return {
            "file": str(path),
            "changed": True,
            "changes": changes,
            "encoding_before": chosen_encoding,
            "encoding_after": "utf-8",
            "backup": backup_path,
        }

    return {
        "file": str(path),
        "changed": False,
        "changes": 0,
        "encoding_before": chosen_encoding,
        "encoding_after": chosen_encoding,
        "backup": None,
    }


def detect_patchable_targets(root: Path, include_all: bool = False) -> list[Path]:
    out = []
    for path in sorted(iter_files(root, include_all=include_all)):
        if path.suffix in TEXT_EXTENSIONS or path.suffix == ".py":
            out.append(path)
    return out


def open_in_editor(path: Path, line: int | None = None, editor: str | None = None) -> int:
    editor = editor or os.environ.get("EDITOR") or "nano"
    editors = {
        "nano": lambda p, l: ["nano", f"+{l}" if l else "+1", str(p)],
        "vim": lambda p, l: ["vim", f"+{l}" if l else "+1", str(p)],
        "nvim": lambda p, l: ["nvim", f"+{l}" if l else "+1", str(p)],
        "code": lambda p, l: ["code", "-g", f"{p}:{l or 1}"],
        "codium": lambda p, l: ["codium", "-g", f"{p}:{l or 1}"],
        "gedit": lambda p, l: ["gedit", str(p)],
        "mousepad": lambda p, l: ["mousepad", str(p)],
        "xed": lambda p, l: ["xed", str(p)],
    }

    launcher = editors.get(editor)
    if launcher is None:
        cmd = [editor, str(path)]
    else:
        cmd = launcher(path, line)

    try:
        subprocess.run(cmd, check=False)
        return 0
    except FileNotFoundError:
        print(f"[ERREUR] Éditeur introuvable: {editor}")
        return 1


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"SCAN PROJECT: {root}")
    for item in gather_scan(root, include_all=args.all):
        depth = max(0, len(Path(item).parts) - 1)
        print(f"{'  ' * depth}- {item}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"SYNTAX CHECK: {root}")
    ok, errors = gather_check(root, include_all=args.all)
    if args.verbose:
        for item in ok:
            print(f"[OK] {item}")
    for item in errors:
        print(f"[ERROR] {item.file}")
        print(item.message)
    print(f"\nRésumé: {len(ok)} OK | {len(errors)} erreur(s)")
    return 1 if errors else 0


def cmd_grep(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"GREP: {args.pattern!r} in {root}")
    results = gather_grep(root, args.pattern, ignore_case=args.ignore_case, include_all=args.all)
    for item in results:
        print(f"{item.file}:{item.line}: {item.text}")
    print(f"\nRésultat: {len(results)} match(s)")
    return 0 if results else 1


def cmd_todos(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"TODOS: {root}")
    results = gather_todos(root, include_all=args.all)
    for item in results:
        print(f"{item.file}:{item.line}: {item.text}")
    print(f"\nRésultat: {len(results)} note(s)")
    return 0 if results else 1


def cmd_longlines(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"LONG LINES > {args.max_len}: {root}")
    results = gather_longlines(root, max_len=args.max_len, include_all=args.all)
    for item in results:
        print(f"{item['file']}:{item['line']}: len={item['length']}")
    print(f"\nRésultat: {len(results)} ligne(s) trop longues")
    return 0 if results else 1


def cmd_utf8(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"UTF-8 CHECK: {root}")
    ok, bad = gather_utf8(root, include_all=args.all)
    if args.verbose:
        for item in ok:
            print(f"[UTF-8 OK] {item}")
    for item in bad:
        print(f"[NOT UTF-8] {item.file} -> {item.message}")
    print(f"\nRésumé: {len(ok)} OK | {len(bad)} non UTF-8")
    return 1 if bad else 0


def cmd_stats(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"STATS: {root}")
    stats = gather_stats(root, include_all=args.all)
    for key in ("files_python", "lines_total", "lines_blank", "lines_comments", "functions", "classes"):
        print(f"{key}: {stats[key]}")
    print("\nTop imports:")
    for name, count in stats["top_imports"]:
        print(f"  - {name}: {count}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    report = run_report(
        root,
        include_all=args.all,
        grep_pattern=args.grep_pattern,
        grep_ignore_case=args.ignore_case,
        max_len=args.max_len,
    )

    output = Path(args.output).resolve() if args.output else None
    if args.format == "json":
        content = json.dumps(report, indent=2, ensure_ascii=False)
    else:
        content = render_report_text(report)

    if output:
        output.write_text(content, encoding="utf-8")
        print(f"[OK] Rapport écrit: {output}")
    else:
        print(content)
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"PATCH MODE={args.mode}: {root}")
    files = detect_patchable_targets(root, include_all=args.all)
    changed = 0
    total_changes = 0

    for path in files:
        result = patch_file(path, args.mode, backup=not args.no_backup)
        if result.get("changed"):
            changed += 1
            total_changes += int(result.get("changes", 0))
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
        from tkinter import filedialog, ttk, messagebox
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        print(f"[ERREUR] Tkinter indisponible: {exc}")
        return 1

    class App:
        def __init__(self, root_window: "tk.Tk") -> None:
            self.root = root_window
            self.root.title("Python Toolbox V2")
            self.root.geometry("1100x760")

            top = ttk.Frame(self.root, padding=8)
            top.pack(fill="x")

            self.path_var = tk.StringVar(value=str(Path.cwd()))
            self.pattern_var = tk.StringVar(value="session")
            self.max_len_var = tk.StringVar(value="88")
            self.editor_var = tk.StringVar(value=os.environ.get("EDITOR", "nano"))

            ttk.Label(top, text="Projet").grid(row=0, column=0, sticky="w")
            ttk.Entry(top, textvariable=self.path_var, width=90).grid(row=0, column=1, sticky="ew", padx=6)
            ttk.Button(top, text="Browse", command=self.pick_dir).grid(row=0, column=2, padx=4)

            ttk.Label(top, text="Pattern").grid(row=1, column=0, sticky="w")
            ttk.Entry(top, textvariable=self.pattern_var, width=30).grid(row=1, column=1, sticky="w", padx=6)

            ttk.Label(top, text="Max len").grid(row=1, column=2, sticky="e")
            ttk.Entry(top, textvariable=self.max_len_var, width=8).grid(row=1, column=3, sticky="w", padx=6)

            ttk.Label(top, text="Editor").grid(row=1, column=4, sticky="e")
            ttk.Entry(top, textvariable=self.editor_var, width=12).grid(row=1, column=5, sticky="w", padx=6)

            top.columnconfigure(1, weight=1)

            btns = ttk.Frame(self.root, padding=(8, 0, 8, 8))
            btns.pack(fill="x")

            actions = [
                ("Scan", self.do_scan),
                ("Check", self.do_check),
                ("Grep", self.do_grep),
                ("Todos", self.do_todos),
                ("Long lines", self.do_longlines),
                ("UTF-8", self.do_utf8),
                ("Stats", self.do_stats),
                ("Patch safe", self.do_patch_safe),
                ("Export TXT", self.do_export_txt),
                ("Export JSON", self.do_export_json),
            ]
            for i, (label, cb) in enumerate(actions):
                ttk.Button(btns, text=label, command=cb).grid(row=0, column=i, padx=3, pady=3)

            mid = ttk.Panedwindow(self.root, orient="horizontal")
            mid.pack(fill="both", expand=True, padx=8, pady=8)

            left = ttk.Frame(mid, padding=4)
            right = ttk.Frame(mid, padding=4)
            mid.add(left, weight=1)
            mid.add(right, weight=3)

            ttk.Label(left, text="Résultats cliquables").pack(anchor="w")
            self.listbox = tk.Listbox(left, exportselection=False)
            self.listbox.pack(fill="both", expand=True)
            self.listbox.bind("<Double-Button-1>", self.open_selected)

            controls = ttk.Frame(left)
            controls.pack(fill="x", pady=4)
            ttk.Button(controls, text="Open selected", command=self.open_selected).pack(side="left", padx=2)
            ttk.Button(controls, text="Clear", command=self.clear_all).pack(side="left", padx=2)

            ttk.Label(right, text="Sortie / rapport").pack(anchor="w")
            self.output = ScrolledText(right, wrap="word")
            self.output.pack(fill="both", expand=True)

            self.items: list[tuple[str, int | None]] = []

        def get_root(self) -> Path:
            return Path(self.path_var.get()).expanduser().resolve()

        def log(self, text: str) -> None:
            self.output.insert("end", text + "\n")
            self.output.see("end")

        def set_output(self, text: str) -> None:
            self.output.delete("1.0", "end")
            self.output.insert("1.0", text)

        def clear_all(self) -> None:
            self.listbox.delete(0, "end")
            self.output.delete("1.0", "end")
            self.items.clear()

        def pick_dir(self) -> None:
            chosen = filedialog.askdirectory(initialdir=self.path_var.get() or str(Path.cwd()))
            if chosen:
                self.path_var.set(chosen)

        def push_clickable(self, file: str, line: int | None, label: str) -> None:
            self.items.append((file, line))
            self.listbox.insert("end", label)

        def fill_clickable_from_matches(self, matches: list[MatchItem]) -> None:
            self.listbox.delete(0, "end")
            self.items.clear()
            for item in matches:
                self.push_clickable(item.file, item.line, f"{item.file}:{item.line}")

        def open_selected(self, _event=None) -> None:
            sel = self.listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            file, line = self.items[idx]
            project = self.get_root()
            target = (project / file).resolve() if not Path(file).is_absolute() else Path(file)
            rc = open_in_editor(target, line=line, editor=self.editor_var.get().strip() or "nano")
            if rc != 0:
                messagebox.showerror("Erreur", "Impossible d'ouvrir le fichier dans l'éditeur.")

        def do_scan(self) -> None:
            root = self.get_root()
            items = gather_scan(root)
            self.listbox.delete(0, "end")
            self.items.clear()
            for item in items:
                self.push_clickable(item, None, item)
            self.set_output("\n".join(items) if items else "(aucun résultat)")

        def do_check(self) -> None:
            root = self.get_root()
            ok, errors = gather_check(root)
            self.listbox.delete(0, "end")
            self.items.clear()
            lines = [f"OK: {len(ok)}", f"Errors: {len(errors)}", ""]
            for item in errors:
                lines.append(f"[ERROR] {item.file}")
                lines.append(item.message)
                lines.append("")
                m = re.search(r"line (\d+)", item.message)
                guessed_line = int(m.group(1)) if m else None
                self.push_clickable(item.file, guessed_line, f"{item.file}:{guessed_line or '?'}")
            self.set_output("\n".join(lines))

        def do_grep(self) -> None:
            root = self.get_root()
            matches = gather_grep(root, self.pattern_var.get())
            self.fill_clickable_from_matches(matches)
            self.set_output("\n".join(f"{m.file}:{m.line}: {m.text}" for m in matches) if matches else "(aucun match)")

        def do_todos(self) -> None:
            root = self.get_root()
            matches = gather_todos(root)
            self.fill_clickable_from_matches(matches)
            self.set_output("\n".join(f"{m.file}:{m.line}: {m.text}" for m in matches) if matches else "(aucun TODO/FIXME/HACK/BUG/NOTE)")

        def do_longlines(self) -> None:
            root = self.get_root()
            try:
                max_len = int(self.max_len_var.get().strip())
            except ValueError:
                messagebox.showerror("Erreur", "Max len doit être un entier.")
                return
            results = gather_longlines(root, max_len=max_len)
            self.listbox.delete(0, "end")
            self.items.clear()
            for item in results:
                self.push_clickable(item["file"], item["line"], f"{item['file']}:{item['line']}")
            self.set_output("\n".join(f"{x['file']}:{x['line']} len={x['length']}" for x in results) if results else "(aucune ligne trop longue)")

        def do_utf8(self) -> None:
            root = self.get_root()
            ok, bad = gather_utf8(root)
            self.listbox.delete(0, "end")
            self.items.clear()
            for item in bad:
                self.push_clickable(item.file, None, item.file)
            text = [f"UTF-8 OK: {len(ok)}", f"UTF-8 bad: {len(bad)}", ""]
            text.extend(f"{x.file}: {x.message}" for x in bad)
            self.set_output("\n".join(text))

        def do_stats(self) -> None:
            root = self.get_root()
            stats = gather_stats(root)
            lines = []
            for k, v in stats.items():
                lines.append(f"{k}: {v}")
            self.set_output("\n".join(lines))
            self.listbox.delete(0, "end")
            self.items.clear()

        def do_patch_safe(self) -> None:
            root = self.get_root()
            files = detect_patchable_targets(root)
            changed_lines = []
            patched_files = []
            for path in files:
                result = patch_file(path, "all-safe", backup=True)
                if result.get("changed"):
                    p = rel(Path(result["file"]), root)
                    patched_files.append(p)
                    changed_lines.append(f"{p} | changes={result['changes']} | backup={result['backup']}")
            self.listbox.delete(0, "end")
            self.items.clear()
            for file in patched_files:
                self.push_clickable(file, None, file)
            self.set_output("\n".join(changed_lines) if changed_lines else "(aucun changement)")

        def do_export_txt(self) -> None:
            root = self.get_root()
            report = run_report(root, grep_pattern=self.pattern_var.get(), max_len=int(self.max_len_var.get() or "88"))
            content = render_report_text(report)
            output = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
            if output:
                Path(output).write_text(content, encoding="utf-8")
                messagebox.showinfo("OK", f"Rapport écrit:\n{output}")

        def do_export_json(self) -> None:
            root = self.get_root()
            report = run_report(root, grep_pattern=self.pattern_var.get(), max_len=int(self.max_len_var.get() or "88"))
            output = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
            if output:
                Path(output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                messagebox.showinfo("OK", f"Rapport écrit:\n{output}")

    root = tk.Tk()
    app = App(root)
    root.mainloop()
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    return launch_gui()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python Toolbox V2 - audit, patch simple, rapport, mini UI.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("path", help="Chemin du projet à analyser")
    common.add_argument("--all", action="store_true", help="Inclure aussi les dossiers normalement exclus")

    p_scan = sub.add_parser("scan", parents=[common], help="Afficher les fichiers Python du projet")
    p_scan.set_defaults(func=cmd_scan)

    p_check = sub.add_parser("check", parents=[common], help="Détecter les erreurs de syntaxe Python")
    p_check.add_argument("-v", "--verbose", action="store_true", help="Afficher chaque fichier OK")
    p_check.set_defaults(func=cmd_check)

    p_grep = sub.add_parser("grep", parents=[common], help="Chercher un texte/regex dans les fichiers Python")
    p_grep.add_argument("pattern", help="Motif regex à chercher")
    p_grep.add_argument("-i", "--ignore-case", action="store_true", help="Ignorer la casse")
    p_grep.set_defaults(func=cmd_grep)

    p_todos = sub.add_parser("todos", parents=[common], help="Trouver TODO/FIXME/HACK/BUG/NOTE")
    p_todos.set_defaults(func=cmd_todos)

    p_long = sub.add_parser("longlines", parents=[common], help="Trouver les lignes trop longues")
    p_long.add_argument("--max-len", type=int, default=88, help="Longueur max (défaut: 88)")
    p_long.set_defaults(func=cmd_longlines)

    p_utf8 = sub.add_parser("utf8", parents=[common], help="Vérifier quels fichiers ne sont pas en UTF-8")
    p_utf8.add_argument("-v", "--verbose", action="store_true", help="Afficher chaque fichier OK")
    p_utf8.set_defaults(func=cmd_utf8)

    p_stats = sub.add_parser("stats", parents=[common], help="Statistiques du projet Python")
    p_stats.set_defaults(func=cmd_stats)

    p_report = sub.add_parser("report", parents=[common], help="Exporter un rapport TXT ou JSON")
    p_report.add_argument("--format", choices=("txt", "json"), default="txt", help="Format du rapport")
    p_report.add_argument("--output", help="Fichier de sortie")
    p_report.add_argument("--grep-pattern", help="Motif de grep à inclure dans le rapport")
    p_report.add_argument("-i", "--ignore-case", action="store_true", help="Ignorer la casse pour grep")
    p_report.add_argument("--max-len", type=int, default=88, help="Longueur max pour longlines")
    p_report.set_defaults(func=cmd_report)

    p_patch = sub.add_parser("patch", parents=[common], help="Appliquer des patchs simples et sûrs")
    p_patch.add_argument("--mode", choices=("tabs", "trim", "newline", "crlf", "utf8", "quotes", "all-safe"), default="all-safe")
    p_patch.add_argument("--no-backup", action="store_true", help="Ne pas créer de backup")
    p_patch.add_argument("-v", "--verbose", action="store_true", help="Afficher aussi les fichiers inchangés")
    p_patch.set_defaults(func=cmd_patch)

    p_open = sub.add_parser("open", help="Ouvrir un fichier dans un éditeur à une ligne précise")
    p_open.add_argument("file", help="Chemin du fichier")
    p_open.add_argument("--line", type=int, default=1, help="Numéro de ligne")
    p_open.add_argument("--editor", help="Éditeur à utiliser (nano, vim, code, codium, gedit, etc.)")
    p_open.set_defaults(func=cmd_open)

    p_gui = sub.add_parser("gui", help="Ouvrir la mini interface Tkinter")
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
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
