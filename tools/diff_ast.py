#!/usr/bin/env python3
"""
diff_ast.py — Diff structurel AST entre deux codes Python.

Compare la structure du code (fonctions, classes, signatures) en
ignorant le formatage, les commentaires, la position exacte. Montre
le vrai changement sémantique.

Usage:
    python -m tools.diff_ast ancien.py nouveau.py
    python -m tools.diff_ast -c "def foo(x): pass" -C "def foo(x, y): pass"
    python -m tools.diff_ast ancien.py nouveau.py --json
"""

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class ASTDiffer:
    """Compare deux AST et produit un diff structurel."""

    def __init__(self, source_a: str, source_b: str,
                 name_a: str = "<old>", name_b: str = "<new>"):
        self.source_a = source_a
        self.source_b = source_b
        self.name_a = name_a
        self.name_b = name_b
        self.tree_a = None
        self.tree_b = None
        self.error = None

        try:
            self.tree_a = ast.parse(source_a)
        except SyntaxError as e:
            self.tree_a = None
            self.error_a = str(e)

        try:
            self.tree_b = ast.parse(source_b)
        except SyntaxError as e:
            self.tree_b = None
            self.error_b = str(e)

    def diff(self) -> Dict:
        if self.error:
            return {"error": self.error}

        if self.tree_a is None and self.tree_b is None:
            return {"error": "Both files have syntax errors"}
        if self.tree_a is None:
            return {"error": f"File {self.name_a} has syntax error"}
        if self.tree_b is None:
            return {"error": f"File {self.name_b} has syntax error"}

        changes = []

        # Compare top-level functions
        funcs_a = {n.name: n for n in ast.iter_child_nodes(self.tree_a)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        funcs_b = {n.name: n for n in ast.iter_child_nodes(self.tree_b)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

        # Functions added
        for name in funcs_b:
            if name not in funcs_a:
                changes.append({
                    "type": "added",
                    "kind": "function",
                    "name": name,
                    "detail": f"Function '{name}' added (line {funcs_b[name].lineno})",
                })

        # Functions removed
        for name in funcs_a:
            if name not in funcs_b:
                changes.append({
                    "type": "removed",
                    "kind": "function",
                    "name": name,
                    "detail": f"Function '{name}' removed",
                })

        # Functions modified (signature)
        for name in funcs_a:
            if name in funcs_b:
                fa, fb = funcs_a[name], funcs_b[name]
                sig_changes = self._compare_function_sig(fa, fb)
                if sig_changes:
                    changes.append({
                        "type": "modified",
                        "kind": "function",
                        "name": name,
                        "detail": f"Function '{name}' modified: {'; '.join(sig_changes)}",
                        "changes": sig_changes,
                    })

        # Compare classes
        classes_a = {n.name: n for n in ast.iter_child_nodes(self.tree_a)
                     if isinstance(n, ast.ClassDef)}
        classes_b = {n.name: n for n in ast.iter_child_nodes(self.tree_b)
                     if isinstance(n, ast.ClassDef)}

        for name in classes_b:
            if name not in classes_a:
                changes.append({
                    "type": "added",
                    "kind": "class",
                    "name": name,
                    "detail": f"Class '{name}' added (line {classes_b[name].lineno})",
                })

        for name in classes_a:
            if name not in classes_b:
                changes.append({
                    "type": "removed",
                    "kind": "class",
                    "name": name,
                    "detail": f"Class '{name}' removed",
                })

        for name in classes_a:
            if name in classes_b:
                ca, cb = classes_a[name], classes_b[name]
                # Compare bases
                bases_a = {ast.unparse(b) if hasattr(ast, 'unparse') else ast.dump(b)
                           for b in ca.bases}
                bases_b = {ast.unparse(b) if hasattr(ast, 'unparse') else ast.dump(b)
                           for b in cb.bases}
                added_bases = bases_b - bases_a
                removed_bases = bases_a - bases_b
                class_changes = []
                if added_bases:
                    class_changes.append(f"added bases: {', '.join(added_bases)}")
                if removed_bases:
                    class_changes.append(f"removed bases: {', '.join(removed_bases)}")

                # Compare methods
                methods_a = {n.name for n in ast.iter_child_nodes(ca)
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                methods_b = {n.name for n in ast.iter_child_nodes(cb)
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                added_methods = methods_b - methods_a
                removed_methods = methods_a - methods_b
                if added_methods:
                    class_changes.append(f"added methods: {', '.join(added_methods)}")
                if removed_methods:
                    class_changes.append(f"removed methods: {', '.join(removed_methods)}")

                if class_changes:
                    changes.append({
                        "type": "modified",
                        "kind": "class",
                        "name": name,
                        "detail": f"Class '{name}': {'; '.join(class_changes)}",
                        "changes": class_changes,
                    })

        # Compare imports
        imports_a = self._get_imports(self.tree_a)
        imports_b = self._get_imports(self.tree_b)
        added_imports = imports_b - imports_a
        removed_imports = imports_a - imports_b
        if added_imports:
            changes.append({
                "type": "added",
                "kind": "import",
                "name": ", ".join(sorted(added_imports)),
                "detail": f"Imports added: {', '.join(sorted(added_imports))}",
            })
        if removed_imports:
            changes.append({
                "type": "removed",
                "kind": "import",
                "name": ", ".join(sorted(removed_imports)),
                "detail": f"Imports removed: {', '.join(sorted(removed_imports))}",
            })

        return {
            "file_a": self.name_a,
            "file_b": self.name_b,
            "total_changes": len(changes),
            "changes": changes,
        }

    def _get_imports(self, tree: ast.AST) -> Set[str]:
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for alias in node.names:
                        imports.add(f"from {node.module} import {alias.name}")
        return imports

    def _compare_function_sig(self, fa: ast.AST, fb: ast.AST) -> List[str]:  # type: ignore[override]
        """Compare function signatures. Accepts FunctionDef or AsyncFunctionDef."""
        changes = []

        # Parameters
        args_a = [a.arg for a in fa.args.args] if hasattr(fa, 'args') else []
        args_b = [a.arg for a in fb.args.args] if hasattr(fb, 'args') else []
        added_args = [a for a in args_b if a not in args_a]
        removed_args = [a for a in args_a if a not in args_b]
        if added_args:
            changes.append(f"params added: {', '.join(added_args)}")
        if removed_args:
            changes.append(f"params removed: {', '.join(removed_args)}")

        # Decorators
        decos_a = {ast.unparse(d) if hasattr(ast, 'unparse') else ast.dump(d)
                   for d in fa.decorator_list}
        decos_b = {ast.unparse(d) if hasattr(ast, 'unparse') else ast.dump(d)
                   for d in fb.decorator_list}
        added_decos = decos_b - decos_a
        removed_decos = decos_a - decos_b
        if added_decos:
            changes.append(f"decorators added: {', '.join(added_decos)}")
        if removed_decos:
            changes.append(f"decorators removed: {', '.join(removed_decos)}")

        # Return type annotation
        if hasattr(ast, 'unparse'):
            ra = ast.unparse(fa.returns) if fa.returns else None
            rb = ast.unparse(fb.returns) if fb.returns else None
            if ra != rb:
                changes.append(f"return type: {ra or 'None'} → {rb or 'None'}")

        return changes


def cmd_diff(args):
    if args.code and args.Code:
        source_a = args.code
        source_b = args.Code
        name_a = "<code_a>"
        name_b = "<code_b>"
    elif args.file_a and args.file_b:
        source_a = Path(args.file_a).read_text(encoding="utf-8")
        source_b = Path(args.file_b).read_text(encoding="utf-8")
        name_a = args.file_a
        name_b = args.file_b
    else:
        print("Utilisez -c <code> -C <code> OU deux fichiers")
        sys.exit(1)

    differ = ASTDiffer(source_a, source_b, name_a, name_b)
    result = differ.diff()

    if "error" in result:
        print(f"❌ {result['error']}")
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"📊 AST Diff — {name_a} → {name_b}")
        print(f"   {result['total_changes']} changement(s)\n")
        for c in result["changes"]:
            icon = {"added": "🟢", "removed": "🔴", "modified": "🟡"}
            print(f"  {icon.get(c['type'], '•')} [{c['kind']}] {c['detail']}")


def register_cli(subparsers):
    """Register this tool as a CLI subcommand."""
    p = subparsers.add_parser("diff-ast", help="Diff structurel AST entre deux fichiers Python")
    p.add_argument("file_a", nargs="?", help="Fichier source ancien")
    p.add_argument("file_b", nargs="?", help="Fichier source nouveau")
    p.add_argument("-c", dest="code", help="Code source ancien (inline)")
    p.add_argument("-C", dest="Code", help="Code source nouveau (inline)")
    p.add_argument("--json", "-j", action="store_true", help="Sortie JSON")
    p.set_defaults(func=cmd_diff)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Diff structurel AST entre deux codes Python")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    args.func(args)