#!/usr/bin/env python3
"""
apigen.py — Générateur de documentation API depuis les docstrings Python.

Déterministe pur (AST).
Sortie : Markdown structuré arborescent, compatible MkDocs.

Usage:
    python -m tools.apigen /path/to/project
    python -m tools.apigen /path/to/project --output docs/api.md
    python -m tools.apigen /path/to/project --format mkdocs
        -> génère docs/ avec un fichier par module
    python -m tools.apigen /path/to/project --format json
"""

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class ParamDoc:
    name: str
    type: Optional[str] = None
    description: Optional[str] = None
    default: Optional[str] = None


@dataclass
class FunctionDoc:
    name: str
    line: int
    docstring: Optional[str] = None
    params: List[ParamDoc] = field(default_factory=list)
    return_type: Optional[str] = None
    return_desc: Optional[str] = None
    is_async: bool = False
    is_method: bool = False
    decorators: List[str] = field(default_factory=list)
    raises: List[str] = field(default_factory=list)

    def to_markdown(self, level: int = 3) -> str:
        prefix = "#" * level
        lines = []
        lines.append(f"\n{prefix} `{self.name}(...)`\n")

        if self.decorators:
            lines.append(f"*Décorateurs :* `{'`, `'.join(self.decorators)}`\n")
        if self.is_async:
            lines.append("*Async function*\n")

        # Description
        if self.docstring:
            desc = self._extract_description()
            lines.append(desc + "\n")

        # Paramètres
        if self.params:
            lines.append("**Paramètres :**\n")
            lines.append("| Nom | Type | Description | Défaut |")
            lines.append("|-----|------|-------------|--------|")
            for p in self.params:
                ptype = p.type or "*any*"
                pdesc = p.description or ""
                pdef = p.default or ""
                lines.append(f"| `{p.name}` | `{ptype}` | {pdesc} | {pdef} |")
            lines.append("")

        # Retour
        if self.return_type and self.return_type != "None":
            lines.append(f"**Retourne :** `{self.return_type}`")
            if self.return_desc:
                lines.append(f" — {self.return_desc}")
            lines.append("")

        # Raises
        if self.raises:
            lines.append("**Exceptions :**\n")
            for r in self.raises:
                lines.append(f"- `{r}`")
            lines.append("")

        return "\n".join(lines)

    def _extract_description(self) -> str:
        """Extrait la description (première section du docstring avant Args:)."""
        if not self.docstring:
            return ""
        lines = self.docstring.strip().split("\n")
        desc_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("Args:", "Parameters:", "Returns:", "Raises:",
                                     "Example:", "Note:")):
                break
            if stripped:
                desc_lines.append(line)
        return "\n".join(desc_lines).strip()


@dataclass
class ClassDoc:
    name: str
    line: int
    docstring: Optional[str] = None
    methods: List[FunctionDoc] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    bases: List[str] = field(default_factory=list)

    def to_markdown(self, level: int = 2) -> str:
        prefix = "#" * level
        lines = []
        lines.append(f"\n{prefix} Class `{self.name}`\n")

        if self.bases:
            lines.append(f"*Hérite de :* `{'`, `'.join(self.bases)}`\n")
        if self.decorators:
            lines.append(f"*Décorateurs :* `{'`, `'.join(self.decorators)}`\n")

        if self.docstring:
            lines.append(self._extract_description() + "\n")

        lines.append("---\n")

        if self.methods:
            constructor = next((m for m in self.methods if m.name == "__init__"), None)
            others = [m for m in self.methods if m.name != "__init__"]
            if constructor:
                lines.append(constructor.to_markdown(level + 1))
            for method in others:
                if method.name.startswith("__") and method.name != "__init__":
                    continue
                lines.append(method.to_markdown(level + 1))

        return "\n".join(lines)

    def _extract_description(self) -> str:
        if not self.docstring:
            return ""
        lines = self.docstring.strip().split("\n")
        desc_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("Args:", "Parameters:", "Attributes:", "Example:")):
                break
            if stripped:
                desc_lines.append(line)
        return "\n".join(desc_lines).strip()


@dataclass
class ModuleDoc:
    name: str
    path: str
    docstring: Optional[str] = None
    functions: List[FunctionDoc] = field(default_factory=list)
    classes: List[ClassDoc] = field(default_factory=list)
    submodules: List[str] = field(default_factory=list)

    def to_markdown(self, level: int = 1) -> str:
        prefix = "#" * level
        lines = []
        lines.append(f"\n{prefix} Module `{self.name}`\n")
        lines.append(f"*Fichier :* `{self.path}`\n")

        if self.docstring:
            lines.append(self.docstring.strip() + "\n")

        if self.submodules:
            lines.append("**Sous-modules :**\n")
            for sm in sorted(self.submodules):
                lines.append(f"- `{sm}`")
            lines.append("")

        if self.functions:
            lines.append("---\n")
            lines.append(f"\n{'#' * (level + 1)} Fonctions\n")
            for func in self.functions:
                if func.name.startswith("_") and func.name != "__init__":
                    continue
                lines.append(func.to_markdown(level + 2))

        if self.classes:
            lines.append("---\n")
            lines.append(f"\n{'#' * (level + 1)} Classes\n")
            for cls in self.classes:
                lines.append(cls.to_markdown(level + 1))

        return "\n".join(lines)


class ApiGen:
    """Génère la documentation API d'un projet Python."""

    EXCLUDED_DIRS = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", "egg-info", ".tox", ".mypy_cache",
        ".pytest_cache", ".hypothesis",
    }

    def __init__(self, root: str, exclude_dirs: Optional[Set[str]] = None):
        self.root = Path(root).resolve()
        self.exclude_dirs = exclude_dirs or self.EXCLUDED_DIRS
        self._modules: Dict[str, ModuleDoc] = {}

    def _is_excluded(self, path: Path) -> bool:
        return any(p in self.exclude_dirs for p in path.parts)

    def _module_name(self, path: Path) -> str:
        rel = path.relative_to(self.root)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        return ".".join(p for p in parts if p != "__pycache__")

    def _analyze_file(self, path: Path) -> Optional[ModuleDoc]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return None

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return None

        rel = path.relative_to(self.root).as_posix()
        mname = self._module_name(path)
        module = ModuleDoc(name=mname, path=rel)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str) and not module.docstring:
                    module.docstring = node.value.value.strip()
            elif isinstance(node, ast.FunctionDef):
                func = self._extract_function(node)
                module.functions.append(func)
            elif isinstance(node, ast.AsyncFunctionDef):
                func = self._extract_function(node, async_=True)
                module.functions.append(func)
            elif isinstance(node, ast.ClassDef):
                cls = self._extract_class(node)
                module.classes.append(cls)

        return module if (module.functions or module.classes or module.docstring) else None

    def _extract_function(self, node: ast.FunctionDef, async_: bool = False) -> FunctionDoc:
        func = FunctionDoc(
            name=node.name,
            line=node.lineno,
            docstring=ast.get_docstring(node),
            is_async=async_,
            is_method=False,
            decorators=[self._decorator_name(d) for d in node.decorator_list],
        )
        self._parse_params(node, func)
        if node.returns:
            func.return_type = self._type_str(node.returns)
        # Parse Raises: from docstring
        if func.docstring:
            func.raises = self._extract_raises(func.docstring)
            func.return_desc = self._extract_return_desc(func.docstring)
            func.params = self._merge_param_docs(func.params, func.docstring)
        return func

    def _extract_class(self, node: ast.ClassDef) -> ClassDoc:
        cls = ClassDoc(
            name=node.name,
            line=node.lineno,
            docstring=ast.get_docstring(node),
            bases=[self._type_str(b) for b in node.bases if isinstance(b, ast.Name)],
            decorators=[self._decorator_name(d) for d in node.decorator_list],
        )
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                method = self._extract_function(item)
                method.is_method = True
                cls.methods.append(method)
            elif isinstance(item, ast.AsyncFunctionDef):
                method = self._extract_function(item, async_=True)
                method.is_method = True
                cls.methods.append(method)
        return cls

    def _parse_params(self, node, func: FunctionDoc) -> None:
        args = node.args
        defaults = [None] * (len(args.args) - len(args.defaults)) + [
            self._type_str(d) for d in args.defaults
        ]
        for i, arg in enumerate(args.args):
            if arg.arg == "self":
                continue
            p = ParamDoc(
                name=arg.arg,
                type=self._type_str(arg.annotation) if arg.annotation else None,
                default=defaults[i] if i < len(defaults) else None,
            )
            func.params.append(p)
        for arg in args.kwonlyargs:
            p = ParamDoc(
                name=arg.arg,
                type=self._type_str(arg.annotation) if arg.annotation else None,
            )
            func.params.append(p)

    def _type_str(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Subscript):
            value = self._type_str(node.value)
            slice_str = self._type_str(node.slice)
            return f"{value}[{slice_str}]"
        if isinstance(node, ast.Tuple):
            return f"Tuple[{', '.join(self._type_str(e) for e in node.elts)}]"
        if isinstance(node, ast.Attribute):
            return f"{self._type_str(node.value)}.{node.attr}"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return f"Union[{self._type_str(node.left)}, {self._type_str(node.right)}]"
        return "Any"

    def _decorator_name(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return f"{node.func.id}(...)"
        if isinstance(node, ast.Attribute):
            return f"{self._type_str(node)}"
        return str(getattr(node, "id", "?"))

    def _extract_raises(self, docstring: str) -> List[str]:
        raises = []
        in_raises = False
        for line in docstring.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Raises:"):
                in_raises = True
                continue
            if in_raises:
                if stripped.startswith(("Returns:", "Example:", "Note:", "Args:")):
                    in_raises = False
                    continue
                if stripped and not stripped.startswith(" "):
                    in_raises = False
                    continue
                if ":" in stripped:
                    raise_name = stripped.split(":")[0].strip().strip("`")
                    if raise_name:
                        raises.append(raise_name)
        return raises

    def _extract_return_desc(self, docstring: str) -> Optional[str]:
        in_returns = False
        lines = []
        for line in docstring.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Returns:"):
                in_returns = True
                continue
            if in_returns:
                if stripped.startswith(("Raises:", "Example:", "Note:", "Args:")):
                    break
                if stripped:
                    lines.append(stripped)
        return " ".join(lines) if lines else None

    def _merge_param_docs(self, params: List[ParamDoc], docstring: str) -> List[ParamDoc]:
        """Fusionne les types AST avec les descriptions du docstring."""
        param_descs: Dict[str, str] = {}
        in_args = False
        for line in docstring.split("\n"):
            stripped = line.strip()
            if stripped.startswith(("Args:", "Parameters:")):
                in_args = True
                continue
            if in_args:
                if stripped.startswith(("Returns:", "Raises:", "Example:", "Note:")):
                    break
                if stripped:
                    parts = stripped.split(":", 1)
                    if len(parts) == 2:
                        param_name = parts[0].strip()
                        desc = parts[1].strip()
                        param_descs[param_name] = desc
        for p in params:
            if p.name in param_descs:
                p.description = param_descs[p.name]
        return params

    def generate(self) -> Dict[str, ModuleDoc]:
        """Analyse le projet et retourne tous les modules documentés."""
        modules: Dict[str, ModuleDoc] = {}
        for pyfile in sorted(self.root.rglob("*.py")):
            if self._is_excluded(pyfile):
                continue
            module = self._analyze_file(pyfile)
            if module:
                modules[module.name] = module
        self._modules = modules
        return modules

    def to_markdown(self) -> str:
        """Documentation complète en Markdown."""
        if not self._modules:
            self.generate()

        lines = [
            f"# API Reference — `{self.root.name}`\n",
            f"*Généré par apigen.py*\n",
            "---\n",
        ]

        for mname in sorted(self._modules.keys()):
            module = self._modules[mname]
            lines.append(module.to_markdown(level=1))
            lines.append("\n---\n")

        return "\n".join(lines)

    def to_mkdocs(self, output_dir: str) -> None:
        """Génère une arborescence docs/ pour MkDocs."""
        if not self._modules:
            self.generate()

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Index
        index_lines = [
            "# API Documentation\n",
            f"*Project: {self.root.name}*\n",
            "## Modules\n",
        ]
        for mname in sorted(self._modules.keys()):
            file_path = mname.replace(".", "/") + ".md"
            index_lines.append(f"- [`{mname}`]({file_path}) — `{self._modules[mname].path}`")
        (out / "index.md").write_text("\n".join(index_lines), encoding="utf-8")

        for mname, module in self._modules.items():
            parts = mname.split(".")
            file_path = out / "/".join(parts[:-1]) / f"{parts[-1]}.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            md = module.to_markdown(level=1)
            file_path.write_text(md, encoding="utf-8")

        print(f"Documentation MkDocs générée dans {out}/", file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Générateur de documentation API depuis les docstrings"
    )
    parser.add_argument("project", help="Chemin du projet à documenter")
    parser.add_argument("--output", "-o", help="Fichier Markdown de sortie")
    parser.add_argument("--format", "-f", choices=["markdown", "mkdocs", "json"],
                        default="markdown", help="Format de sortie")
    parser.add_argument("--mkdocs-dir", default="docs",
                        help="Répertoire de sortie pour le format mkdocs")
    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print(f"Erreur: {args.project} n'est pas un dossier", file=sys.stderr)
        sys.exit(1)

    apigen = ApiGen(args.project)

    if args.format == "mkdocs":
        apigen.to_mkdocs(args.mkdocs_dir)
        print(f"Fichiers générés dans {args.mkdocs_dir}/", file=sys.stderr)
    elif args.format == "json":
        modules = apigen.generate()
        # Sérialisation simple
        data = {}
        for mname, m in modules.items():
            data[mname] = {
                "path": m.path,
                "docstring": m.docstring,
                "functions": [f.name for f in m.functions if not f.name.startswith("_")],
                "classes": [c.name for c in m.classes],
            }
        output = json.dumps(data, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output)
    else:
        output = apigen.to_markdown()
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Documentation écrite dans {args.output}", file=sys.stderr)
        else:
            print(output)


def register_cli(subparsers):
    """Register apigen as a CLI subcommand."""
    p = subparsers.add_parser("apigen", help="Documentation API depuis docstrings")
    p.add_argument("project", help="Chemin du projet")
    p.add_argument("--format", "-f", choices=["markdown", "mkdocs", "json"], default="markdown")
    p.add_argument("--output", "-o", help="Fichier de sortie")
    p.add_argument("--mkdocs-dir", default="docs")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()