#!/usr/bin/env python3
"""
lint.py — Linter Python structuré (sans LLM).

Utilise pyflakes (si dispo) ou un analyseur AST intégré en fallback.
Remplace "check mon code" par un résultat JSON structuré.

Usage:
    python -m tools.lint mon_fichier.py
    python -m tools.lint mon_fichier.py --json
    python -m tools.lint /chemin/projet --errors-only
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


def ast_check(source: str, filename: str = "<unknown>") -> List[Dict]:
    """Analyse AST basique — capture les erreurs évidentes sans pyflakes."""
    issues = []

    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [{
            "severity": "error",
            "line": e.lineno or 0,
            "col": e.offset or 0,
            "message": f"SyntaxError: {e.msg}",
            "code": "E000",
        }]

    lines = source.splitlines()

    # 1. Vérifier les fonctions sans docstring (sauf __init__, _private)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                if not ast.get_docstring(node):
                    issues.append({
                        "severity": "info",
                        "line": node.lineno,
                        "col": node.col_offset,
                        "message": f"Function '{node.name}' missing docstring",
                        "code": "D100",
                    })

    # 2. Vérifier les classes sans docstring
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not ast.get_docstring(node):
                issues.append({
                    "severity": "info",
                    "line": node.lineno,
                    "col": node.col_offset,
                    "message": f"Class '{node.name}' missing docstring",
                    "code": "D200",
                })

    # 3. Détecter les `try` sans `except` spécifique
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is None:
                    issues.append({
                        "severity": "warning",
                        "line": handler.lineno,
                        "col": handler.col_offset,
                        "message": "Bare 'except:' catches all exceptions — use 'except SpecificError:'",
                        "code": "W0702",
                    })

    # 4. Détecter les prints dans du code non-script
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                issues.append({
                    "severity": "info",
                    "line": node.lineno,
                    "col": node.col_offset,
                    "message": "print() found — use logger instead",
                    "code": "W1001",
                })

    # 5. Lignes trop longues
    for i, l in enumerate(lines, 1):
        if len(l.rstrip("\n")) > 120:
            issues.append({
                "severity": "info",
                "line": i,
                "col": 0,
                "message": f"Line too long ({len(l.rstrip())} > 120 chars)",
                "code": "E501",
            })

    # 6. Détecter `== None` / `!= None`
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    if isinstance(comp, ast.Constant) and comp.value is None:
                        issues.append({
                            "severity": "warning",
                            "line": node.lineno,
                            "col": node.col_offset,
                            "message": f"Use 'is {type(op).__name__ == 'Eq' and 'not' or ''} None' instead of '{'==' if isinstance(op, ast.Eq) else '!='} None'",
                            "code": "W1002",
                        })

    return issues


def run_pyflakes(filepath: str) -> Optional[List[Dict]]:
    """Tente d'utiliser pyflakes pour un meilleur diagnostic."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", filepath],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and not result.stdout:
            return []

        issues = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # pyflakes format: "filename:line:col: message"
            parts = line.split(":", 3)
            if len(parts) >= 4:
                issues.append({
                    "severity": "error" if "undefined" in parts[3].lower() or "redefinition" in parts[3].lower() else "warning",
                    "line": int(parts[1]),
                    "col": int(parts[2]) if parts[2].isdigit() else 0,
                    "message": parts[3].strip(),
                    "tool": "pyflakes",
                })
        return issues
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None


def analyze_file(filepath: str) -> Dict:
    source = Path(filepath).read_text(encoding="utf-8")

    # Try pyflakes first
    pyflakes_issues = run_pyflakes(filepath)
    if pyflakes_issues is not None:
        ast_issues = ast_check(source, filepath)
        all_issues = pyflakes_issues + ast_issues
    else:
        all_issues = ast_check(source, filepath)

    # Count by severity
    counts = {"error": 0, "warning": 0, "info": 0}
    for i in all_issues:
        counts[i["severity"]] = counts.get(i["severity"], 0) + 1

    return {
        "filename": filepath,
        "total": len(all_issues),
        "counts": counts,
        "issues": all_issues,
    }


def cmd_lint(args):
    if os.path.isdir(args.target):
        results = []
        for py_file in sorted(Path(args.target).rglob("*.py")):
            results.append(analyze_file(str(py_file)))

        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            for r in results:
                if r["total"] > 0:
                    c = r["counts"]
                    print(f"  {r['filename']}")
                    print(f"    {r['total']} issues ({c['error']}E {c['warning']}W {c['info']}I)")
                    if args.errors_only:
                        for i in r["issues"]:
                            if i["severity"] in ("error", "warning"):
                                print(f"      L.{i['line']:4d} [{i['severity'][0].upper()}] {i['message']}")
        return

    result = analyze_file(args.target)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    c = result["counts"]
    print(f"📋 {result['filename']} — {result['total']} issues")
    print(f"   {c['error']} errors | {c['warning']} warnings | {c['info']} info\n")

    for i in result["issues"]:
        icon = {"error": "❌", "warning": "⚠", "info": "ℹ"}
        print(f"  {icon.get(i['severity'], '•')} L.{i['line']:4d}  {i['message']}")
        if i.get("code"):
            print(f"       [{i['code']}]")


def register_cli(subparsers):
    """Register this tool as a CLI subcommand."""
    p = subparsers.add_parser("lint", help="Linter Python structuré")
    p.add_argument("target", help="Fichier ou dossier")
    p.add_argument("--json", "-j", action="store_true", help="Sortie JSON")
    p.add_argument("--errors-only", "-e", action="store_true", help="Uniquement les erreurs/warnings")
    p.set_defaults(func=cmd_lint)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Linter Python structuré")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    args.func(args)