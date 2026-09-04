#!/usr/bin/env python3
"""
codechunk.py — Découpe un fichier Python en chunks fonction/classe.

Économise des tokens LLM : au lieu d'envoyer un fichier entier,
envoyez seulement le chunk qui concerne la fonction/classe modifiée.

Usage:
    python -m tools.codechunk mon_fichier.py
    python -m tools.codechunk mon_fichier.py --function foo
    python -m tools.codechunk mon_fichier.py --line 42 --context 3
    python -m tools.codechunk mon_fichier.py --json
"""

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_source_lines(source: str) -> List[str]:
    return source.splitlines(keepends=True)


def extract_chunks(source: str, filename: str = "<unknown>") -> List[Dict[str, Any]]:
    """Découpe le code en chunks par fonction/classe."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{"error": str(e), "filename": filename}]

    lines = get_source_lines(source)
    chunks = []
    # Preamble: imports, module docstring, top-level assignments before first def/class
    first_def_line = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first_def_line = node.lineno
            break

    if first_def_line and first_def_line > 1:
        preamble = "".join(lines[: first_def_line - 1])
        if preamble.strip():
            chunks.append({
                "kind": "preamble",
                "name": "<module>",
                "start_line": 1,
                "end_line": first_def_line - 1,
                "code": preamble,
            })

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunk = {
                "kind": "function",
                "name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "code": "".join(lines[node.lineno - 1 : node.end_lineno]),
                "decorators": [d.id for d in node.decorator_list if isinstance(d, ast.Name)],
            }
            chunks.append(chunk)

        elif isinstance(node, ast.ClassDef):
            class_chunk = {
                "kind": "class",
                "name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "bases": [b.id for b in node.bases if isinstance(b, ast.Name)],
                "code": "".join(lines[node.lineno - 1 : node.end_lineno]),
                "methods": [],
            }
            # Extract methods as sub-chunks
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_chunk["methods"].append({
                        "name": item.name,
                        "start_line": item.lineno,
                        "end_line": item.end_lineno,
                    })
            chunks.append(class_chunk)

    return chunks


def filter_chunk(chunks: List[Dict], line: Optional[int] = None,
                 name: Optional[str] = None) -> Optional[Dict]:
    """Trouve le chunk contenant une ligne ou un nom."""
    if name:
        for c in chunks:
            if c.get("name") == name:
                return c
            if c["kind"] == "class" and any(m["name"] == name for m in c.get("methods", [])):
                return c
        return None

    if line:
        for c in chunks:
            if c.get("start_line", 0) <= line <= c.get("end_line", 0):
                return c
        return None

    return None


def context_around(source: str, line: int, context_lines: int = 2) -> str:
    """Extrait N lignes de contexte autour d'une ligne donnée."""
    lines = source.splitlines(keepends=True)
    start = max(0, line - 1 - context_lines)
    end = min(len(lines), line + context_lines)
    return "".join(lines[start:end])


def cmd_codechunk(args):
    if args.file:
        source = Path(args.file).read_text(encoding="utf-8")
    else:
        source = sys.stdin.read()

    chunks = extract_chunks(source, args.file or "<stdin>")

    # Filter by function/class name
    if args.function:
        filtered = [c for c in chunks if c.get("name") == args.function]
        if filtered:
            chunks = filtered
        else:
            print(f"⚠ Fonction/classe '{args.function}' introuvable")
            sys.exit(1)

    # Filter by line
    if args.line:
        chunk = filter_chunk(chunks, line=args.line)
        if chunk:
            ctx = context_around(chunk["code"], chunk["end_line"] - chunk["start_line"] + 1, args.context)
            if args.json:
                print(json.dumps({**chunk, "context": ctx}, indent=2, ensure_ascii=False))
            else:
                print(f"# {chunk['kind'].upper()} : {chunk['name']} (l.{chunk['start_line']}-{chunk['end_line']})")
                print(f"# {'='*50}")
                print(chunk["code"])
                print(f"# Contexte ({args.context} lignes):")
                print(ctx)
        else:
            print(f"⚠ Aucun chunk trouvé à la ligne {args.line}")
            sys.exit(1)
        return

    if args.json:
        print(json.dumps(chunks, indent=2, ensure_ascii=False))
    else:
        for c in chunks:
            label = f"{c['kind'].upper()} : {c['name']}"
            print(f"{'─'*50}")
            print(f"  {label}")
            print(f"  Lignes {c['start_line']}-{c['end_line']} ({c.get('end_line', c['start_line']) - c['start_line'] + 1} lignes)")
            if c['kind'] == 'function' and c.get('decorators'):
                print(f"  Décorateurs: {', '.join(c['decorators'])}")
            if c['kind'] == 'class':
                bases = c.get('bases', [])
                if bases:
                    print(f"  Hérite de: {', '.join(bases)}")
                print(f"  Méthodes: {', '.join(m['name'] for m in c.get('methods', []))}")
            print()


def register_cli(subparsers):
    """Register this tool as a CLI subcommand."""
    p = subparsers.add_parser("codechunk", help="Découper un fichier en chunks fonction/classe")
    p.add_argument("file", nargs="?", help="Fichier source")
    p.add_argument("--function", "-f", help="Filtrer par nom de fonction/classe")
    p.add_argument("--line", "-l", type=int, help="Filtrer par numéro de ligne")
    p.add_argument("--context", "-c", type=int, default=2, help="Lignes de contexte (défaut: 2)")
    p.add_argument("--json", "-j", action="store_true", help="Sortie JSON")
    p.set_defaults(func=cmd_codechunk)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Découpe un fichier Python en chunks")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()