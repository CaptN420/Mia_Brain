#!/usr/bin/env python3
"""
testgen.py — Générateur de stubs pytest à partir de signatures Python.

Déterministe pur (AST + type hints).
Génère des tests paramétrés, property-based (hypothesis), fixtures.

Usage:
    python -m tools.testgen /path/to/file.py
    python -m tools.testgen /path/to/module.py --output tests/test_module.py
    python -m tools.testgen /path/to/module.py --with-hypothesis
    python -m tools.testgen /path/to/module.py --strategy
        # --strategy: 'parametrize' (défaut), 'hypothesis', 'fixture'
"""

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class ParamInfo:
    name: str
    type_hint: Optional[str]
    default: Optional[str] = None
    has_default: bool = False

    def is_simple(self) -> bool:
        """Types simples : int, float, str, bool, None, Optional[simples]"""
        if not self.type_hint:
            return True  # any
        simple = {"int", "float", "str", "bool", "None", "Any"}
        base = self.type_hint.replace("Optional[", "").replace("List[", "").replace("Dict[", "")
        base = base.replace("Set[", "").replace("Tuple[", "").replace("]", "").strip()
        # Handle Union[..., None] → split
        base = base.split("Union")[0].strip()
        return base in simple or not base


@dataclass
class FunctionInfo:
    name: str
    params: List[ParamInfo]
    return_type: Optional[str]
    is_async: bool
    is_method: bool = False
    docstring: Optional[str] = None
    line: int = 0

    def is_pure(self) -> bool:
        """Heuristique : functions sans side-effects évidents."""
        keywords = {"print", "write", "open(", "input(", "logger", ".save", ".delete"}
        return not keywords

    def generate_parametrize_stub(self, idx: int) -> str:
        """Génère un test paramétré pytest."""
        lines = []
        lines.append("")

        # Docstring test
        if self.docstring:
            short = self.docstring.split("\n")[0]
            lines.append(f"# {short}")

        # Paramètres pour parametrize
        param_names = [p.name for p in self.params if p.name != "self"]

        if param_names:
            params_str = ", ".join(param_names)
            lines.append("@pytest.mark.parametrize(")
            lines.append(f"    \"{params_str}\",")
            lines.append("    [")
            # Exemples auto
            examples = self._generate_examples()
            if len(examples) > 3:
                examples = examples[:3]
            for ex in examples:
                vals = ", ".join(ex)
                lines.append(f"        ({vals}),")
            lines.append("    ]")
            lines.append(")")
            lines.append(f"def test_{self.name}_{idx}({params_str}):")
            if self.return_type and self.return_type != "None":
                lines.append(f"    result = function_under_test({params_str})")
                lines.append(f"    assert result is not None  # TODO: valeur attendue")
            else:
                if param_names:
                    lines.append(f"    function_under_test({params_str})")
                else:
                    lines.append("    function_under_test()")
                lines.append("    # TODO: assert side-effect / output")
        else:
            lines.append(f"def test_{self.name}_{idx}():")
            lines.append("    # function_under_test()")
            lines.append("    pass")

        return "\n".join(lines)

    def _generate_examples(self) -> List[List[str]]:
        """Génère des exemples basiques pour les paramètres."""
        examples = []
        def _example_for(t: Optional[str]) -> str:
            if not t:
                return "None"
            t_clean = t.replace("Optional[", "").replace("]", "")
            if "int" in t_clean and "str" not in t_clean:
                return "0"
            if "float" in t_clean:
                return "0.0"
            if "str" in t_clean:
                return '"test"'
            if "bool" in t_clean:
                return "True"
            if "list" in t_clean.lower() or "List" in t_clean:
                return "[]"
            if "dict" in t_clean.lower() or "Dict" in t_clean:
                return "{}"
            if "Path" in t_clean:
                return 'Path("/tmp/test")'
            if "None" in t_clean:
                return "None"
            if "Any" in t_clean:
                return '"any_value"'
            return "None"

        # Default: one simple example
        ex1 = [_example_for(p.type_hint) for p in self.params if p.name != "self"]
        examples.append(ex1)

        # Alternative values
        ex2 = []
        for p in self.params:
            if p.name == "self":
                continue
            t = p.type_hint.replace("Optional[", "").replace("]", "") if p.type_hint else ""
            if "int" in t:
                ex2.append("42")
            elif "str" in t:
                ex2.append('""')
            elif "float" in t:
                ex2.append("-1.0")
            elif "bool" in t:
                ex2.append("False")
            elif "None" in t:
                ex2.append("None")
            elif "list" in t.lower():
                ex2.append('["a", "b"]')
            else:
                ex2.append("None")
        if ex2 != ex1:
            examples.append(ex2)

        return examples

    def generate_hypothesis_stub(self, idx: int) -> str:
        """Génère un test avec hypothesis."""
        lines = []
        lines.append("")
        lines.append(f"@given(")
        param_lines = []
        for p in self.params:
            if p.name == "self":
                continue
            strat = self._hypothesis_strategy(p.type_hint)
            param_lines.append(f"    st.{strat}")
        lines.append(",\n".join(param_lines))
        lines.append(")")
        param_names = ", ".join(
            p.name for p in self.params if p.name != "self"
        )
        lines.append(f"def test_{self.name}_{idx}({param_names}):")
        if self.return_type and self.return_type != "None":
            lines.append(f"    result = function_under_test({param_names})")
            lines.append("    assert isinstance(result, type(result))  # type-safe")
        else:
            lines.append(f"    function_under_test({param_names})")
        return "\n".join(lines)

    def _hypothesis_strategy(self, t: Optional[str]) -> str:
        if not t:
            return "just(None)"
        t_clean = t.replace("Optional[", "").replace("]", "")
        if "int" in t_clean and "str" not in t_clean:
            return "integers()"
        if "float" in t_clean:
            return "floats()"
        if "str" in t_clean or "Str" in t_clean:
            return "text()"
        if "bool" in t_clean:
            return "booleans()"
        if "list" in t_clean.lower() or "List" in t_clean:
            inner = t_clean.split("[")[-1].rstrip("]") if "[" in t_clean else "integers()"
            if inner and "int" in inner:
                inner_strat = "integers()"
            else:
                inner_strat = "text()"
            return f"lists({inner_strat})"
        if "dict" in t_clean.lower() or "Dict" in t_clean:
            return "dictionaries(text(), integers())"
        if "bytes" in t_clean:
            return "binary()"
        if "None" in t_clean:
            return "just(None)"
        return "just(None)"

    def generate_fixture_stub(self, idx: int) -> str:
        """Génère un test avec fixture."""
        lines = []
        lines.append("")
        lines.append("@pytest.fixture")
        params = [p for p in self.params if p.name != "self"]
        if params:
            pstr = ", ".join(f"{p.name}: {p.type_hint or 'Any'}" for p in params)
            lines.append(f"def _{self.name}_fixture_{idx}({pstr}):")
            lines.append(f'    """Fixture pour {self.name}."""')
            lines.append(f"    result = function_under_test({pstr})")
            if self.return_type and self.return_type != "None":
                lines.append("    return result")
            lines.append("")
        lines.append(f"def test_{self.name}_{idx}():")
        lines.append("    # TODO: utiliser la fixture")
        lines.append("    pass")
        return "\n".join(lines)


@dataclass
class ClassInfo:
    name: str
    methods: List[FunctionInfo] = field(default_factory=list)
    line: int = 0
    docstring: Optional[str] = None


@dataclass
class ModuleInfo:
    path: Path
    functions: List[FunctionInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    has_async: bool = False
    file_docstring: Optional[str] = None


class TestGenAnalyzer:
    """Parse un fichier Python et extrait les signatures pour génération de tests."""

    def __init__(self, path: str):
        self.path = Path(path).resolve()
        self.text = self.path.read_text(encoding="utf-8", errors="replace")

    def analyze(self) -> ModuleInfo:
        tree = ast.parse(self.text)
        info = ModuleInfo(path=self.path)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                func = self._extract_function(node)
                info.functions.append(func)
                if func.is_async:
                    info.has_async = True
            elif isinstance(node, ast.AsyncFunctionDef):
                func = self._extract_function(node, async_=True)
                info.functions.append(func)
                info.has_async = True
            elif isinstance(node, ast.ClassDef):
                cls_info = ClassInfo(
                    name=node.name,
                    line=node.lineno,
                    docstring=ast.get_docstring(node),
                )
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        is_async = isinstance(item, ast.AsyncFunctionDef)
                        method = self._extract_function(item, async_=is_async, is_method=True)
                        cls_info.methods.append(method)
                        if is_async:
                            info.has_async = True
                info.classes.append(cls_info)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str) and not info.file_docstring:
                    info.file_docstring = node.value.value

        return info

    def _extract_function(self, node: ast.FunctionDef, async_: bool = False,
                          is_method: bool = False) -> FunctionInfo:
        params = []
        args = node.args
        for arg in args.args + args.kwonlyargs:
            type_str = None
            if arg.annotation:
                type_str = self._ast_to_type_str(arg.annotation)
            has_default = False
            default_str = None
            # Check defaults
            defaults = args.defaults or []
            kw_defaults = args.kw_defaults or []
            # positional arg with default?
            pos_idx = args.args.index(arg) if arg in args.args else -1
            if pos_idx >= 0:
                offset = len(args.args) - len(defaults)
                if offset >= 0 and (pos_idx - offset) >= 0 and (pos_idx - offset) < len(defaults):
                    has_default = True
                    default_str = self._ast_to_type_str(defaults[pos_idx - offset])
            if arg in args.kwonlyargs:
                kidx = args.kwonlyargs.index(arg)
                if kidx < len(kw_defaults) and kw_defaults[kidx] is not None:
                    has_default = True
                    default_str = self._ast_to_type_str(kw_defaults[kidx])

            params.append(ParamInfo(
                name=arg.arg,
                type_hint=type_str,
                default=default_str,
                has_default=has_default,
            ))

        return_type = None
        if node.returns:
            return_type = self._ast_to_type_str(node.returns)

        doc = ast.get_docstring(node)

        return FunctionInfo(
            name=node.name,
            params=params,
            return_type=return_type,
            is_async=async_,
            is_method=is_method,
            docstring=doc,
            line=node.lineno,
        )

    def _ast_to_type_str(self, node) -> str:
        """Convertit un AST type annotation en string, le plus fidèlement possible."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Subscript):
            value = self._ast_to_type_str(node.value)
            slice_str = self._ast_to_type_str(node.slice)
            return f"{value}[{slice_str}]"
        if isinstance(node, ast.Tuple):
            elts = ", ".join(self._ast_to_type_str(e) for e in node.elts)
            return f"Tuple[{elts}]"
        if isinstance(node, ast.Attribute):
            return f"{self._ast_to_type_str(node.value)}.{node.attr}"
        if isinstance(node, ast.Starred):
            return f"*{self._ast_to_type_str(node.value)}"
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.BitOr):
                left = self._ast_to_type_str(node.left)
                right = self._ast_to_type_str(node.right)
                return f"Union[{left}, {right}]"
            return f"{self._ast_to_type_str(node.left)} op {self._ast_to_type_str(node.right)}"
        if isinstance(node, ast.List):
            elts = ", ".join(self._ast_to_type_str(e) for e in node.elts)
            return f"List[{elts}]"
        return "Any"


def generate_stubs(info: ModuleInfo, strategy: str = "parametrize",
                   with_hypothesis: bool = False) -> str:
    """Génère le code de test complet."""
    lines = [
        '"""Tests générés pour {}."""'.format(info.path.name),
        "",
        "import pytest",
    ]

    if strategy == "hypothesis" or with_hypothesis:
        lines.append("from hypothesis import given")
        lines.append("import hypothesis.strategies as st")

    lines.append("")
    lines.append("# Module under test")
    mod_name = info.path.stem
    lines.append(f"from {mod_name} import (")
    # Import symbols
    imported = []
    for f in info.functions:
        if not f.name.startswith("_"):
            imported.append(f"    {f.name},")
    for c in info.classes:
        imported.append(f"    {c.name},")
    if "function_under_test" not in [f.name for f in info.functions]:
        lines.append(f"    # Ajoutez vos imports ici")
    else:
        lines.extend(imported)
    lines.append(")")
    lines.append("")

    idx = [0]

    def _gen_function(func: FunctionInfo) -> str:
        idx[0] += 1
        if strategy == "hypothesis":
            return func.generate_hypothesis_stub(idx[0])
        elif strategy == "fixture":
            return func.generate_fixture_stub(idx[0])
        else:
            return func.generate_parametrize_stub(idx[0])

    # Functions
    for func in info.functions:
        if func.name.startswith("_"):
            continue
        stub = _gen_function(func)
        lines.append(stub)
        lines.append("")

    # Classes
    for cls in info.classes:
        lines.append("")
        if cls.docstring:
            lines.append(f"# {cls.docstring.split(chr(10))[0]}")
        lines.append(f"class Test{cls.name}:")
        lines.append(f'    """Tests for {cls.name}."""')
        for method in cls.methods:
            if method.name.startswith("_"):
                continue
            method.is_method = True
            # Rewrite: use self for fixture
            stub_lines = _gen_function(method).split("\n")
            for sl in stub_lines:
                if sl.strip():
                    lines.append("    " + sl)
            lines.append("")
        lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Générateur de stubs pytest à partir de signatures Python"
    )
    parser.add_argument("file", help="Fichier Python à analyser")
    parser.add_argument("--output", "-o", help="Fichier de sortie (défaut: stdout)")
    parser.add_argument("--strategy", "-s", choices=["parametrize", "hypothesis", "fixture"],
                        default="parametrize", help="Stratégie de génération")
    parser.add_argument("--with-hypothesis", "-H", action="store_true",
                        help="Utiliser hypothesis en plus de parametrize")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"Erreur: {args.file} n'est pas un fichier", file=sys.stderr)
        sys.exit(1)

    analyzer = TestGenAnalyzer(args.file)
    info = analyzer.analyze()

    output = generate_stubs(info, strategy=args.strategy, with_hypothesis=args.with_hypothesis)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Tests générés dans {args.output}", file=sys.stderr)
    else:
        print(output)


def register_cli(subparsers):
    """Register testgen as a CLI subcommand."""
    p = subparsers.add_parser("testgen", help="Générer des stubs pytest")
    p.add_argument("file", help="Fichier source Python")
    p.add_argument("--output", "-o", help="Fichier de test de sortie")
    p.add_argument("--strategy", "-s", choices=["parametrize", "hypothesis", "fixture"],
                      default="parametrize")
    p.add_argument("--with-hypothesis", "-H", action="store_true")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()