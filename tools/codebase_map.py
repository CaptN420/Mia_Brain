#!/usr/bin/env python3
"""codebase_map — Cartographie déterministe d'un projet Python.

Produit un manifest structuré : arbre des fichiers, classes/fonctions
avec signatures, imports, dépendances, métriques (LOC, complexité),
et détection de candidats workers/thinkers utilisables par CaptN.

Usage:
    python tools/codebase_map.py /path/to/project
    python tools/codebase_map.py /path/to/project --output manifest.json
    python tools/codebase_map.py /path/to/project --full

Déterministe, zéro LLM, pure AST + os.walk.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ═══════════════════════════════════════════════════════════════
# 1. DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════

@dataclass
class FunctionInfo:
    name: str
    line: int
    end_line: int
    decorators: List[str] = field(default_factory=list)
    args: List[str] = field(default_factory=list)
    has_return_annotation: bool = False
    docstring_length: int = 0
    body_line_count: int = 0
    complexity_estimate: int = 0  # approximate McCabe (if/for/while/except)


@dataclass
class ClassInfo:
    name: str
    line: int
    end_line: int
    bases: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    methods: List[FunctionInfo] = field(default_factory=list)
    docstring_length: int = 0


@dataclass
class ImportInfo:
    def __init__(self, source: str, names: List[str], is_from_import: bool = False):
        self.source = source
        self.names = names
        self.is_from_import = is_from_import


@dataclass
class FileInfo:
    path: str  # relative to project root
    size_bytes: int = 0
    loc: int = 0  # lines of code
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    is_entry_point: bool = False


@dataclass
class ProjectInfo:
    root: str
    total_files: int = 0
    total_python_files: int = 0
    total_loc: int = 0
    total_classes: int = 0
    total_functions: int = 0
    has_venv: bool = False
    has_git: bool = False
    files: List[FileInfo] = field(default_factory=list)
    all_dependencies: Set[str] = field(default_factory=set)
    worker_candidates: List[str] = field(default_factory=list)
    thinker_candidates: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 2. AST ANALYSER
# ═══════════════════════════════════════════════════════════════

def _count_body_lines(body: List[ast.stmt]) -> int:
    """Count approximate lines of code in a body (excludes docstrings)."""
    count = 0
    for node in body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # skip docstring
        count += 1
    return count


def _estimate_complexity(body: List[ast.stmt]) -> int:
    """Rough McCabe-like complexity: count branching/looping nodes."""
    complexity = 1
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                             ast.AsyncFor, ast.Try, ast.Assert)):
            complexity += 1
    return complexity


def _extract_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
    args = []
    for arg in node.args.args:
        args.append(arg.arg)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    for arg in node.args.kwonlyargs:
        args.append(arg.arg)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return args


def _has_return_annotation(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return node.returns is not None


def _get_decorator_names(decorator_list: List[ast.expr]) -> List[str]:
    names = []
    for dec in decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(f"{_get_attr_base(dec)}.{dec.attr}")
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.append(f"{_get_attr_base(dec.func)}.{dec.func.attr}")
    return names


def _get_attr_base(node: ast.Attribute) -> str:
    if isinstance(node.value, ast.Name):
        return node.value.id
    if isinstance(node.value, ast.Attribute):
        return f"{_get_attr_base(node.value)}.{node.value.attr}"
    return "?"


def _get_base_names(bases: List[ast.expr]) -> List[str]:
    names = []
    for base in bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(f"{_get_attr_base(base)}.{base.attr}")
    return names


def _extract_function_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
    decorators = _get_decorator_names(node.decorator_list)
    args = _extract_args(node)
    doc_len = 0
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        doc_len = len(node.body[0].value.value.strip())

    return FunctionInfo(
        name=node.name,
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        decorators=decorators,
        args=args,
        has_return_annotation=_has_return_annotation(node),
        docstring_length=doc_len,
        body_line_count=_count_body_lines(node.body),
        complexity_estimate=_estimate_complexity(node.body),
    )


def _extract_class_info(node: ast.ClassDef) -> ClassInfo:
    bases = _get_base_names(node.bases)
    decorators = _get_decorator_names(node.decorator_list)
    methods = []
    doc_len = 0
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        doc_len = len(node.body[0].value.value.strip())

    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_info = _extract_function_info(item)
            if item.name == "__init__":
                methods.insert(0, func_info)  # keep __init__ first
            else:
                methods.append(func_info)

    return ClassInfo(
        name=node.name,
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        bases=bases,
        decorators=decorators,
        methods=methods,
        docstring_length=doc_len,
    )


def _extract_imports(node: ast.Module) -> List[ImportInfo]:
    imports = []
    for item in node.body:
        if isinstance(item, ast.Import):
            for alias in item.names:
                imports.append(ImportInfo(
                    source=alias.name,
                    names=[alias.asname or alias.name],
                    is_from_import=False,
                ))
        elif isinstance(item, ast.ImportFrom):
            if item.module:
                names = [alias.asname or alias.name for alias in item.names]
                imports.append(ImportInfo(
                    source=item.module,
                    names=names,
                    is_from_import=True,
                ))
    return imports


def analyse_file(filepath: str) -> FileInfo:
    """Parse a single .py file and return its structured info."""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        source = f.read()
    loc = len(source.splitlines())

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return FileInfo(
            path=filepath,
            size_bytes=os.path.getsize(filepath),
            loc=loc,
            imports=[ImportInfo(source="<parse_error>", names=[], is_from_import=False)],
        )

    classes = []
    functions = []
    imports = _extract_imports(tree)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(_extract_class_info(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_extract_function_info(node))

    return FileInfo(
        path=filepath,
        size_bytes=os.path.getsize(filepath),
        loc=loc,
        classes=classes,
        functions=functions,
        imports=imports,
    )


# ═══════════════════════════════════════════════════════════════
# 3. PROJECT SCANNER (étendu)
# ═══════════════════════════════════════════════════════════════

EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "__pycache__",
                 "node_modules", "dist", "build", ".pytest_cache",
                 "migrations", "session", "archive", "backups"}

ENTRY_POINTS = {"main.py", "app.py", "run.py", "launcher.py", "cli.py"}

# Patterns for CaptN worker/thinker detection
WORKER_PATTERNS = {
    "Worker": lambda c: any("Worker" in b for b in c.bases),
    "BaseWorker": lambda c: any("BaseWorker" in b for b in c.bases),
}
THINKER_PATTERNS = {
    "Thinker": lambda c: "Thinker" in c.name or any("Thinker" in b for b in c.bases),
    "BaseThinker": lambda c: "BaseThinker" in c.name or any("BaseThinker" in b for b in c.bases),
}


def scan_project(project_root: str) -> ProjectInfo:
    """Full deterministic scan of a Python project."""
    root = os.path.abspath(project_root)
    info = ProjectInfo(root=root)

    # Detect venv and git
    for v in (".venv", "venv", "env"):
        if os.path.isdir(os.path.join(root, v)):
            info.has_venv = True
            break
    info.has_git = os.path.isdir(os.path.join(root, ".git"))

    # Walk files
    python_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter excluded dirs in-place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                python_files.append((rel, os.path.join(dirpath, fn)))

    info.total_files = len(python_files)

    # Analyse chaque fichier Python
    for rel_path, abs_path in python_files:
        file_info = analyse_file(abs_path)
        file_info.path = rel_path
        file_info.is_entry_point = os.path.basename(rel_path) in ENTRY_POINTS
        info.files.append(file_info)

        # Accumulate totals
        info.total_loc += file_info.loc
        info.total_classes += len(file_info.classes)
        for c in file_info.classes:
            info.total_functions += len(c.methods)
            # Detect worker/thinker candidates
            for label, checker in WORKER_PATTERNS.items():
                if checker(c):
                    info.worker_candidates.append(f"{rel_path}::{c.name}")
            for label, checker in THINKER_PATTERNS.items():
                if checker(c):
                    info.thinker_candidates.append(f"{rel_path}::{c.name}")
        info.total_functions += len(file_info.functions)

        # Collect dependencies
        for imp in file_info.imports:
            top_pkg = imp.source.split(".")[0]
            if top_pkg not in ("os", "sys", "re", "json", "math", "time",
                               "logging", "typing", "pathlib", "abc",
                               "unittest", "pytest", "collections",
                               "functools", "itertools", "copy",
                               "dataclasses", "enum", "hashlib",
                               "tempfile", "shutil", "glob", "io",
                               "textwrap", "string", "random", "statistics",
                               "fractions", "decimal", "pprint",
                               "argparse", "configparser", "subprocess",
                               "threading", "concurrent", "multiprocessing",
                               "queue", "selectors", "asyncio",
                               "socket", "http", "urllib", "email",
                               "base64", "binascii", "struct",
                               "warnings", "contextlib", "ast",
                               "inspect", "imp", "importlib",
                               "platform", "ctypes", "signal",
                               "traceback", "pickle", "shelve", "dbm"):
                info.all_dependencies.add(top_pkg)

    # Tri par taille décroissante pour les gros fichiers en premier
    info.files.sort(key=lambda f: f.loc, reverse=True)

    return info


# ═══════════════════════════════════════════════════════════════
# 4. SERIALISATION
# ═══════════════════════════════════════════════════════════════

def _to_dict(obj):
    """Convert dataclass tree to JSON-compatible dict."""
    if isinstance(obj, (ProjectInfo, FileInfo, ClassInfo, FunctionInfo, ImportInfo)):
        return {k: _to_dict(v) for k, v in asdict(obj).items() if v or k in ("path", "name", "root")}
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    return obj


def project_to_dict(info: ProjectInfo) -> Dict[str, Any]:
    """Serialise a ProjectInfo as a clean JSON dict."""
    return _to_dict(info)


# ═══════════════════════════════════════════════════════════════
# 5. CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Cartographie déterministe d'un projet Python",
    )
    parser.add_argument("project", help="Chemin du projet à analyser")
    parser.add_argument("--output", "-o", help="Fichier de sortie JSON (défaut: stdout)")
    parser.add_argument("--full", "-f", action="store_true",
                        help="Inclure tous les détails (fonctions, méthodes, imports)")
    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print(f"Erreur: {args.project} n'est pas un dossier", file=sys.stderr)
        sys.exit(1)

    info = scan_project(args.project)
    data = project_to_dict(info)

    if args.full:
        pass  # full data already included
    else:
        # Only summary + file list with class/function counts
        for f in data["files"]:
            f.pop("imports", None)
            for c in f.get("classes", []):
                c.pop("methods", None)
                c.pop("docstring_length", None)
            for fn in f.get("functions", []):
                fn.pop("args", None)
                fn.pop("decorators", None)
                fn.pop("docstring_length", None)
                fn.pop("has_return_annotation", None)
                fn.pop("complexity_estimate", None)

    output = json.dumps(data, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"✅ Manifest écrit dans {args.output}")
    else:
        print(output)


def register_cli(subparsers):
    """Register codebase-map as a CLI subcommand."""
    p = subparsers.add_parser("codebase-map", help="Cartographie déterministe d'un projet Python")
    p.add_argument("project", help="Chemin du projet à analyser")
    p.add_argument("--output", "-o", help="Fichier JSON de sortie")
    p.add_argument("--full", "-f", action="store_true", help="Inclure tous les détails")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()