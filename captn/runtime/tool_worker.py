#!/usr/bin/env python3
"""
tool_worker.py — CaptN-BRAIN Worker that wraps the unified tool router.

Allows any Worker, Thinker, or the Orchestrator to call deterministic tools
via the message bus.

NEW: Auto-detects the correct tool from a natural language description
when no explicit tool_name is provided.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from captn.runtime.base import Message, Plugin
from captn.cli._tool_router import run_tool, list_all_tools, list_tools_by_domain
from captn.runtime._tool_domains import (
    TOOL_DOMAIN_MAP, DOMAIN_KEYWORDS, DOMAIN_TOOL_COUNTS,
)

# Try to load Intent Parser for domain-aware tool detection
_intent_parser: Any = None
try:
    import captn.runtime.intent_parser as _intent_parser_module  # type: ignore[import-not-found]
    _intent_parser = _intent_parser_module
    _intent_parser_available = True
except Exception:
    _intent_parser_available = False

logger = logging.getLogger("ToolWorker")

# ── Tool detection mapping: keywords → (tool_name, param_key, default_kwargs) ──
# When a query contains these keywords, the tool is auto-selected
TOOL_DETECTION: Dict[str, List[Dict[str, Any]]] = {
    # ── Code Review (ruff + pylint + flake8 + bandit + mypy) ──
    "review": [
        {"tool": "review_review", "param": "code", "score": 10},
        {"tool": "cr_review", "param": "code", "score": 8},
    ],
    "code review": [
        {"tool": "review_review", "param": "code", "score": 15},
    ],
    "code_review": [
        {"tool": "review_review", "param": "code", "score": 15},
    ],
    "lint": [
        {"tool": "review_review", "param": "code", "score": 10},
        {"tool": "cr_review", "param": "code", "score": 8},
    ],
    "linter": [{"tool": "review_review", "param": "code", "score": 12}],
    "linting": [{"tool": "review_review", "param": "code", "score": 12}],
    "ruff": [{"tool": "review_review", "param": "code", "score": 15}],
    "mypy": [{"tool": "review_review", "param": "code", "score": 15}],
    "pylint": [{"tool": "review_review", "param": "code", "score": 15}],
    "flake8": [{"tool": "review_review", "param": "code", "score": 15}],
    "bandit": [{"tool": "review_review", "param": "code", "score": 15}],
    "format code": [{"tool": "review_format", "param": "code", "score": 15}],
    "code format": [{"tool": "review_format", "param": "code", "score": 15}],
    "format": [{"tool": "review_format", "param": "code", "score": 8}],
    "formatting": [{"tool": "review_format", "param": "code", "score": 10}],
    "security audit": [{"tool": "review_review", "param": "code", "score": 12}],
    "security": [{"tool": "review_review", "param": "code", "score": 10}],
    "type check": [{"tool": "review_review", "param": "code", "score": 10}],
    "type checking": [{"tool": "review_review", "param": "code", "score": 10}],
    "code quality": [{"tool": "review_review", "param": "code", "score": 10}],
    "code analysis": [{"tool": "review_analyze", "param": "source", "score": 10}],
    "code style": [{"tool": "review_review", "param": "code", "score": 10}],
    "check code": [{"tool": "review_review", "param": "code", "score": 8}],
    "analyse code": [{"tool": "review_analyze", "param": "source", "score": 10}],
    "analyze code": [{"tool": "review_analyze", "param": "source", "score": 10}],
    "code metrics": [{"tool": "review_analyze", "param": "source", "score": 12}],
    "check file": [{"tool": "review_check", "param": "file", "score": 12}],

    # ── Algebra (Scratch Algebra) ──
    "simplify": [
        {"tool": "algebra_simplify", "param": "expression", "score": 15},
        {"tool": "scratch_simplify", "param": "expression", "score": 10},
    ],
    "expand": [
        {"tool": "algebra_expand", "param": "expression", "score": 15},
        {"tool": "scratch_expand", "param": "expression", "score": 10},
    ],
    "derivative": [
        {"tool": "algebra_derivative", "param": "expression", "score": 15},
        {"tool": "scratch_derivative", "param": "expression", "score": 10},
    ],
    "derive": [
        {"tool": "algebra_derivative", "param": "expression", "score": 15},
    ],
    "differentiate": [
        {"tool": "algebra_derivative", "param": "expression", "score": 15},
    ],
    "evaluate": [
        {"tool": "algebra_evaluate", "param": "expression", "score": 15},
        {"tool": "scratch_evaluate", "param": "expression", "score": 10},
    ],
    "solve": [
        {"tool": "algebra_solve", "param": "equation", "score": 15},
        {"tool": "scratch_solve", "param": "equation", "score": 10},
    ],
    "factor": [
        {"tool": "algebra_factor", "param": "expression", "score": 15},
        {"tool": "scratch_factor", "param": "expression", "score": 10},
    ],
    "equation": [
        {"tool": "algebra_solve", "param": "equation", "score": 10},
        {"tool": "scratch_solve", "param": "equation", "score": 8},
    ],
    "expression": [
        {"tool": "algebra_simplify", "param": "expression", "score": 8},
    ],

    # ── Math tools ──
    "gcd": [{"tool": "math_gcd", "param": "a", "score": 15}],
    "lcm": [{"tool": "math_lcm", "param": "a", "score": 15}],
    "prime": [{"tool": "math_is_prime", "param": "n", "score": 12}],
    "fibonacci": [{"tool": "math_fibonacci", "param": "n", "score": 12}],
    "factorial": [{"tool": "math_factorial", "param": "n", "score": 12}],
    "quadratic": [{"tool": "math_quadratic", "param": "a", "score": 12}],
    "mean": [{"tool": "math_mean", "param": "values", "score": 10}],
    "median": [{"tool": "math_median", "param": "values", "score": 10}],
    "variance": [{"tool": "math_variance", "param": "values", "score": 10}],
    "stddev": [{"tool": "math_stddev", "param": "values", "score": 10}],
    "trig": [{"tool": "math_trig", "param": "angle_deg", "score": 10}],
    "sine": [{"tool": "math_trig", "param": "sine", "score": 10}],
    "cosine": [{"tool": "math_trig", "param": "cosine", "score": 10}],
    "tangent": [{"tool": "math_trig", "param": "angle_deg", "score": 10}],
    "sin": [{"tool": "math_trig", "param": "sine", "score": 8}],
    "cos": [{"tool": "math_trig", "param": "cosine", "score": 8}],
    "logarithm": [{"tool": "math_log_", "param": "value", "score": 10}],
    "log": [{"tool": "math_log_", "param": "value", "score": 8}],
    "exponent": [{"tool": "math_exp_", "param": "x", "score": 10}],
    "exp": [{"tool": "math_exp_", "param": "x", "score": 8}],
    "absolute": [{"tool": "math_abs_", "param": "x", "score": 8}],
    "permutation": [{"tool": "math_permutation", "param": "n", "score": 10}],
    "binomial": [{"tool": "math_binomial", "param": "n", "score": 10}],
    "distance": [{"tool": "math_distance", "param": "x1", "score": 10}],
    "midpoint": [{"tool": "math_midpoint", "param": "x1", "score": 10}],
    "bayes": [{"tool": "math_bayes_theorem", "param": "prior", "score": 12}],
    "probability": [{"tool": "math_bayes_theorem", "param": "prior", "score": 8}],

    # ── Physics ──
    "force": [{"tool": "physics_force", "param": "mass", "score": 12}],
    "ohms law": [{"tool": "physics_ohms_law", "param": "voltage", "score": 15}],
    "ohm": [{"tool": "physics_ohms_law", "param": "voltage", "score": 12}],
    "kinematics": [{"tool": "physics_kinematics", "param": "v0", "score": 12}],
    "kinetic energy": [{"tool": "physics_kinetic_energy", "param": "mass", "score": 12}],
    "potential energy": [{"tool": "physics_potential_energy", "param": "mass", "score": 12}],
    "momentum": [{"tool": "physics_momentum", "param": "mass", "score": 12}],
    "wave speed": [{"tool": "physics_wave_speed", "param": "freq", "score": 12}],
    "wavelength": [{"tool": "physics_wave_speed", "param": "speed", "score": 10}],
    "density": [{"tool": "physics_density", "param": "mass", "score": 10}],
    "ideal gas": [{"tool": "physics_idealgas", "param": "P", "score": 12}],
    "gas law": [{"tool": "physics_idealgas", "param": "P", "score": 12}],
    "work": [{"tool": "physics_work", "param": "force", "score": 10}],
    "power": [{"tool": "physics_power", "param": "work", "score": 10}],
    "pressure": [{"tool": "physics_pressure", "param": "force", "score": 10}],
    "coulomb": [{"tool": "physics_coulomb", "param": "q1", "score": 15}],
    "specific heat": [{"tool": "physics_specific_heat", "param": "mass", "score": 10}],
    "heat capacity": [{"tool": "physics_specific_heat", "param": "mass", "score": 10}],
    "kepler": [{"tool": "physics_kepler", "param": "period", "score": 10}],
    "free fall": [{"tool": "kinematics_free_fall", "param": "height", "score": 12}],
    "projectile": [{"tool": "kinematics_projectile", "param": "v0", "score": 10}],

    # ── Chemistry ──
    "molar mass": [{"tool": "chemistry_molar_mass", "param": "formula", "score": 15}],
    "ph": [{"tool": "chemistry_ph", "param": "H_conc", "score": 12}],
    "dilution": [{"tool": "chemistry_dilution", "param": "C1", "score": 10}],
    "mole": [{"tool": "chemistry_mole", "param": "mass", "score": 10}],
    "concentration": [{"tool": "chemistry_concentration", "param": "moles", "score": 10}],
    "yield": [{"tool": "chemistry_yield", "param": "actual", "score": 10}],
    "equilibrium": [{"tool": "chemistry_equilibrium", "param": "K", "score": 10}],
    "enthalpy": [{"tool": "chemistry_enthalpy", "param": "bonds_broken", "score": 10}],
    "entropy": [{"tool": "chemistry_entropy", "param": "Q_rev", "score": 10}],
    "gibbs": [{"tool": "chemistry_gibbs", "param": "H", "score": 10}],
    "balance": [{"tool": "chemsym_balance", "param": "equation", "score": 12}],
    "arrhenius": [{"tool": "chemistry_arrhenius", "param": "A", "score": 15}],
    "nernst": [{"tool": "chemistry_nernst", "param": "E0", "score": 15}],
    "half life": [{"tool": "chemistry_half_life", "param": "decay_const", "score": 10}],
    "half-life": [{"tool": "chemistry_half_life", "param": "decay_const", "score": 10}],
    "reaction rate": [{"tool": "chemistry_reaction_rate", "param": "k", "score": 10}],
    "chemical formula": [{"tool": "chemistry_molar_mass", "param": "formula", "score": 8}],

    # ── Logic ──
    "syllogism": [{"tool": "logic_syllogism", "param": "major", "score": 12}],
    "fallacy": [{"tool": "logic_fallacy", "param": "text", "score": 12}],
    "ambiguity": [{"tool": "logic_ambiguity", "param": "text", "score": 10}],
    "argument": [{"tool": "logic_fallacy", "param": "text", "score": 8}],
    "epistemology": [{"tool": "epistemology_analysis", "param": "claim", "score": 10}],

    # ── Coding tools ──
    "loc": [{"tool": "coding_loc", "param": "source", "score": 10}],
    "cyclomatic": [{"tool": "coding_cyclomatic", "param": "source", "score": 10}],
    "complexity": [{"tool": "coding_cyclomatic", "param": "source", "score": 10}],
    "regex": [{"tool": "coding_validate_regex", "param": "pattern", "score": 10}],
    "identify type": [{"tool": "coding_identify_type", "param": "value", "score": 8}],
    "type of": [{"tool": "coding_identify_type", "param": "value", "score": 8}],

    # ── Software tools ──
    "check idempotency": [{"tool": "software_check_idempotency", "param": "source", "score": 12}],
    "check security": [{"tool": "software_detect_security", "param": "source", "score": 12}],
    "detect type": [{"tool": "software_detect_type_errors", "param": "source", "score": 10}],
    "detect div/0": [{"tool": "software_detect_div_zero", "param": "source", "score": 10}],
    "detect recursion": [{"tool": "software_detect_recursion_bugs", "param": "source", "score": 10}],
    "validate binary": [{"tool": "software_validate_binary_search", "param": "source", "score": 10}],
    "validate kadane": [{"tool": "software_validate_max_subarray", "param": "source", "score": 10}],
    "generate tests": [{"tool": "software_generate_tests", "param": "func_name", "score": 10}],
    "palindrome": [{"tool": "software_generate_tests", "param": "func_name", "score": 8}],
    "analyze nesting": [{"tool": "software_analyze_nesting", "param": "source", "score": 10}],
    "nesting depth": [{"tool": "software_analyze_nesting", "param": "source", "score": 10}],

    # ── Eqsolve (symbolic math, SymPy-powered) ──
    "integrate": [{"tool": "eqsolve_integrate", "param": "expr", "score": 12}],
    "integral": [{"tool": "eqsolve_integrate", "param": "expr", "score": 12}],
    "taylor": [{"tool": "eqsolve_taylor", "param": "expr", "score": 10}],
    "taylor series": [{"tool": "eqsolve_taylor", "param": "expr", "score": 12}],
    "limit": [{"tool": "eqsolve_limit", "param": "expr", "score": 12}],
    "solve system": [{"tool": "eqsolve_solve_system", "param": "equations", "score": 12}],

    # ── Equation tools ──
    "linear system": [{"tool": "equation_solve_linear_system", "param": "a1", "score": 12}],
    "matrix": [{"tool": "equation_matrix_operations", "param": "matrix", "score": 10}],
    "determinant": [{"tool": "equation_matrix_operations", "param": "matrix", "score": 10}],
    "matrix inverse": [{"tool": "equation_matrix_operations", "param": "matrix", "score": 10}],
    "eigenvalue": [{"tool": "equation_matrix_operations", "param": "matrix", "score": 10}],
    "interpolation": [{"tool": "equation_interpolation_linear", "param": "x", "score": 10}],
    "interpolate": [{"tool": "equation_interpolation_linear", "param": "x", "score": 10}],
    "statistics": [{"tool": "equation_statistics_basic", "param": "values", "score": 10}],
    "ohmic": [{"tool": "equation_ohms_law_tool", "param": "voltage", "score": 10}],

    # ── NL2EQ ──
    "natural language equation": [{"tool": "nl2eq_convert", "param": "text", "score": 15}],
    "nl to equation": [{"tool": "nl2eq_convert", "param": "text", "score": 12}],
    "convert to equation": [{"tool": "nl2eq_convert", "param": "text", "score": 12}],

    # ── Project analysis ──
    "dead code": [{"tool": "deadscout_scan", "param": "path", "score": 10}],
    "deadcode": [{"tool": "deadscout_scan", "param": "path", "score": 10}],
    "dependency graph": [{"tool": "depgraph_analyze", "param": "path", "score": 10}],
    "dependencies": [{"tool": "depgraph_analyze", "param": "path", "score": 8}],
    "codebase map": [{"tool": "codebase_map_map_project", "param": "path", "score": 10}],
    "map codebase": [{"tool": "codebase_map_map_project", "param": "path", "score": 10}],
    "project map": [{"tool": "codebase_map_map_project", "param": "path", "score": 10}],
    "health check": [{"tool": "healthcheck_score", "param": "path", "score": 10}],
    "project health": [{"tool": "healthcheck_score", "param": "path", "score": 10}],
    "code health": [{"tool": "healthcheck_score", "param": "path", "score": 10}],

    # ── Finance ──
    "finance": [{"tool": "finance_tools", "param": "value", "score": 5}],

    # ── Data Science ──
    "data science": [{"tool": "datasci_tools", "param": "values", "score": 5}],

    # ── Engineering ──
    "engineering": [{"tool": "engineering_tools", "param": "value", "score": 5}],

    # ── Utility ──
    "utility": [{"tool": "utility_tools", "param": "value", "score": 5}],

    # ── Biology ──
    "biology": [{"tool": "biology_tools", "param": "value", "score": 5}],
}

# Flatten multi-word patterns into a single regex-friendly mapping
_FLAT_TOOL_MAP: List[tuple] = []  # (regex_pattern, tool_info)
for key, tools in TOOL_DETECTION.items():
    for t in tools:
        _FLAT_TOOL_MAP.append((key, t))


class ToolWorker(Plugin):
    """Worker that executes deterministic tools via the message bus."""

    name = "tool_worker"

    def __init__(self, bus=None):
        super().__init__(bus)
        self._tool_cache: Dict[str, Dict[str, str]] = {}
        self._load_tool_cache()

    def _load_tool_cache(self) -> None:
        """Cache all tool names for fast lookup."""
        for t in list_all_tools():
            self._tool_cache[t["name"]] = t
            parts = t["name"].split("_", 1)
            if len(parts) == 2:
                self._tool_cache[parts[1]] = t

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing with {len(self._tool_cache)} cached tools...")
        self.is_active = True
        return True

    def _detect_tool(self, description: str, source_code: str = "", domain_hint: str = "") -> Optional[Dict[str, Any]]:
            """Auto-detect the best tool from a natural language description.

            OPTIMIZATION: Domain-filtered detection. When domain_hint is provided
            (from Intent Parser), only tools in that domain are scored. This
            reduces pattern matching from ~130 to ~20 patterns per query.

            Returns dict with tool_name, param_key, and kwargs, or None.
            """
            low_desc = description.lower() if description else ""
            low_source = source_code.lower() if source_code else ""

            # If domain_hint is provided, pre-filter TOOL_DETECTION keys to
            # only those matching the detected domain
            relevant_keys: List[str] = []
            if domain_hint and domain_hint in DOMAIN_KEYWORDS:
                domain_kws = DOMAIN_KEYWORDS[domain_hint]
                for key in TOOL_DETECTION:
                    if key in domain_kws:
                        relevant_keys.append(key)
                # If no keys in domain, fall back to all
                if not relevant_keys:
                    relevant_keys = list(TOOL_DETECTION.keys())
                logger.debug("Domain filter '%s': %d/%d keys relevant",
                             domain_hint, len(relevant_keys), len(TOOL_DETECTION))
            else:
                relevant_keys = list(TOOL_DETECTION.keys())

            best_score = 0
            best_match = None

            for key in relevant_keys:
                tools = TOOL_DETECTION[key]
                key_low = key.lower()
                # Match multi-word and single-word patterns — use word boundaries for short keys
                if len(key_low) <= 3:
                    # Short keys (ph, log, etc.) must match as whole words to avoid false positives
                    import re
                    if re.search(rf'\\b{re.escape(key_low)}\\b', low_desc) or \
                       re.search(rf'\\b{re.escape(key_low)}\\b', low_source):
                        for t in tools:
                            if t["score"] > best_score:
                                best_score = t["score"]
                                best_match = {
                                    "tool_name": t["tool"],
                                    "param_key": t["param"],
                                    "kwargs": {},
                                    "score": t["score"],
                                    "domain": domain_hint or TOOL_DOMAIN_MAP.get(key, "general"),
                                }
                else:
                    # Longer keys can use substring matching (more flexible)
                    if key_low in low_desc or key_low in low_source:
                        for t in tools:
                            if t["score"] > best_score:
                                best_score = t["score"]
                                best_match = {
                                    "tool_name": t["tool"],
                                    "param_key": t["param"],
                                    "kwargs": {},
                                    "score": t["score"],
                                    "domain": domain_hint or TOOL_DOMAIN_MAP.get(key, "general"),
                                }

            # If we found a match, check if source code was provided
            if best_match and source_code:
                param = best_match["param_key"]
                if param in ("code", "source", "expression", "equation"):
                    best_match["kwargs"][param] = source_code

            return best_match

    def execute(self, message: Message) -> None:
        """Execute a tool call from the bus.

        Expected message payload:
          - tool_name: str (optional — auto-detected if missing)
          - description: str (optional — used for auto-detection)
          - kwargs: dict (optional) — keyword arguments
          - task_id: str (optional)
          - code / source / expression / equation: str (optional — auto-detected)
          - domain: str (optional — domain hint for filtering tools)
        """
        payload = message.payload
        tool_name = payload.get("tool_name", "")
        description = payload.get("description", payload.get("query", ""))
        kwargs = payload.get("kwargs", {})
        task_id = payload.get("task_id", "unknown")
        domain_hint = payload.get("domain", "")

        # Auto-detect domain from description via Intent Parser
        if not domain_hint and description and _intent_parser_available:
            try:
                intent = _intent_parser.parse_intent(description)
                domain_hint = intent.domain if intent.domain != "general" else ""
                logger.debug("Intent domain: %s (confidence=%.2f) → tool filter domain=%s",
                             intent.domain, intent.confidence, domain_hint)
            except Exception:
                pass

        # Auto-detect tool if not specified
        if not tool_name:
            # Check for direct code/expression/equation in payload
            source_code = ""
            for key in ("code", "source", "expression", "equation", "file"):
                if key in payload and isinstance(payload[key], str):
                    source_code = payload[key] if not source_code else source_code
                    if key not in kwargs:
                        kwargs[key] = payload[key]

            # If still no description, use the source code itself
            if not description and source_code:
                description = source_code[:100]

            # If still no description, use the first payload key as hint
            if not description:
                for key in payload:
                    if key not in ("kwargs", "task_id", "tool_name", "description", "query"):
                        description = f"{key} {str(payload[key])[:50]}"
                        break

            detection = self._detect_tool(description, source_code, domain_hint=domain_hint)
            if detection:
                tool_name = detection["tool_name"]
                # Merge detected kwargs with payload kwargs
                for k, v in detection["kwargs"].items():
                    if k not in kwargs:
                        kwargs[k] = v
                logger.info(
                    f"[{self.name}] Auto-detected tool: {tool_name} "
                    f"from description: {description[:60]}"
                )
            else:
                self._respond(message, {
                    "valid": False,
                    "error": "No tool_name specified and could not auto-detect from description",
                    "description": description,
                }, task_id)
                return

        logger.info(f"[{self.name}] Running tool: {tool_name} with kwargs={kwargs}")
        result = run_tool(tool_name, **kwargs)

        self._respond(message, result, task_id)

    def _respond(self, original_msg: Message, result: Dict[str, Any], task_id: str) -> None:
        """Send the tool result back through the bus."""
        response = Message(
            sender=self.name,
            destination=original_msg.sender,
            type="tool_response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "tool_result": result,
                "valid": result.get("valid", False),
            }
        )
        if self.bus:
            self.bus.publish(response)

    def list_tools_by_domain(self) -> Dict[str, List[Dict[str, str]]]:
        return list_tools_by_domain()

    def get_tool_count(self) -> int:
        return len(self._tool_cache)


class ToolOrchestratorMixin:
    """Mixin for the Orchestrator to route tasks to tools."""

    def route_to_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        logger.info(f"[Orchestrator] Routing to tool: {tool_name}")
        return run_tool(tool_name, **kwargs)

    def tool_available(self, tool_name: str) -> bool:
        try:
            result = run_tool(tool_name)
            return "valid" in result
        except Exception:
            return False


# Register this module for auto-discovery
def register_cli(subparsers) -> None:
    p = subparsers.add_parser(
        "toolworker",
        help="Tool Worker status and info",
    )
    sub = p.add_subparsers(dest="toolworker_cmd")
    status_p = sub.add_parser("status", help="Show tool worker status")
    status_p.set_defaults(func=_cmd_status)
    count_p = sub.add_parser("count", help="Count available tools")
    count_p.set_defaults(func=_cmd_count)


def _cmd_status(args) -> None:
    by_domain = list_tools_by_domain()
    total = sum(len(tools) for tools in by_domain.values())
    print(f"ToolWorker: {total} tools across {len(by_domain)} domains")
    for domain, tools in sorted(by_domain.items()):
        print(f"  {domain:15s}: {len(tools):>3d} tools")


def _cmd_count(args) -> None:
    by_domain = list_tools_by_domain()
    total = sum(len(tools) for tools in by_domain.values())
    print(f"Total tools: {total}")
    for domain, tools in sorted(by_domain.items()):
        print(f"  {domain:15s}: {len(tools):>3d}")


if __name__ == "__main__":
    _cmd_status(None)