#!/usr/bin/env python3
"""
fragment_registry.py — Registry of deterministic code fragments + typed assembler.

Concept (inspired by O1-O / FixedCode / neural-codegen):
    Instead of asking an LLM to write code from scratch, we maintain a registry of
    pre-verified Python code fragments (functions, classes, transforms) with typed
    input/output signatures. The Assembler composes fragments type-safely into
    executable programs — all deterministic, zero LLM tokens.

Type system (8 colors):
    COLOR          MEANING                        EXAMPLES
    ──────────────────────────────────────────────────────────
    str            string / text output           JSON, YAML, docstring
    int            integer / counter / line        line numbers, counts
    code           Python source code             AST node, source text
    ast            parsed AST                     ast.Module, ast.AST
    analysis       structured findings            dict[severity, line, msg]
    manifest       file listing / project map     list[Path], dict[str, …]
    graph          dependency / relation graph     adjacency dict, edges
    any            polymorphic / generic           passthrough, wrapper

Usage:
    from captn.runtime.fragment_registry import registry, Assembler

    # Register a fragment
    registry.register(Fragment(
        name="deterministic_coder.docstrings",
        input_type="code", output_type="code",
        fn=deterministic_coder_instance.generate,
        description="Add missing docstrings to Python code",
        tags=["code", "docstring", "python"],
    ))

    # Assemble a program from a description
    assembler = Assembler(registry)
    result = assembler.assemble("add docstrings to this code", code=source)
    if result.success:
        print(result.code)
"""

from __future__ import annotations

import ast
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("fragment_registry")

# ── Type system ──────────────────────────────────────────────────────
TYPE_COLORS = frozenset({
    "str", "int", "code", "ast", "analysis", "manifest", "graph", "any",
})

# Color compatibility matrix:
# - "any" connects to everything
# - Otherwise, exact match required (or auto-converter declared)
COLOR_COMPATIBLE = {
    "any": set(TYPE_COLORS),
    "str": {"str", "any"},
    "int": {"int", "any"},
    "code": {"code", "ast", "str", "any"},
    "ast": {"ast", "code", "any"},
    "analysis": {"analysis", "str", "any"},
    "manifest": {"manifest", "str", "any"},
    "graph": {"graph", "str", "any"},
}

# Color converters — built-in transformations between colors
COLOR_CONVERTERS: Dict[Tuple[str, str], Callable[[Any], Any]] = {
    ("ast", "code"): lambda tree: ast.unparse(tree),
    ("code", "ast"): lambda src: ast.parse(src) if isinstance(src, str) else src,
    ("str", "code"): lambda s: s,
    ("code", "str"): lambda s: s,
    ("analysis", "str"): lambda d: str(d),
    ("manifest", "str"): lambda d: str(d),
    ("graph", "str"): lambda d: str(d),
}


def compatible(output_color: str, input_color: str) -> bool:
    """Check if output_color can feed into input_color."""
    return input_color in COLOR_COMPATIBLE.get(output_color, {output_color})


# ── Fragment definition ──────────────────────────────────────────────
@dataclass
class Fragment:
    """A deterministic code fragment with typed interface.

    Attributes:
        name:        Unique identifier (e.g. ``deterministic_coder.docstrings``)
        input_type:  Expected input color
        output_type: Produced output color
        fn:          Callable that performs the transform (pure, deterministic)
        description: Human-readable description
        tags:        Keywords for routing / matching
        params:      Optional parameter schema {name: type}
    """
    name: str
    input_type: str
    output_type: str
    fn: Callable
    description: str = ""
    tags: List[str] = field(default_factory=list)
    params: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.input_type in TYPE_COLORS, \
            f"Unknown input_type {self.input_type!r}, must be one of {TYPE_COLORS}"
        assert self.output_type in TYPE_COLORS, \
            f"Unknown output_type {self.output_type!r}, must be one of {TYPE_COLORS}"


# ── Registry ─────────────────────────────────────────────────────────
class FragmentRegistry:
    """Registry of all deterministic fragments."""

    def __init__(self) -> None:
        self._fragments: Dict[str, Fragment] = {}

    def register(self, fragment: Fragment) -> None:
        """Register a fragment."""
        if fragment.name in self._fragments:
            logger.warning("Overwriting existing fragment: %s", fragment.name)
        self._fragments[fragment.name] = fragment
        logger.debug("Registered fragment: %s (%s → %s)",
                     fragment.name, fragment.input_type, fragment.output_type)

    def get(self, name: str) -> Optional[Fragment]:
        return self._fragments.get(name)

    def all(self) -> List[Fragment]:
        return list(self._fragments.values())

    def find(
        self,
        *,
        input_type: Optional[str] = None,
        output_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        query: str = "",
    ) -> List[Fragment]:
        """Search fragments by type, tags, or keyword query."""
        results = list(self._fragments.values())

        if input_type:
            results = [f for f in results if compatible(f.output_type, input_type)]
        if output_type:
            results = [f for f in results if compatible(f.input_type, output_type)]
        if tags:
            tag_set = set(tags)
            results = [f for f in results if tag_set & set(f.tags)]
        if query:
            q = query.lower()
            results = [
                f for f in results
                if q in f.name.lower() or q in f.description.lower()
                or any(q in t for t in f.tags)
            ]
        return results

    def route(self, description: str, top_k: int = 3) -> List[Fragment]:
        """Score fragments by keyword match, return top_k (like SmartRouter)."""
        import re
        words = {w for w in re.findall(r"[a-z]{3,}", description.lower())}
        scored: List[Tuple[float, Fragment]] = []
        for f in self._fragments.values():
            score = 0.0
            name_words = set(f.name.lower().split("."))
            tag_words = set(f.tags)
            desc_words = set(re.findall(r"[a-z]{3,}", f.description.lower()))
            # Name match (highest weight)
            score += sum(5.0 for w in words if w in name_words)
            # Tag match
            score += sum(3.0 for w in words if w in tag_words)
            # Description overlap
            score += sum(1.0 for w in words if w in desc_words)
            if score > 0:
                scored.append((score, f))
        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored[:top_k]]

    def count(self) -> int:
        return len(self._fragments)

    def status(self) -> Dict[str, Any]:
        return {
            "total_fragments": self.count(),
            "by_input_type": {
                t: sum(1 for f in self._fragments.values() if f.input_type == t)
                for t in TYPE_COLORS
            },
            "by_output_type": {
                t: sum(1 for f in self._fragments.values() if f.output_type == t)
                for t in TYPE_COLORS
            },
        }


# ── Assembler ────────────────────────────────────────────────────────
@dataclass
class AssemblyResult:
    """Result of a fragment assembly."""
    success: bool
    code: Optional[str] = None
    fragments_used: List[str] = field(default_factory=list)
    error: Optional[str] = None
    pipeline: List[str] = field(default_factory=list)


class Assembler:
    """Composes fragments into executable code.

    Given a description and input data, the assembler:
    1. Routes to matching fragments
    2. Type-checks compatibility between consecutive fragments
    3. Inserts color converters where needed
    4. Produces assembled Python code as a string
    """

    def __init__(self, registry: FragmentRegistry) -> None:
        self.registry = registry

    def assemble(
        self,
        description: str,
        *,
        input_data: Any = None,
        input_type: str = "code",
        target_output_type: str = "code",
        top_k: int = 3,
    ) -> AssemblyResult:
        """Build a pipeline from description and execute it."""
        fragments = self.registry.route(description, top_k=top_k)
        if not fragments:
            return AssemblyResult(
                success=False,
                error=f"No fragments matched description: {description!r}",
            )

        # Build a pipeline: chain fragments type-safely
        pipeline: List[Fragment] = []
        current_type = input_type

        for f in fragments:
            if compatible(current_type, f.input_type):
                pipeline.append(f)
                current_type = f.output_type
                if compatible(current_type, target_output_type):
                    break
            else:
                # Try color converter
                converter = COLOR_CONVERTERS.get((current_type, f.input_type))
                if converter:
                    pipeline.append(f)
                    current_type = f.output_type
                # else: skip this fragment, type mismatch

        if not pipeline:
            return AssemblyResult(
                success=False,
                error=f"Could not build pipeline from {input_type} → {target_output_type}",
            )

        # Execute the pipeline
        data = input_data
        used_names: List[str] = []
        try:
            for f in pipeline:
                # Insert converter before fragment if needed
                if data is not None and not compatible(
                    _infer_type(data), f.input_type
                ):
                    converter = COLOR_CONVERTERS.get(
                        (_infer_type(data), f.input_type)
                    )
                    if converter:
                        data = converter(data)
                data = f.fn(data) if data is not None else f.fn()
                used_names.append(f.name)
        except Exception as e:
            failed_fragment = used_names[-1] if used_names else "unknown"
            return AssemblyResult(
                success=False,
                error=f"Fragment {failed_fragment} failed: {e}",
                fragments_used=used_names,
                pipeline=[f.name for f in pipeline],
            )

        # Assemble final code representation
        code = self._emit_code(pipeline, used_names, input_data)

        return AssemblyResult(
            success=True,
            code=code,
            fragments_used=used_names,
            pipeline=[f.name for f in pipeline],
        )

    def _emit_code(
        self,
        pipeline: List[Fragment],
        used_names: List[str],
        input_data: Any,
    ) -> str:
        """Produce a human-readable summary of the assembly pipeline."""
        lines = [
            "# ── Assembled by CaptN Fragment Registry ──",
            f"# Pipeline: {' → '.join(used_names)}",
            f"# Input type: {_infer_type(input_data) if input_data is not None else 'none'}",
            "# ─────────────────────────────────────────",
        ]
        for i, name in enumerate(used_names):
            f = self.registry.get(name)
            if f:
                lines.append(f"# Step {i+1}: {f.name}  ({f.description})")
        lines.append("")
        if input_data is not None:
            lines.append("# Input:")
            lines.append(str(input_data)[:500])
        return "\n".join(lines)


def _infer_type(data: Any) -> str:
    """Guess color type from a Python value."""
    if data is None:
        return "any"
    if isinstance(data, ast.AST):
        return "ast"
    if isinstance(data, str):
        return "str"
    if isinstance(data, int):
        return "int"
    if isinstance(data, dict):
        return "analysis"
    if isinstance(data, list):
        return "manifest"
    return "any"


# ── Create the global default registry ──────────────────────────────
registry = FragmentRegistry()

__all__ = [
    "Fragment", "FragmentRegistry", "Assembler", "AssemblyResult",
    "registry", "TYPE_COLORS", "compatible", "COLOR_CONVERTERS",
]