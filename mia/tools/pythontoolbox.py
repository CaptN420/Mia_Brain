#!/usr/bin/env python3
"""
python_toolbox.py
Boîte à outils simple et utile pour coder en Python.

Fonctions incluses:
- scan: affiche l'arborescence d'un projet Python
- check: compile les .py pour détecter les erreurs de syntaxe
- grep: cherche un mot/texte dans les fichiers .py
- todos: liste TODO / FIXME / HACK / BUG
- longlines: trouve les lignes trop longues
- utf8: teste si les fichiers sont lisibles en UTF-8
- stats: donne quelques stats sur le projet

Exemples:
    python3 python_toolbox.py scan .
    python3 python_toolbox.py check .
    python3 python_toolbox.py grep . "session"
    python3 python_toolbox.py todos .
    python3 python_toolbox.py longlines . --max-len 100
    python3 python_toolbox.py utf8 .
    python3 python_toolbox.py stats .
"""

from __future__ import annotations

import argparse
import os
import py_compile
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator


DEFAULT_EXCLUDES = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
}


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


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"SCAN PROJECT: {root}")

    for path in sorted(iter_files(root, include_all=args.all)):
        rel = path.relative_to(root)
        depth = len(rel.parts) - 1
        indent = "  " * depth
        print(f"{indent}- {rel}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"SYNTAX CHECK: {root}")

    ok_count = 0
    err_count = 0

    for path in sorted(iter_files(root, include_all=args.all)):
        try:
            py_compile.compile(str(path), doraise=True)
            ok_count += 1
            if args.verbose:
                print(f"[OK] {path.relative_to(root)}")
        except py_compile.PyCompileError as exc:
            err_count += 1
            print(f"[ERROR] {path.relative_to(root)}")
            print(exc.msg)

    print(f"\nRésumé: {ok_count} OK | {err_count} erreur(s)")
    return 1 if err_count else 0


def cmd_grep(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    pattern = args.pattern
    flags = re.IGNORECASE if args.ignore_case else 0
    regex = re.compile(pattern, flags)

    print_header(f"GREP: {pattern!r} in {root}")
    found = 0

    for path in sorted(iter_files(root, include_all=args.all)):
        text, _enc = safe_read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                found += 1
                print(f"{path.relative_to(root)}:{lineno}: {line.rstrip()}")

    print(f"\nRésultat: {found} match(s)")
    return 0 if found else 1


def cmd_todos(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    pattern = re.compile(r"\b(TODO|FIXME|HACK|BUG|NOTE)\b", re.IGNORECASE)

    print_header(f"TODOS: {root}")
    found = 0

    for path in sorted(iter_files(root, include_all=args.all)):
        text, _enc = safe_read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                found += 1
                print(f"{path.relative_to(root)}:{lineno}: {line.rstrip()}")

    print(f"\nRésultat: {found} note(s)")
    return 0 if found else 1


def cmd_longlines(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    limit = args.max_len

    print_header(f"LONG LINES > {limit}: {root}")
    found = 0

    for path in sorted(iter_files(root, include_all=args.all)):
        text, _enc = safe_read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > limit:
                found += 1
                print(f"{path.relative_to(root)}:{lineno}: len={len(line)}")

    print(f"\nRésultat: {found} ligne(s) trop longues")
    return 0 if found else 1


def cmd_utf8(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()

    print_header(f"UTF-8 CHECK: {root}")
    ok = 0
    bad = 0

    for path in sorted(iter_files(root, include_all=args.all)):
        try:
            path.read_text(encoding="utf-8")
            ok += 1
            if args.verbose:
                print(f"[UTF-8 OK] {path.relative_to(root)}")
        except Exception as exc:
            bad += 1
            print(f"[NOT UTF-8] {path.relative_to(root)} -> {exc}")

    print(f"\nRésumé: {ok} OK | {bad} non UTF-8")
    return 1 if bad else 0


def cmd_stats(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    print_header(f"STATS: {root}")

    file_count = 0
    line_count = 0
    blank_count = 0
    comment_count = 0
    function_count = 0
    class_count = 0
    imports = Counter()

    for path in sorted(iter_files(root, include_all=args.all)):
        file_count += 1
        text, _enc = safe_read_text(path)
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

    print(f"Fichiers Python : {file_count}")
    print(f"Lignes totales  : {line_count}")
    print(f"Lignes vides    : {blank_count}")
    print(f"Commentaires    : {comment_count}")
    print(f"Fonctions       : {function_count}")
    print(f"Classes         : {class_count}")

    print("\nTop imports:")
    for name, count in imports.most_common(15):
        print(f"  - {name}: {count}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boîte à outils utile pour coder en Python.")
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
