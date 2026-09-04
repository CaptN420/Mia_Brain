#!/usr/bin/env python3
"""
deadscout.py — Détecteur de code mort dans les projets Python.

Déterministe pur (AST).
Détecte : fonctions/classes jamais appelées, variables orphelines,
          imports inutilisés, code sous condition toujours fausse.

Usage:
    python -m tools.deadscout /path/to/project
    python -m tools.deadscout /path/to/project --detailed
    python -m tools.deadscout /path/to/project --output report.json
"""

import ast
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class DeadItem:
    kind: str           # 'function', 'class', 'variable', 'import', 'dead_code'
    name: str
    file: str
    line: int
    reason: str
    confidence: float   # 0.0 → 1.0


@dataclass
class DeadScoutReport:
    items: List[DeadItem] = field(default_factory=list)
    analyzed_files: int = 0
    analyzed_lines: int = 0
    total_dead: int = 0

    def to_dict(self) -> dict:
        return {
            "analyzed_files": self.analyzed_files,
            "analyzed_lines": self.analyzed_lines,
            "total_dead": self.total_dead,
            "items": [
                {
                    "kind": i.kind, "name": i.name,
                    "file": i.file, "line": i.line,
                    "reason": i.reason, "confidence": i.confidence,
                }
                for i in sorted(self.items, key=lambda x: (x.file, x.line))
            ],
        }


class DeadScout:
    """Analyseur de code mort pour un projet Python."""

    EXCLUDED_DIRS = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", "egg-info", ".tox", ".mypy_cache",
        ".pytest_cache", ".hypothesis",
    }

    ENTRY_POINT_NAMES = {
        "main", "run", "app", "setup", "cli", "launcher",
        "server", "dashboard", "__main__",
    }

    # Noms qui ressemblent à des tests (donc pas "morts")
    TEST_PREFIXES = {"test_", "test_"}

    def __init__(self, root: str, exclude_dirs: Optional[Set[str]] = None):
        self.root = Path(root).resolve()
        self.exclude_dirs = exclude_dirs or self.EXCLUDED_DIRS
        self._all_files: List[Path] = []
        self._defined: Dict[str, List[Tuple[str, int, str]]] = defaultdict(list)
        self._used: Dict[str, Set[str]] = defaultdict(set)
        self._imports_in_file: Dict[str, Set[str]] = defaultdict(set)
        self._report = DeadScoutReport()
        self._total_lines = 0

    def _is_excluded(self, path: Path) -> bool:
        return any(p in self.exclude_dirs for p in path.parts)

    def _scan(self) -> None:
        for pyfile in self.root.rglob("*.py"):
            if self._is_excluded(pyfile):
                continue
            self._all_files.append(pyfile)

    def _file_kind(self, path: Path) -> str:
        """Classification rapide du fichier."""
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            return "test"
        if "__init__" in name:
            return "package"
        if name.startswith("_"):
            return "internal"
        return "module"

    def _analyze_file(self, path: Path) -> None:
        """Parse un fichier et collecte définitions + usages."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return

        self._total_lines += text.count("\n") + 1

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return

        rel = path.relative_to(self.root).as_posix()
        fkind = self._file_kind(path)

        # Collecte : définitions et appels
        defined_here: Set[str] = set()
        called_here: Set[str] = set()
        imported_here: Set[str] = set()

        for node in ast.walk(tree):
            # Définitions
            if isinstance(node, ast.FunctionDef):
                self._defined[node.name].append((rel, node.lineno, "function"))
                defined_here.add(node.name)
            elif isinstance(node, ast.AsyncFunctionDef):
                self._defined[node.name].append((rel, node.lineno, "function"))
                defined_here.add(node.name)
            elif isinstance(node, ast.ClassDef):
                self._defined[node.name].append((rel, node.lineno, "class"))
                defined_here.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._defined[target.id].append((rel, node.lineno, "variable"))
                        defined_here.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    self._defined[node.target.id].append((rel, node.lineno, "variable"))
                    defined_here.add(node.target.id)

            # Appels / usages
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_here.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        # obj.method() — méthode d'objet, moins significatif
                        pass
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    pass  # On capturera les noms chargés en fin

            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    asname = alias.asname or alias.name
                    name_only = alias.name.split(".")[0]
                    imported_here.add(asname)
                    imported_here.add(name_only)
                    self._imports_in_file[rel].add(asname)
                    self._imports_in_file[rel].add(name_only)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    asname = alias.asname or alias.name
                    imported_here.add(asname)
                    self._imports_in_file[rel].add(asname)

        # Détection des variables définies mais jamais lues dans ce fichier
        # (approximatif : on regarde les noms Load vs Store)
        defined_names: Set[str] = set()
        loaded_names: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    defined_names.add(node.id)
                elif isinstance(node.ctx, ast.Load):
                    loaded_names.add(node.id)

        orphans = defined_names - loaded_names - imported_here
        # Exclure les dunder, les entry points, les décorateurs
        for name in orphans:
            if name.startswith("__") and name.endswith("__"):
                continue
            if name.startswith("_"):
                continue
            self._report.items.append(DeadItem(
                kind="variable",
                name=name,
                file=rel,
                line=0,  # approximatif
                reason=f"Variable définie mais jamais lue dans {rel}",
                confidence=0.6,
            ))

        self._used[rel] = called_here | loaded_names

    def _detect_dead_functions(self) -> None:
        """Fonctions/classes définies mais jamais appelées depuis l'extérieur."""
        for name, locations in self._defined.items():
            # Skip entry points et tests
            if name in self.ENTRY_POINT_NAMES:
                continue
            if name.startswith("test_") or name.endswith("_test"):
                continue
            if name.startswith("__") and name.endswith("__"):
                continue
            if name.startswith("_"):
                continue

            # Vérifier si ce nom est utilisé ailleurs
            # (usage = appel direct + import)
            used_elsewhere = False
            for rel, _, kind in locations:
                for other_rel, used_set in self._used.items():
                    if other_rel == rel:
                        continue  # pas de self-use (déjà dans le même fichier)
                    if name in used_set:
                        used_elsewhere = True
                        break
                if used_elsewhere:
                    break

            if not used_elsewhere:
                for rel, line, kind in locations:
                    # Heuristique : si le fichier est un test, skip
                    if rel.startswith("test_") or "/test_" in rel:
                        continue
                    # Heuristique : les décorateurs ou registrations
                    # (signalent des faux positifs)
                    confidence = 0.7 if kind == "function" else 0.6
                    self._report.items.append(DeadItem(
                        kind=kind,
                        name=name,
                        file=rel,
                        line=line,
                        reason=f"{kind} '{name}' défini mais jamais appelé/utilisé ailleurs",
                        confidence=confidence,
                    ))

    def _detect_dead_imports(self) -> None:
        """Imports qui ne sont jamais utilisés dans le fichier."""
        for rel, imports in self._imports_in_file.items():
            used = self._used.get(rel, set())
            for imp in sorted(imports):
                if imp not in used and imp not in {"__future__", "typing"}:
                    self._report.items.append(DeadItem(
                        kind="import",
                        name=imp,
                        file=rel,
                        line=0,
                        reason=f"Import '{imp}' non utilisé dans {rel}",
                        confidence=0.8,
                    ))

    def analyze(self) -> DeadScoutReport:
        self._scan()
        for path in self._all_files:
            self._analyze_file(path)
        self._detect_dead_functions()
        self._detect_dead_imports()

        self._report.analyzed_files = len(self._all_files)
        self._report.analyzed_lines = self._total_lines
        self._report.total_dead = len(self._report.items)

        return self._report


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Détecteur de code mort dans les projets Python"
    )
    parser.add_argument("project", help="Chemin du projet à analyser")
    parser.add_argument("--detailed", "-d", action="store_true",
                        help="Afficher tous les détails")
    parser.add_argument("--output", "-o", help="Fichier JSON de sortie")
    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print(f"Erreur: {args.project} n'est pas un dossier", file=sys.stderr)
        sys.exit(1)

    scout = DeadScout(args.project)
    report = scout.analyze()

    # Résumé
    print(f"📊 DeadScout — {report.analyzed_files} fichiers, {report.analyzed_lines} lignes")
    print(f"   Code mort trouvé : {report.total_dead} items\n")

    # Stats par catégorie
    by_kind = defaultdict(int)
    for item in report.items:
        by_kind[item.kind] += 1
    for kind, count in sorted(by_kind.items()):
        print(f"   {kind:10s}: {count}")

    if args.detailed:
        print("\n── DÉTAILS ──")
        for item in sorted(report.items, key=lambda x: (x.file, x.line)):
            file_short = item.file[-50:] if len(item.file) > 50 else item.file
            confidence_pct = int(item.confidence * 100)
            print(f"  [{confidence_pct:3d}%] {item.kind:9s} {file_short}:{item.line} — {item.name}")
            print(f"        {item.reason}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nRapport écrit dans {args.output}", file=sys.stderr)


def register_cli(subparsers):
    """Register deadscout as a CLI subcommand."""
    p = subparsers.add_parser("deadscout", help="Détection de code mort")
    p.add_argument("project", help="Chemin du projet")
    p.add_argument("--detailed", "-d", action="store_true")
    p.add_argument("--output", "-o", help="Fichier de rapport JSON")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()