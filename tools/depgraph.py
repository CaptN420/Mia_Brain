#!/usr/bin/env python3
"""
depgraph.py — Analyseur de graphe de dépendances entre modules Python.

Déterministe pur (AST uniquement, zéro LLM).
Détecte : dépendances circulaires, imports morts, modules orphelins, îlots.

Usage:
    python -m tools.depgraph /path/to/project
    python -m tools.depgraph /path/to/project --format dot > deps.dot
    python -m tools.depgraph /path/to/project --format mermaid
    python -m tools.depgraph /path/to/project --circular-only
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
class DepGraphResult:
    """Résultat complet de l'analyse."""
    modules: List[str] = field(default_factory=list)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    circular_groups: List[List[str]] = field(default_factory=list)
    dead_imports: Dict[str, List[str]] = field(default_factory=dict)
    orphan_modules: List[str] = field(default_factory=list)
    islands: List[List[str]] = field(default_factory=list)
    file_count: int = 0
    module_count: int = 0

    def to_dict(self) -> dict:
        return {
            "file_count": self.file_count,
            "module_count": self.module_count,
            "modules": sorted(self.modules),
            "edges": sorted(self.edges),
            "circular_groups": self.circular_groups,
            "dead_imports": self.dead_imports,
            "orphan_modules": sorted(self.orphan_modules),
            "islands": self.islands,
        }


class DepGraphAnalyzer:
    """Analyse les dépendances entre modules Python d'un projet."""

    EXCLUDED_DIRS = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", "egg-info", ".tox", ".mypy_cache",
        ".pytest_cache", ".hypothesis",
    }

    def __init__(self, root: str, exclude_dirs: Optional[Set[str]] = None):
        self.root = Path(root).resolve()
        self.exclude_dirs = exclude_dirs or self.EXCLUDED_DIRS
        self._module_map: Dict[str, Path] = {}       # dotted name → file path
        self._file_to_module: Dict[Path, str] = {}    # file path → dotted name
        self._imports: Dict[str, Set[str]] = defaultdict(set)  # module → set of imported modules
        self._all_python_files: List[Path] = []

    def _is_excluded(self, path: Path) -> bool:
        parts = path.parts
        return any(part in self.exclude_dirs for part in parts)

    def _scan_files(self) -> None:
        """Récupère tous les .py du projet."""
        for pyfile in self.root.rglob("*.py"):
            if self._is_excluded(pyfile):
                continue
            self._all_python_files.append(pyfile)

    def _module_name_from_path(self, path: Path) -> str:
        """Convertit un chemin en nom de module Python.

        Ex : /proj/captn/runtime/base.py → captn.runtime.base
             /proj/tools/depgraph.py → tools.depgraph
             /proj/setup.py → setup
        """
        rel = path.relative_to(self.root)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]  # remove .py
        # skip __pycache__ etc if any
        return ".".join(p for p in parts if p != "__pycache__")

    def _build_module_map(self) -> None:
        """Construit la correspondance fichier → module."""
        for pyfile in self._all_python_files:
            mname = self._module_name_from_path(pyfile)
            self._module_map[mname] = pyfile
            self._file_to_module[pyfile] = mname

    def _extract_imports(self, source: str) -> Set[str]:
        """Extrait les imports Python d'un fichier source."""
        imported: Set[str] = set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return imported

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])  # top-level module
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        return imported

    def _extract_all_imports(self) -> None:
        """Parse chaque fichier et collecte ses imports."""
        for mname, fpath in self._module_map.items():
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue
            raw = self._extract_imports(source)
            # Filtre : ne garder que les modules internes au projet
            internal = {m for m in raw if m in self._module_map}
            self._imports[mname] = internal

    def _detect_circular(self) -> List[List[str]]:
        """Algorithme de Tarjan modifié pour SCC (Strongly Connected Components).

        Dans un graphe orienté, une SCC de taille >= 2 forme une dépendance circulaire.
        """
        graph: Dict[str, List[str]] = {
            m: list(deps) for m, deps in self._imports.items()
        }
        index_counter = [0]
        index: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: Set[str] = set()
        stack: List[str] = []
        sccs: List[List[str]] = []

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack.add(v)

            for w in graph.get(v, []):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) >= 2:
                    sccs.append(sorted(scc))

        for v in graph:
            if v not in index:
                strongconnect(v)

        return sccs

    def _detect_dead_imports(self) -> Dict[str, List[str]]:
        """Trouve les imports de modules qui n'existent pas / n'ont pas de fichier."""
        dead: Dict[str, List[str]] = {}
        for mname, deps in self._imports.items():
            for dep in deps:
                # Les modules peuvent être des packages (import captn → captn/__init__.py)
                # Ou des sous-modules où seul le top-level est importé
                possible = [
                    dep,
                    dep + ".__init__",
                ]
                # Expand: if we import "captn.runtime" but the real module is "captn.runtime.base"
                # the top-level check is "captn" which should exist
                found = any(
                    candidate in self._module_map
                    for candidate in possible
                )
                # Also check if any module starts with dep + "." (package)
                is_package = any(
                    m.startswith(dep + ".") for m in self._module_map
                )
                if not found and not is_package and dep in self._module_map:
                    # Actually it IS in our map — skip
                    pass
                elif not found and not is_package:
                    dead.setdefault(mname, []).append(dep)
        return dead

    def _detect_orphans(self) -> List[str]:
        """Modules qui ne sont importés par personne et ne sont pas des entry points."""
        imported_by: Dict[str, Set[str]] = defaultdict(set)
        for mname, deps in self._imports.items():
            for dep in deps:
                imported_by[dep].add(mname)

        # Entry points typiques
        entry_like = {
            "main", "app", "run", "cli", "launcher",
            "server", "dashboard", "__main__",
        }

        orphans = []
        for mname in self._module_map:
            # Skip __init__.py files and entry-point-like modules
            if mname.endswith(".__init__"):
                continue
            base = mname.split(".")[-1]
            if base in entry_like:
                continue
            if mname not in imported_by:
                orphans.append(mname)

        return sorted(orphans)

    def _detect_islands(self) -> List[List[str]]:
        """Trouve les îlots : composants faiblement connectés (modules qui s'importent
        entre eux mais personne d'autre ne les importe)."""
        graph: Dict[str, Set[str]] = {
            m: set(deps) for m, deps in self._imports.items()
        }
        # Graphe non-orienté pour les composants connexes
        visited: Set[str] = set()
        islands: List[List[str]] = []

        # Inverser pour avoir les dépendants
        dependents: Dict[str, Set[str]] = defaultdict(set)
        for mname, deps in self._imports.items():
            for dep in deps:
                dependents[dep].add(mname)

        all_connected: Dict[str, Set[str]] = {}
        for m in self._module_map:
            all_connected[m] = graph.get(m, set()) | dependents.get(m, set())

        for m in self._module_map:
            if m in visited:
                continue
            component: List[str] = []
            stack = [m]
            while stack:
                v = stack.pop()
                if v in visited:
                    continue
                visited.add(v)
                component.append(v)
                for neighbor in all_connected.get(v, set()):
                    if neighbor not in visited:
                        stack.append(neighbor)
            if len(component) >= 2:
                # Vérifier que ce composant n'est pas le composant principal
                islands.append(sorted(component))

        # Trouver le plus grand composant (le "main")
        if islands:
            max_idx = max(range(len(islands)), key=lambda i: len(islands[i]))
            main_component = set(islands[max_idx])
            islands = [c for i, c in enumerate(islands) if i != max_idx]
            # Ne garder que les petits îlots détachés
            islands = [c for c in islands if len(c) <= 5]

        return islands

    def analyze(self) -> DepGraphResult:
        self._scan_files()
        self._build_module_map()
        self._extract_all_imports()

        edges: List[Tuple[str, str]] = []
        for mname, deps in self._imports.items():
            for dep in deps:
                if dep in self._module_map:
                    edges.append((mname, dep))

        result = DepGraphResult(
            modules=list(self._module_map.keys()),
            edges=edges,
            circular_groups=self._detect_circular(),
            dead_imports=self._detect_dead_imports(),
            orphan_modules=self._detect_orphans(),
            islands=self._detect_islands(),
            file_count=len(self._all_python_files),
            module_count=len(self._module_map),
        )
        return result


def format_mermaid(result: DepGraphResult) -> str:
    """Formatte le graphe en Mermaid flowchart."""
    lines = ["graph TD"]
    for src, dst in sorted(set(result.edges)):
        lines.append(f'    {src.replace(".", "_")} --> {dst.replace(".", "_")}')
    if result.circular_groups:
        lines.append("")
        lines.append("%% Circular dependencies:")
        for group in result.circular_groups:
            label = " / ".join(group)
            lines.append(f"    subgraph Circular[{label}]")
            for m in group:
                safe = m.replace(".", "_")
                lines.append(f"    {safe}")
            lines.append("    end")
    return "\n".join(lines)


def format_dot(result: DepGraphResult) -> str:
    """Formatte le graphe en DOT (Graphviz)."""
    lines = [
        "digraph DepGraph {",
        "    rankdir=LR;",
        '    node [shape=box, style=filled, fillcolor="#f0f0f0"];',
        '    edge [color="#666666", arrowhead=vee];',
    ]
    for src, dst in sorted(set(result.edges)):
        s = src.replace(".", "_")
        d = dst.replace(".", "_")
        lines.append(f'    {s} -> {d};')
    if result.circular_groups:
        for group in result.circular_groups:
            for m in group:
                safe = m.replace(".", "_")
                lines.append(f'    {safe} [color=red, penwidth=2];')
    lines.append("}")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Analyseur de dépendances entre modules Python",
    )
    parser.add_argument("project", help="Chemin du projet à analyser")
    parser.add_argument("--format", "-f", choices=["json", "dot", "mermaid"],
                        default="json", help="Format de sortie")
    parser.add_argument("--circular-only", "-c", action="store_true",
                        help="Afficher uniquement les dépendances circulaires")
    parser.add_argument("--output", "-o", help="Fichier de sortie (défaut: stdout)")
    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print(f"Erreur: {args.project} n'est pas un dossier", file=sys.stderr)
        sys.exit(1)

    analyzer = DepGraphAnalyzer(args.project)
    result = analyzer.analyze()

    if args.circular_only:
        if result.circular_groups:
            print(f"⚠  {len(result.circular_groups)} dépendance(s) circulaire(s) trouvée(s) :")
            for group in result.circular_groups:
                print(f"   {' ↔ '.join(group)}")
        else:
            print("✅ Aucune dépendance circulaire détectée.")
        return

    if args.format == "mermaid":
        output = format_mermaid(result)
    elif args.format == "dot":
        output = format_dot(result)
    else:
        output = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Rapport écrit dans {args.output}", file=sys.stderr)
    else:
        print(output)


def register_cli(subparsers):
    """Register depgraph as a CLI subcommand."""
    p = subparsers.add_parser("depgraph", help="Graphe de dépendances")
    p.add_argument("project", help="Chemin du projet")
    p.add_argument("--format", "-f", choices=["json", "dot", "mermaid"], default="json")
    p.add_argument("--output", "-o", help="Fichier de sortie")
    p.add_argument("--circular-only", "-c", action="store_true")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()