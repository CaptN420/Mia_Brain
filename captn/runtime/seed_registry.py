#!/usr/bin/env python3
"""Seed the fragment registry with existing CaptN tools and workers.

Run once at startup to populate the registry with fragments from:
- DeterministicCoder (docstrings, annotate, normalize)
- tools/eqsolve (symbolic math)
- tools/chemsym (chemistry)
- tools/healthcheck (project scoring)
- tools/depgraph (dependency analysis)
- tools/codebase_map (project mapping)
- tools/testgen (pytest stub generation)
- SyntaxWorker, BugWorker (analysis)

Usage:
    from captn.runtime.seed_registry import seed_all
    seed_all()
"""
from __future__ import annotations

import logging

from captn.runtime.fragment_registry import Fragment, registry

logger = logging.getLogger("seed_registry")


def _lazy_fragment(name: str, input_type: str, output_type: str,
                   description: str, tags: list[str],
                   module_path: str, fn_attr: str,
                   params: dict | None = None) -> Fragment:
    """Create a fragment that imports its function lazily."""
    def _make_fn(mod_path: str, attr: str):
        def fn(input_data=None):
            import importlib
            mod = importlib.import_module(mod_path)
            func = getattr(mod, attr)
            if input_data is not None:
                return func(input_data) if not isinstance(input_data, str) else func(input_data)
            return func()
        return fn
    return Fragment(
        name=name,
        input_type=input_type,
        output_type=output_type,
        fn=_make_fn(module_path, fn_attr),
        description=description,
        tags=tags,
        params=params or {},
    )


def seed_deterministic_coder() -> None:
    """Seed fragments from DeterministicCoder passes."""
    from captn.workers.code_generation.deterministic_coder import DeterministicCoder
    dc = DeterministicCoder()

    def _make_pass(mode: str, desc: str, tags: list[str]):
        def _pass_fn(code: str) -> str:
            result, method = dc.generate(code, modes=[mode])
            if result is None:
                raise ValueError(f"DeterministicCoder.{mode} failed: {method}")
            return result
        return _pass_fn

    registry.register(Fragment(
        name="deterministic_coder.docstrings",
        input_type="code", output_type="code",
        fn=_make_pass("docstrings", "Add missing docstrings", ["docstring", "pep257"]),
        description="Add module/class/function docstrings where missing",
        tags=["code", "docstring", "python", "pep257"],
    ))
    registry.register(Fragment(
        name="deterministic_coder.annotate",
        input_type="code", output_type="code",
        fn=_make_pass("annotate", "Add conservative type hints", ["type-hint", "annotation"]),
        description="Add type annotations from literal defaults (never guesses Any)",
        tags=["code", "type-hint", "python", "annotation"],
    ))
    registry.register(Fragment(
        name="deterministic_coder.normalize",
        input_type="code", output_type="code",
        fn=_make_pass("normalize", "Normalize formatting via ast.unparse",
                       ["format", "normalize", "pep8"]),
        description="Re-format Python code using ast.unparse (stable, reproducible)",
        tags=["code", "format", "python", "normalize"],
    ))
    logger.debug("Seeded 3 DeterministicCoder fragments")


def seed_tools() -> None:
    """Seed fragments from tools/ modules."""
    lazy_fragments = [
        # eqsolve — symbolic math
        _lazy_fragment(
            "tools.eqsolve.simplify", "str", "str",
            "Simplify a mathematical expression using sympy",
            ["math", "sympy", "simplify"],
            "tools.eqsolve", "_simplify_wrapper",
            params={"expr": "str"},
        ),
        _lazy_fragment(
            "tools.eqsolve.solve", "str", "str",
            "Solve an equation for a variable",
            ["math", "sympy", "solve"],
            "tools.eqsolve", "_solve_wrapper",
            params={"expr": "str", "variable": "str"},
        ),
        _lazy_fragment(
            "tools.eqsolve.derive", "str", "str",
            "Compute symbolic derivative",
            ["math", "sympy", "derivative", "calculus"],
            "tools.eqsolve", "_derive_wrapper",
            params={"expr": "str", "variable": "str", "order": "int"},
        ),
        _lazy_fragment(
            "tools.eqsolve.integrate", "str", "str",
            "Compute symbolic integral",
            ["math", "sympy", "integral", "calculus"],
            "tools.eqsolve", "_integrate_wrapper",
            params={"expr": "str", "variable": "str"},
        ),
        # chemsym — chemistry
        _lazy_fragment(
            "tools.chemsym.molar_mass", "str", "str",
            "Compute molar mass of a chemical formula",
            ["chemistry", "molar-mass", "formula"],
            "tools.chemsym", "_molar_mass_wrapper",
            params={"formula": "str"},
        ),
        _lazy_fragment(
            "tools.chemsym.balance", "str", "str",
            "Balance a chemical equation",
            ["chemistry", "reaction", "balance"],
            "tools.chemsym", "_balance_wrapper",
            params={"equation": "str"},
        ),
        # healthcheck — project scoring
        _lazy_fragment(
            "tools.healthcheck.score", "str", "analysis",
            "Score project health (0-100)",
            ["code", "health", "quality", "score"],
            "tools.healthcheck", "_score_wrapper",
            params={"path": "str"},
        ),
        # depgraph — dependency analysis
        _lazy_fragment(
            "tools.depgraph.analyze", "str", "graph",
            "Analyze project dependency graph",
            ["code", "dependencies", "graph", "imports"],
            "tools.depgraph", "_analyze_wrapper",
            params={"path": "str"},
        ),
        # codebase_map — project mapping
        _lazy_fragment(
            "tools.codebase_map.map_project", "str", "manifest",
            "Map project file structure and LOC",
            ["code", "mapping", "structure", "loc"],
            "tools.codebase_map", "_map_project_wrapper",
            params={"path": "str"},
        ),
        # testgen — stub generation
        _lazy_fragment(
            "tools.testgen.generate_stubs", "code", "code",
            "Generate pytest stubs from function signatures",
            ["code", "test", "pytest", "stubs"],
            "tools.testgen", "_generate_stubs_wrapper",
            params={"source": "str", "strategy": "str"},
        ),
    ]
    for f in lazy_fragments:
        registry.register(f)
    logger.debug("Seeded %d tool fragments", len(lazy_fragments))


def seed_workers() -> None:
    """Seed fragments from built-in workers."""
    from captn.runtime.workers import SyntaxWorker, BugWorker
    sw = SyntaxWorker()
    bw = BugWorker()

    def _syntax_check(code: str) -> dict:
        """Minimal syntax check via SyntaxWorker logic."""
        findings = []
        try:
            import ast
            ast.parse(code)
        except SyntaxError as e:
            findings.append(str(e))
        return {"worker": "syntax_worker", "findings": findings, "valid": len(findings) == 0}

    def _bug_check(code: str) -> dict:
        """Minimal bug check via BugWorker logic."""
        findings = []
        if "eval(" in code or "exec(" in code:
            findings.append("Potential security risk: eval() or exec() detected")
        return {"worker": "bug_worker", "findings": findings, "valid": len(findings) == 0}

    registry.register(Fragment(
        name="workers.syntax_check",
        input_type="code", output_type="analysis",
        fn=_syntax_check,
        description="Check Python code for syntax errors",
        tags=["code", "syntax", "analysis", "lint"],
    ))
    registry.register(Fragment(
        name="workers.bug_check",
        input_type="code", output_type="analysis",
        fn=_bug_check,
        description="Check Python code for common bugs and security risks",
        tags=["code", "bug", "security", "analysis"],
    ))
    logger.debug("Seeded 2 worker fragments")


def seed_all() -> int:
    """Seed the fragment registry with all available fragments.

    Returns:
        Total number of fragments registered.
    """
    count_before = registry.count()
    seed_deterministic_coder()
    seed_tools()
    seed_workers()
    count_after = registry.count()
    logger.info("Fragment registry seeded: %d → %d fragments (+%d)",
                count_before, count_after, count_after - count_before)
    return count_after - count_before


__all__ = ["seed_all"]