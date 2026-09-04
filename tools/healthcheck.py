#!/usr/bin/env python3
"""
healthcheck.py — Score de santé d'un projet Python.

Agrège : couverture de docstring, complexité, code mort, duplication, âge, etc.
Sort un score /100 avec recommandations.

Usage:
    python -m tools.healthcheck /path/to/project
    python -m tools.healthcheck /path/to/project --json
    python -m tools.healthcheck /path/to/project --verbose
"""

import ast
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class HealthMetrics:
    file_count: int = 0
    line_count: int = 0
    function_count: int = 0
    class_count: int = 0
    documented_functions: int = 0
    documented_classes: int = 0
    documented_modules: int = 0
    total_cyclomatic: float = 0.0
    file_sizes: List[int] = field(default_factory=list)
    dependency_count: int = 0
    dead_items: int = 0
    has_tests: bool = False
    test_count: int = 0
    has_readme: bool = False
    has_license: bool = False
    has_contributing: bool = False
    has_entry_point: bool = False
    has_ci_config: bool = False
    python_file_count: int = 0


@dataclass
class HealthReport:
    score: int = 0
    grade: str = "N/A"
    metrics: HealthMetrics = field(default_factory=HealthMetrics)
    recommendations: List[str] = field(default_factory=list)
    details: Dict[str, any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "grade": self.grade,
            "recommendations": self.recommendations,
            "metrics": {
                "file_count": self.metrics.file_count,
                "line_count": self.metrics.line_count,
                "function_count": self.metrics.function_count,
                "class_count": self.metrics.class_count,
                "docstring_coverage_pct": self.details.get("docstring_coverage", 0),
                "avg_cyclomatic": self.details.get("avg_cyclomatic", 0),
                "avg_file_size": self.details.get("avg_file_size", 0),
                "dead_items": self.metrics.dead_items,
                "dependency_count": self.metrics.dependency_count,
                "has_tests": self.metrics.has_tests,
                "test_count": self.metrics.test_count,
                "has_ci": self.metrics.has_ci_config,
            },
        }


class HealthChecker:
    """Analyse la santé d'un projet Python."""

    EXCLUDED_DIRS = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", "egg-info", ".tox", ".mypy_cache",
        ".pytest_cache", ".hypothesis",
    }

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.metrics = HealthMetrics()
        self._all_files: List[Path] = []
        self._python_files: List[Path] = []

    def _is_excluded(self, path: Path) -> bool:
        return any(p in self.EXCLUDED_DIRS for p in path.parts)

    def _scan(self) -> None:
        for f in self.root.rglob("*"):
            if any(p in self.EXCLUDED_DIRS for p in f.parts):
                continue
            if f.is_file():
                self._all_files.append(f)
                if f.suffix == ".py":
                    self._python_files.append(f)

    def _count_lines(self) -> int:
        total = 0
        for f in self._python_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                total += text.count("\n") + 1
                self.metrics.file_sizes.append(len(text))
            except (OSError, PermissionError):
                pass
        return total

    def _analyze_python_files(self) -> None:
        """Analyse docstrings, complexité cyclomatique, counts."""
        for f in self._python_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except (SyntaxError, OSError, PermissionError):
                continue

            is_test = f.name.startswith("test_") or "_test.py" in f.name

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.metrics.function_count += 1
                    if not is_test and not node.name.startswith("_"):
                        if ast.get_docstring(node):
                            self.metrics.documented_functions += 1
                    # Cyclomatic complexity (McCabe)
                    self.metrics.total_cyclomatic += self._cyclomatic(node)
                elif isinstance(node, ast.ClassDef):
                    self.metrics.class_count += 1
                    if not is_test and not node.name.startswith("_"):
                        if ast.get_docstring(node):
                            self.metrics.documented_classes += 1

        # Module-level docstrings
        for f in self._python_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                        if isinstance(node.value.value, str):
                            self.metrics.documented_modules += 1
                            break
            except Exception:
                pass

    def _cyclomatic(self, node) -> int:
        """Calcule la complexité cyclomatique McCabe pour un nœud."""
        count = 1  # base
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                count += 1
            elif isinstance(child, ast.ExceptHandler):
                count += 1
            elif isinstance(child, (ast.And, ast.Or)):
                count += 1
            elif isinstance(child, ast.Assert):
                count += 1
            elif isinstance(child, ast.BoolOp):
                # each "and"/"or" adds complexity
                # BoolOp has 'op' and 'values'
                count += len(child.values) - 1
        return count

    def _check_tests(self) -> None:
        """Cherche des tests (pytest, unittest)."""
        test_files = [f for f in self._python_files
                      if f.name.startswith("test_") or "_test.py" in f.name]
        self.metrics.test_count = len(test_files)
        self.metrics.has_tests = len(test_files) > 0

        # Also check if pytest is configured
        pytest_ini = self.root / "pytest.ini"
        setup_cfg = self.root / "setup.cfg"
        pyproject = self.root / "pyproject.toml"
        if pytest_ini.exists():
            self.metrics.has_tests = True
        if setup_cfg.exists() and "pytest" in setup_cfg.read_text():
            self.metrics.has_tests = True
        if pyproject.exists() and "pytest" in pyproject.read_text():
            self.metrics.has_tests = True

    def _check_project_files(self) -> None:
        self.metrics.has_readme = any(
            f.name.lower().startswith("readme") for f in self._all_files
        )
        self.metrics.has_license = any(
            f.name.lower().startswith("license") for f in self._all_files
        )
        self.metrics.has_contributing = any(
            f.name.lower().startswith("contributing") for f in self._all_files
        )
        self.metrics.has_entry_point = any(
            f.name in {"main.py", "app.py", "cli.py", "run.py", "__main__.py"}
            for f in self._python_files
        )
        # CI
        ci_paths = [
            self.root / ".github" / "workflows",
            self.root / ".gitlab-ci.yml",
            self.root / ".circleci" / "config.yml",
        ]
        self.metrics.has_ci_config = any(p.exists() for p in ci_paths)

    def _check_dependencies(self) -> None:
        """Compte les dépendances directes."""
        req_files = [
            self.root / "requirements.txt",
            self.root / "pyproject.toml",
            self.root / "setup.py",
            self.root / "setup.cfg",
        ]
        count = 0
        for rf in req_files:
            if rf.exists():
                try:
                    text = rf.read_text()
                    if rf.name == "requirements.txt":
                        count += sum(1 for line in text.split("\n")
                                     if line.strip() and not line.startswith("#"))
                    elif rf.name == "pyproject.toml":
                        if "dependencies" in text or "project.dependencies" in text:
                            # Approx
                            count += text.count(">=") + text.count("==")
                    elif rf.name == "setup.py":
                        count += text.count("install_requires=[")
                except Exception:
                    pass
        self.metrics.dependency_count = count

    def check(self) -> HealthReport:
        self._scan()
        self.metrics.file_count = len(self._all_files)
        self.metrics.python_file_count = len(self._python_files)
        self.metrics.line_count = self._count_lines()

        self._analyze_python_files()
        self._check_tests()
        self._check_project_files()
        self._check_dependencies()

        # Calculate dead items with deadscout if available
        try:
            from tools.deadscout import DeadScout
            scout = DeadScout(str(self.root))
            report = scout.analyze()
            self.metrics.dead_items = report.total_dead
        except Exception:
            self.metrics.dead_items = 0

        report = HealthReport(metrics=self.metrics)
        report.score, report.grade, report.recommendations = self._score()
        report.details = self._details()

        return report

    def _details(self) -> dict:
        mc = self.metrics
        doc_coverage = self._docstring_coverage()
        avg_cyc = round(self.metrics.total_cyclomatic / max(self.metrics.function_count, 1), 2)
        avg_fsize = round(sum(self.metrics.file_sizes) / max(len(self.metrics.file_sizes), 1), 0)
        return {
            "docstring_coverage": doc_coverage,
            "avg_cyclomatic": avg_cyc,
            "avg_file_size": int(avg_fsize),
            "python_file_count": len(self._python_files),
        }

    def _docstring_coverage(self) -> float:
        mc = self.metrics
        total_public = mc.function_count + mc.class_count
        documented = mc.documented_functions + mc.documented_classes
        if total_public == 0:
            return 100.0
        return round(documented / total_public * 100, 1)

    def _score(self) -> Tuple[int, str, List[str]]:
        score = 100
        recs: List[str] = []
        mc = self.metrics

        # — Pénalités —

        # Docstring coverage
        doc_cov = self._docstring_coverage()
        if doc_cov < 30:
            score -= 20
            recs.append(f"🔴 Documentation faible ({doc_cov}%) — ajouter des docstrings")
        elif doc_cov < 60:
            score -= 10
            recs.append(f"🟡 Documentation partielle ({doc_cov}%)")
        elif doc_cov < 80:
            score -= 5

        # Complexité cyclomatique
        avg_cyc = self.metrics.total_cyclomatic / max(self.metrics.function_count, 1)
        if avg_cyc > 10:
            score -= 15
            recs.append(f"🔴 Complexité cyclomatique élevée ({avg_cyc:.1f}) — refactorer")
        elif avg_cyc > 5:
            score -= 5
            recs.append(f"🟡 Complexité moyenne ({avg_cyc:.1f})")

        # Code mort
        if mc.dead_items > 20:
            score -= 15
            recs.append(f"🔴 {mc.dead_items} items de code mort — nettoyer")
        elif mc.dead_items > 5:
            score -= 5
            recs.append(f"🟡 {mc.dead_items} items de code mort")

        # Tests
        if not mc.has_tests:
            score -= 15
            recs.append("🔴 Aucun test trouvé — ajouter pytest")
        elif mc.test_count < 5:
            score -= 5
            recs.append(f"🟡 Peu de tests ({mc.test_count})")

        # Fichiers projet
        if not mc.has_readme:
            score -= 5
            recs.append("🟡 Pas de README")
        if not mc.has_license:
            score -= 5
            recs.append("🟡 Pas de LICENSE")
        if not mc.has_contributing:
            score -= 2
        if not mc.has_entry_point:
            score -= 3
            recs.append("🟡 Pas de point d'entrée clair (main.py, cli.py)")

        # CI
        if not mc.has_ci_config:
            score -= 5
            recs.append("🟡 Pas de CI configurée (GitHub Actions, etc.)")

        # Taille des fichiers
        avg_size = sum(mc.file_sizes) / max(len(mc.file_sizes), 1)
        if avg_size > 50000:
            score -= 10
            recs.append(f"🔴 Fichiers volumineux (moyenne {int(avg_size)} octets)")

        score = max(0, min(100, score))

        # Grade
        if score >= 90:
            grade = "A+"
        elif score >= 80:
            grade = "A"
        elif score >= 70:
            grade = "B"
        elif score >= 60:
            grade = "C"
        elif score >= 40:
            grade = "D"
        else:
            grade = "F"

        return score, grade, recs


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Score de santé d'un projet Python"
    )
    parser.add_argument("project", help="Chemin du projet à analyser")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Détails complets")
    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print(f"Erreur: {args.project} n'est pas un dossier", file=sys.stderr)
        sys.exit(1)

    checker = HealthChecker(args.project)
    report = checker.check()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return

    # Affichage
    m = report.metrics
    print(f"\n{'='*50}")
    print(f"  🩺 HEALTHCHECK — {Path(args.project).name}")
    print(f"{'='*50}")
    print(f"  Score : {report.score}/100  [{report.grade}]")
    print(f"{'='*50}")

    print(f"\n📊 Métriques :")
    print(f"  Fichiers Python   : {len(checker._python_files)}")
    print(f"  Lignes de code    : {m.line_count}")
    print(f"  Fonctions         : {m.function_count}")
    print(f"  Classes            : {m.class_count}")
    print(f"  Docstring coverage : {report.details.get('docstring_coverage', 0):.1f}%")
    print(f"  Complexité moy.    : {report.details.get('avg_cyclomatic', 0)}")
    print(f"  Dépendances       : {m.dependency_count}")
    print(f"  Code mort          : {m.dead_items}")
    print(f"  Tests              : {'✅' if m.has_tests else '❌'} ({m.test_count})")
    print(f"  CI config          : {'✅' if m.has_ci_config else '❌'}")

    if report.recommendations:
        print(f"\n📋 Recommandations :")
        for r in report.recommendations:
            print(f"  {r}")


def register_cli(subparsers):
    """Register healthcheck as a CLI subcommand."""
    p = subparsers.add_parser("healthcheck", help="Score de santé du projet (0-100)")
    p.add_argument("project", help="Chemin du projet")
    p.add_argument("--verbose", "-v", action="store_true", help="Détails complets")
    p.add_argument("--json", action="store_true", help="Sortie JSON")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()