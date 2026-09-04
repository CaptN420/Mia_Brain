#!/usr/bin/env python3
"""
imports.py — Analyse et optimisation des imports Python.

Économise des tokens LLM : remplace "quelle import manque ?" ou
"cette import est-elle utilisée ?" par un check déterministe.

Usage:
    python -m tools.imports mon_fichier.py
    python -m tools.imports mon_fichier.py --fix
    python -m tools.imports /chemin/projet --json
    python -m tools.imports mon_fichier.py --unused-only
"""

import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Stdlib modules (connus)
STDLIB_MODULES = {
    "abc", "ast", "asyncio", "base64", "collections", "copy", "csv", "datetime",
    "decimal", "enum", "functools", "glob", "hashlib", "heapq", "html", "http",
    "importlib", "inspect", "io", "itertools", "json", "logging", "math", "multiprocessing",
    "operator", "os", "pathlib", "pickle", "platform", "pprint", "queue", "random",
    "re", "shutil", "signal", "socket", "sqlite3", "statistics", "string", "struct",
    "subprocess", "sys", "tempfile", "textwrap", "threading", "time", "traceback",
    "typing", "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
}


class ImportInfo:
    def __init__(self, lineno: int, col_offset: int):
        self.lineno = lineno
        self.col_offset = col_offset
        self.module = ""
        self.names: List[str] = []
        self.is_from = False
        self.alias: Dict[str, str] = {}
        self.used: bool = False

    def to_dict(self):
        return {
            "lineno": self.lineno,
            "module": self.module,
            "names": self.names,
            "is_from": self.is_from,
            "alias": self.alias,
            "used": self.used,
            "category": self.categorize(),
        }

    def categorize(self) -> str:
        if self.module in STDLIB_MODULES or self.module.split(".")[0] in STDLIB_MODULES:
            return "stdlib"
        return "third-party"


def analyze_imports(source: str, filename: str = "<unknown>") -> Dict:
    """Analyse les imports et leur utilisation effective."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": str(e), "filename": filename}

    imports: List[ImportInfo] = []
    used_names: Set[str] = set()
    defined_names: Set[str] = set()
    import_linenos: Dict[int, ImportInfo] = {}

    # First pass: collect imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                ii = ImportInfo(node.lineno, node.col_offset)
                ii.module = alias.name
                ii.names = [alias.asname or alias.name.split(".")[0]]
                ii.alias = {alias.name: alias.asname or alias.name.split(".")[0]}
                imports.append(ii)
                import_linenos[node.lineno] = ii
                defined_names.add(alias.asname or alias.name.split(".")[0])

        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue  # from . import ...
            ii = ImportInfo(node.lineno, node.col_offset)
            ii.module = node.module
            ii.is_from = True
            for alias in node.names:
                name = alias.asname or alias.name
                ii.names.append(name)
                ii.alias[alias.name] = alias.asname or alias.name
                defined_names.add(name)
            imports.append(ii)
            import_linenos[node.lineno] = ii

    # Second pass: find all name references (except in imports)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Name):
            used_names.add(node.id)

    # Mark unused imports
    unused = []
    for ii in imports:
        imported_names = set(ii.alias.values())
        # Names used in the code
        actually_used = imported_names & used_names
        # Also check: is the module itself used as a qualified name (module.func)?
        module_name = ii.module.split(".")[0]
        if module_name in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and
                           not isinstance(getattr(find_parent(tree, n), 'ctx', None), ast.Store)}:
            actually_used.add(module_name)
        ii.used = bool(actually_used)
        if not actually_used:
            unused.append(ii)

    return {
        "filename": filename,
        "total_imports": len(imports),
        "unused_count": len(unused),
        "stdlib_count": sum(1 for i in imports if i.categorize() == "stdlib"),
        "third_party_count": sum(1 for i in imports if i.categorize() == "third-party"),
        "imports": [i.to_dict() for i in imports],
        "unused": [i.to_dict() for i in unused],
    }


def find_parent(tree: ast.AST, node: ast.AST) -> Optional[ast.AST]:
    """Trouve le noeud parent dans l'arbre."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if child is node:
                return parent
    return None


def remove_unused_imports(source: str, unused: List[ImportInfo]) -> str:
    """Supprime les lignes d'import inutilisées."""
    lines = source.splitlines(keepends=True)
    # Collect line numbers to remove (reverse order to preserve indices)
    remove_lines = sorted(set(ii.lineno for ii in unused), reverse=True)
    for lineno in remove_lines:
        if 0 <= lineno - 1 < len(lines):
            lines.pop(lineno - 1)
    return "".join(lines)


def cmd_imports(args):
    if os.path.isdir(args.target):
        # Scan directory
        results = {}
        for py_file in sorted(Path(args.target).rglob("*.py")):
            try:
                source = py_file.read_text(encoding="utf-8")
                results[str(py_file)] = analyze_imports(source, str(py_file))
            except Exception as e:
                results[str(py_file)] = {"error": str(e)}
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            total_unused = 0
            for fname, r in results.items():
                if "error" in r:
                    continue
                if r["unused_count"]:
                    print(f"📄 {fname}")
                    for u in r["unused"]:
                        print(f"   L.{u['lineno']:4d}  {u['module']:30s} {'| from ' + str(u['names']) if u['is_from'] else ''}")
                    total_unused += r["unused_count"]
                    print()
            print(f"📊 Total: {len(results)} fichiers, {total_unused} imports inutilisé(s)")
        return

    # Single file
    source = Path(args.target).read_text(encoding="utf-8")
    result = analyze_imports(source, args.target)

    if args.unused_only:
        if result["unused"]:
            for u in result["unused"]:
                print(f"L.{u['lineno']}  {u['module']}  {' → ' + ', '.join(u['names']) if u['is_from'] else ''}")
        else:
            print("✅ Aucun import inutilisé.")
        return

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(f"📄 {args.target}")
    print(f"   Total imports : {result['total_imports']} (stdlib: {result['stdlib_count']}, 3rd-party: {result['third_party_count']})")
    print(f"   Inutilisés    : {result['unused_count']}")
    if result["unused"]:
        print("\n  ❌ Imports inutilisés:")
        for u in result["unused"]:
            names = ", ".join(u["names"])
            print(f"     L.{u['lineno']:4d}  {u['module']:30s}  → {names}")

    if args.fix and result["unused"]:
        unused_objs = []
        for u in result["unused"]:
            ii = ImportInfo(u["lineno"], 0)
            ii.module = u["module"]
            ii.names = u["names"]
            ii.is_from = u["is_from"]
            unused_objs.append(ii)
        cleaned = remove_unused_imports(source, unused_objs)
        if args.output:
            Path(args.output).write_text(cleaned, encoding="utf-8")
            print(f"\n✅ Fichier nettoyé écrit dans {args.output}")
        else:
            print(cleaned)


def register_cli(subparsers):
    """Register this tool as a CLI subcommand."""
    p = subparsers.add_parser("imports", help="Analyse et optimisation des imports Python")
    p.add_argument("target", help="Fichier ou dossier")
    p.add_argument("--json", "-j", action="store_true", help="Sortie JSON")
    p.add_argument("--unused-only", "-u", action="store_true", help="Afficher seulement les inutilisés")
    p.add_argument("--fix", action="store_true", help="Supprimer les imports inutilisés")
    p.add_argument("--output", "-o", help="Fichier de sortie (avec --fix)")
    p.set_defaults(func=cmd_imports)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Analyse et optimise les imports Python")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()