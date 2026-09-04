#!/usr/bin/env python3
"""
context_ai.py — Extrait le contexte local d'un fichier Python à une ligne donnée.

Économise des tokens LLM : au lieu d'envoyer tout un fichier pour
"explique cette fonction", extrayez précisément la fonction + ses
dépendances locales.

Usage:
    python -m tools.context_ai mon_fichier.py --line 42
    python -m tools.context_ai mon_fichier.py --function foo
    python -m tools.context_ai mon_fichier.py --line 42 --surrounding 5
    python -m tools.context_ai mon_fichier.py --json
"""

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class ContextExtractor:
    def __init__(self, source: str, filename: str = "<unknown>"):
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.filename = filename
        self.tree = None
        self.error = None
        try:
            self.tree = ast.parse(source)
        except SyntaxError as e:
            self.error = str(e)

    def find_enclosing_node(self, line: int) -> Optional[Dict]:
        """Trouve le nœud (fonction/classe) qui contient la ligne."""
        if not self.tree:
            return None

        best = None
        best_depth = -1

        def walk(node, depth=0):
            nonlocal best, best_depth
            if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
                if node.lineno <= line <= node.end_lineno:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef)):
                        if depth > best_depth:
                            best = node
                            best_depth = depth
            for child in ast.iter_child_nodes(node):
                walk(child, depth + 1)

        walk(self.tree)
        return best

    def get_context(self, line: int, surrounding: int = 3) -> Dict:
        """Extrait le contexte complet autour d'une ligne."""
        if self.error:
            return {"error": self.error}

        enclosing = self.find_enclosing_node(line)
        result = {
            "filename": self.filename,
            "target_line": line,
            "source_line": self.lines[line - 1].rstrip() if 0 < line <= len(self.lines) else None,
        }

        # Lignes environnantes
        start = max(0, line - 1 - surrounding)
        end = min(len(self.lines), line + surrounding)
        result["surrounding_lines"] = "".join(self.lines[start:end]).rstrip()
        result["surrounding_range"] = (start + 1, end)

        if enclosing:
            result["enclosing_kind"] = type(enclosing).__name__
            result["enclosing_name"] = enclosing.name
            result["enclosing_range"] = (enclosing.lineno, enclosing.end_lineno)

            # Extraire le code de la fonction/classe englobante
            result["enclosing_code"] = "".join(
                self.lines[enclosing.lineno - 1: enclosing.end_lineno]
            )

            # Docstring
            doc = ast.get_docstring(enclosing)
            if doc:
                result["enclosing_docstring"] = doc

            # Paramètres (pour les fonctions)
            if isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = []
                for arg in enclosing.args.args:
                    p = {"name": arg.arg}
                    if arg.annotation:
                        p["type"] = ast.unparse(arg.annotation) if hasattr(ast, 'unparse') else "..."
                    params.append(p)
                result["parameters"] = params

                # Décorateurs
                decos = []
                for d in enclosing.decorator_list:
                    decos.append(ast.unparse(d) if hasattr(ast, 'unparse') else ast.dump(d))
                if decos:
                    result["decorators"] = decos

                # Return annotation
                if enclosing.returns:
                    result["return_type"] = ast.unparse(enclosing.returns) if hasattr(ast, 'unparse') else ast.dump(enclosing.returns)

            # Bases (pour les classes)
            if isinstance(enclosing, ast.ClassDef):
                bases = []
                for b in enclosing.bases:
                    bases.append(ast.unparse(b) if hasattr(ast, 'unparse') else ast.dump(b))
                if bases:
                    result["bases"] = bases

        return result

    def get_function_context(self, func_name: str) -> Optional[Dict]:
        """Extrait le contexte d'une fonction par son nom."""
        if not self.tree:
            return None

        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == func_name:
                    # Find the first line of this node to use get_context
                    return self.get_context(node.lineno)
        return None

    def list_functions(self) -> List[Dict]:
        """Liste toutes les fonctions/classes avec leur portée."""
        if not self.tree:
            return []
        result = []
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                result.append({
                    "kind": "function",
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "decorators": [ast.unparse(d) if hasattr(ast, 'unparse') else ast.dump(d)
                                   for d in node.decorator_list],
                    "has_docstring": doc is not None,
                })
            elif isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node)
                methods = []
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({
                            "name": item.name,
                            "line": item.lineno,
                        })
                result.append({
                    "kind": "class",
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "has_docstring": doc is not None,
                    "methods": methods,
                })
        return result


def _fmt_param(p: dict) -> str:
    name = p["name"]
    return f"{name}: {p['type']}" if p.get("type") else name


def cmd_context(args):
    source = Path(args.file).read_text(encoding="utf-8")
    extractor = ContextExtractor(source, args.file)

    if args.list:
        funcs = extractor.list_functions()
        if args.json:
            print(json.dumps(funcs, indent=2, ensure_ascii=False))
        else:
            print(f"📋 {args.file} — {len(funcs)} symboles\n")
            for f in funcs:
                if f["kind"] == "function":
                    deco = f" @{', '.join(f['decorators'])}" if f["decorators"] else ""
                    doc = " 📝" if f["has_docstring"] else ""
                    print(f"  def {f['name']}{deco}  (l.{f['line']}){doc}")
                else:
                    doc = " 📝" if f["has_docstring"] else ""
                    print(f"  class {f['name']}{doc}  (l.{f['line']})")
                    for m in f["methods"]:
                        print(f"    └─ def {m['name']}  (l.{m['line']})")
        return

    if args.function:
        result = extractor.get_function_context(args.function)
        if not result:
            print(f"⚠ Fonction '{args.function}' introuvable")
            sys.exit(1)
    elif args.line:
        result = extractor.get_context(args.line, args.surrounding)
    else:
        print("Utilisez --line ou --function")
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"  📍 Contexte — {Path(args.file).name}")
        if "enclosing_name" in result:
            print(f"  {result['enclosing_kind']} : {result['enclosing_name']}")
            print(f"  Lignes {result['enclosing_range'][0]}-{result['enclosing_range'][1]}")
            if "parameters" in result:
                params_str = ", ".join(_fmt_param(p) for p in result['parameters'])
                print(f"  Paramètres : {params_str}")
            if "return_type" in result:
                print(f"  Retourne : {result['return_type']}")
            if "bases" in result:
                print(f"  Hérite de : {', '.join(result['bases'])}")
        print(f"  Ligne cible : {result.get('target_line')} → {result.get('source_line', '')}")
        print(f"  Contexte ({result.get('surrounding_range', ('?','?'))[0]}-{result.get('surrounding_range', ('?','?'))[1]}):")
        print(f"{'='*60}")
        print(result.get("enclosing_code", result.get("surrounding_lines", "")))


def register_cli(subparsers):
    """Register this tool as a CLI subcommand."""
    p = subparsers.add_parser("context", help="Extraire le contexte local d'un fichier Python")
    p.add_argument("file", help="Fichier source")
    p.add_argument("--line", "-l", type=int, help="Numéro de ligne cible")
    p.add_argument("--function", "-f", help="Nom de la fonction/classe")
    p.add_argument("--surrounding", "-s", type=int, default=3, help="Lignes de contexte (défaut: 3)")
    p.add_argument("--list", action="store_true", help="Lister les symboles du fichier")
    p.add_argument("--json", "-j", action="store_true", help="Sortie JSON")
    p.set_defaults(func=cmd_context)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extrait le contexte local d'un fichier Python")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    args.func(args)