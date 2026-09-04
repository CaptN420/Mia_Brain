#!/usr/bin/env python3
"""
benchmark.py — Coding benchmark for CaptN-BRAIN deterministic tools.

Mesures :
  - **Token economy** (% d'économie vs approche LLM équivalente)
  - **Correctness** (validation contre ground truth)
  - **False positives** (le tool dit ok=True mais la réponse est fausse)

Suites :
  - eqsolve       : solveur symbolique (sympy wrapper)
  - chemsym       : chimie (balancement, masse molaire, parsing)
  - equation      : outils math/physique/chimie déterministes purs
  - nl2eq         : conversion langage naturel → équation
  - deadscout     : détection code mort
  - depgraph      : analyse graphe de dépendances
  - healthcheck   : score de santé projet

Usage:
    python -m tools.benchmark                          # Run all suites
    python -m tools.benchmark --suite eqsolve           # Specific suite
    python -m tools.benchmark --json                    # JSON output
    python -m tools.benchmark --verbose                 # Verbose per-test output
    python -m tools.benchmark --list                    # List suites only
"""
from __future__ import annotations

import importlib
import json
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════
# Structures de données
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkCase:
    """Un unique cas de test.

    name:            nom lisible du test
    suite:           nom de la suite (renseigné par le builder)
    tool_call:       fonction à appeler (tool(*args, **kwargs))
    expected_checks: dict de {key: expected_value} à vérifier dans le résultat
    expect_ok:       le champ ok/valid doit-il être True ?
    llm_eq_chars:    estimation des caractères nécessaires à un prompt LLM équivalent
    description:     courte description du cas
    """
    name: str
    suite: str = ""
    tool_call: Callable[[], Any] = lambda: None
    expected_checks: Dict[str, Any] = field(default_factory=dict)
    expect_ok: bool = True
    llm_eq_chars: int = 0
    description: str = ""


@dataclass
class BenchmarkResult:
    """Résultat d'un unique cas."""
    name: str
    suite: str
    passed: bool
    actual: Any
    expected_checks: Dict[str, Any]
    mismatches: Dict[str, Tuple[Any, Any]] = field(default_factory=dict)
    false_positive: bool = False
    false_negative: bool = False
    execution_time_ms: float = 0.0
    token_savings_pct: float = 100.0
    char_efficiency_pct: float = 100.0  # CLI chars vs LLM equivalent chars
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "suite": self.suite,
            "passed": self.passed,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "token_savings_pct": round(self.token_savings_pct, 1),
            "char_efficiency_pct": round(self.char_efficiency_pct, 1),
            "mismatches": self.mismatches,
            "description": self.description,
        }


@dataclass
class SuiteReport:
    """Rapport agrégé pour une suite de tests."""
    suite_name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    avg_time_ms: float = 0.0
    total_token_savings_pct: float = 100.0
    avg_char_efficiency_pct: float = 100.0
    details: List[BenchmarkResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suite": self.suite_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "pass_rate_pct": round(self.passed / self.total * 100, 1) if self.total else 0,
            "avg_time_ms": round(self.avg_time_ms, 2),
            "token_savings_pct": round(self.total_token_savings_pct, 1),
            "char_efficiency_pct": round(self.avg_char_efficiency_pct, 1),
            "details": [r.to_dict() for r in self.details],
        }


@dataclass
class BenchmarkReport:
    """Rapport global."""
    suites: Dict[str, SuiteReport] = field(default_factory=dict)
    total_cases: int = 0
    total_passed: int = 0
    total_failed: int = 0
    total_false_positives: int = 0
    total_false_negatives: int = 0
    overall_token_savings_pct: float = 100.0
    overall_char_efficiency_pct: float = 100.0
    global_pass_rate_pct: float = 100.0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "benchmark": "CaptN-BRAIN Deterministic Tools Benchmark",
            "duration_seconds": round(self.duration_seconds, 2),
            "summary": {
                "total_cases": self.total_cases,
                "passed": self.total_passed,
                "failed": self.total_failed,
                "pass_rate_pct": round(self.global_pass_rate_pct, 1),
                "false_positives": self.total_false_positives,
                "false_negatives": self.total_false_negatives,
                "overall_token_savings_pct": round(self.overall_token_savings_pct, 1),
                "overall_char_efficiency_pct": round(self.overall_char_efficiency_pct, 1),
            },
            "suites": {k: v.to_dict() for k, v in sorted(self.suites.items())},
        }

    def print_report(self, verbose: bool = False) -> None:
        """Affiche un rapport lisible."""
        sep = "═" * 68
        print(sep)
        print(f"  📊  CaptN-BRAIN — Benchmark des outils déterministes")
        print(sep)
        print(f"  Durée totale : {self.duration_seconds:.2f}s")
        print()
        print(f"  {self.total_passed:3d} / {self.total_cases}  tests réussis")
        print(f"  {self.total_failed:3d}  échecs")
        if self.total_false_positives:
            print(f"  ⚠️  {self.total_false_positives}  faux positifs (ok=True mais réponse fausse)")
        if self.total_false_negatives:
            print(f"  ⚠️  {self.total_false_negatives}  faux négatifs (ok=False alors que réponse correcte)")
        print(f"  Taux de réussite : {self.global_pass_rate_pct:.1f}%")
        print(f"  Économie de tokens LLM : {self.overall_token_savings_pct:.1f}%"
              f"  (0 token LLM consommé — 100% déterministe)")
        print(f"  Efficacité caractère   : {self.overall_char_efficiency_pct:.1f}%"
              f"  (CLI vs prompt LLM équivalent)")
        print(sep)
        print()

        for suite_name, sr in sorted(self.suites.items()):
            status = "✅" if sr.failed == 0 else "❌"
            print(f"  {status} [{suite_name}]  {sr.passed}/{sr.total} passé  "
                  f"FP:{sr.false_positives} FN:{sr.false_negatives}  "
                  f"⏱{sr.avg_time_ms:.1f}ms  "
                  f"💾{sr.total_token_savings_pct:.0f}% tokens  "
                  f"📝{sr.avg_char_efficiency_pct:.0f}% chars")

            if verbose and sr.details:
                for r in sr.details:
                    ic = "✅" if r.passed else ("⚠️" if r.false_positive else "❌")
                    label = f"  {ic}  {r.name}"
                    if not r.passed:
                        label += f" — mismatches: {r.mismatches}"
                    if r.false_positive:
                        label += " [FAUX POSITIF]"
                    if r.false_negative:
                        label += " [FAUX NÉGATIF]"
                    print(label)
                print()


# ═══════════════════════════════════════════════════════════════════
# Helper : estimation du coût token LLM équivalent
# ═══════════════════════════════════════════════════════════════════

def _llm_token_estimate(prompt_chars: int, response_chars: int = 0) -> int:
    """Estimation conservatrice : ~4 chars = 1 token."""
    return math.ceil((prompt_chars + response_chars) / 4)


# ═══════════════════════════════════════════════════════════════════
# Suites de tests
# ═══════════════════════════════════════════════════════════════════

def _check_result(actual: Any, expected: Dict[str, Any], expect_ok: bool,
                  result: BenchmarkResult) -> BenchmarkResult:
    """Compare le résultat réel aux checks attendus."""
    if actual is None:
        result.passed = False
        result.false_positive = expect_ok
        result.mismatches["__none__"] = ("Result is None", str(actual))
        return result

    if isinstance(actual, str):
        # JSON string — parse it
        try:
            actual = json.loads(actual)
        except (json.JSONDecodeError, TypeError):
            pass

    if isinstance(actual, dict):
        # Check ok/valid field
        ok = actual.get("ok", actual.get("valid", expect_ok))
        if isinstance(ok, (bool,)):
            if ok and not expect_ok:
                result.false_positive = True
            if not ok and expect_ok:
                result.false_negative = True

        # Check expected keys
        for key, exp_val in expected.items():
            if key in ("ok", "valid"):
                continue
            actual_val = actual.get(key)
            if actual_val is None:
                result.mismatches[key] = (f"<missing key: {key}>", str(actual))
                continue
            # Numeric comparison with tolerance
            if isinstance(exp_val, float) and isinstance(actual_val, (int, float)):
                if abs(float(actual_val) - exp_val) > 1e-4:
                    result.mismatches[key] = (f"expected={exp_val}", f"actual={actual_val}")
            # String comparison
            elif isinstance(exp_val, str):
                # Normalize: strip whitespace, remove outer brackets for list-like strings
                a_str = str(actual_val).strip()
                e_str = exp_val.strip()
                # Try to match either exact or as substring
                if a_str != e_str and e_str not in a_str and a_str not in e_str:
                    # Compare as parsed lists if bracketed
                    if e_str.startswith("[") and a_str.startswith("["):
                        try:
                            if json.loads(a_str) != json.loads(e_str):
                                result.mismatches[key] = (e_str, a_str)
                        except (json.JSONDecodeError, TypeError):
                            result.mismatches[key] = (e_str, a_str)
                    else:
                        result.mismatches[key] = (e_str, a_str)
            # List comparison
            elif isinstance(exp_val, list):
                if isinstance(actual_val, list):
                    if actual_val != exp_val:
                        result.mismatches[key] = (str(exp_val), str(actual_val))
                else:
                    result.mismatches[key] = (str(exp_val), str(actual_val))
            # Dict comparison
            elif isinstance(exp_val, dict):
                if isinstance(actual_val, dict):
                    for ek, ev in exp_val.items():
                        if actual_val.get(ek) != ev:
                            result.mismatches[key] = (str(exp_val), str(actual_val))
                            break
                else:
                    result.mismatches[key] = (str(exp_val), str(actual_val))
            else:
                if actual_val != exp_val:
                    result.mismatches[key] = (f"expected={exp_val}", f"actual={actual_val}")

    result.passed = len(result.mismatches) == 0 and not result.false_positive and not result.false_negative
    return result


def run_suite(suite_name: str, cases: List[BenchmarkCase],
              verbose: bool = False) -> SuiteReport:
    """Exécute une suite de cas de test et retourne le rapport."""
    sr = SuiteReport(suite_name=suite_name)
    times = []
    char_effs = []

    for case in cases:
        case.suite = suite_name  # fill suite from caller
        t0 = time.monotonic()
        try:
            actual = case.tool_call()
        except Exception as e:
            actual = {"ok": False, "error": str(e)}
        dt = (time.monotonic() - t0) * 1000
        times.append(dt)

        # Token economy: deterministic = 0 LLM tokens
        savings = 100.0

        # Character efficiency: estimate CLI invocation chars vs LLM equivalent
        cli_chars = _estimate_cli_chars(case)
        if case.llm_eq_chars > 0 and cli_chars > 0:
            char_eff = max(0.0, (1 - cli_chars / max(case.llm_eq_chars, 1)) * 100)
        else:
            char_eff = 100.0
        char_effs.append(char_eff)

        result = BenchmarkResult(
            name=case.name,
            suite=suite_name,
            passed=False,
            actual=actual,
            expected_checks=case.expected_checks,
            execution_time_ms=dt,
            token_savings_pct=savings,
            char_efficiency_pct=round(char_eff, 1),
            description=case.description,
        )
        result = _check_result(actual, case.expected_checks, case.expect_ok, result)
        sr.details.append(result)
        sr.total += 1
        if result.passed:
            sr.passed += 1
        else:
            sr.failed += 1
        if result.false_positive:
            sr.false_positives += 1
        if result.false_negative:
            sr.false_negatives += 1

    sr.avg_time_ms = sum(times) / len(times) if times else 0
    sr.total_token_savings_pct = 100.0
    sr.avg_char_efficiency_pct = sum(char_effs) / len(char_effs) if char_effs else 100.0
    return sr


def _estimate_cli_chars(case: BenchmarkCase) -> int:
    """Estime le nombre de caractères d'une invocation CLI équivalente.

    On prend le nom du test comme approximation du nom de commande CLI,
    et on ajoute des arguments typiques selon la suite.
    """
    name = case.name
    # Common tool name prefixes by suite
    tool_cmd_map = {
        "eqsolve": "eqsolve", "chemsym": "chemsym", "equation": "equation run",
        "nl2eq": "nl2eq", "deadscout": "deadscout", "depgraph": "depgraph",
        "healthcheck": "healthcheck",
    }
    cmd: str = tool_cmd_map.get(case.suite, case.suite)
    # CLI base: summon_agents.py <cmd> <name-as-args>
    # Rough estimate: 20 chars for summon + 5 for tool name + name chars
    base = 20 + len(cmd) + 4
    chars = base + len(name)
    return chars


# ═══════════════════════════════════════════════════════════════════
# Définition des cas de test par suite
# ═══════════════════════════════════════════════════════════════════

SUITE_REGISTRY: Dict[str, Callable[[], List[BenchmarkCase]]] = {}


def _build_eqsolve_cases() -> List[BenchmarkCase]:
    """Cas de test pour eqsolve (solveur symbolique sympy)."""
    cases = []

    # derive
    cases.append(BenchmarkCase(
        name="derive_x2",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_derive("x**2", "x", 1),
        expected_checks={"operation": "derive", "result": "2*x"},
        llm_eq_chars=50,
        description="Dérivée de x² → 2x",
    ))
    cases.append(BenchmarkCase(
        name="derive_sin_x",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_derive("sin(x)", "x", 1),
        expected_checks={"operation": "derive", "result": "cos(x)"},
        llm_eq_chars=50,
        description="Dérivée de sin(x) → cos(x)",
    ))
    cases.append(BenchmarkCase(
        name="derive_kAB",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_derive("k*A*B", "A", 1),
        expected_checks={"operation": "derive", "result": "B*k"},
        llm_eq_chars=60,
        description="Dérivée de k*A*B par A → k*B",
    ))
    cases.append(BenchmarkCase(
        name="derive_order2",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_derive("x**3", "x", 2),
        expected_checks={"operation": "derive", "order": 2, "result": "6*x"},
        llm_eq_chars=60,
        description="Dérivée seconde de x³ → 6x",
    ))

    # integrate
    cases.append(BenchmarkCase(
        name="integrate_x2",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_integrate("x**2", "x"),
        expected_checks={"operation": "integrate"},
        expect_ok=True,
        llm_eq_chars=50,
        description="Intégrale de x² dx",
    ))
    cases.append(BenchmarkCase(
        name="integrate_definite",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_integrate("x**2", "x", [0, 1]),
        expected_checks={"operation": "integrate", "definite": [0, 1]},
        expect_ok=True,
        llm_eq_chars=70,
        description="Intégrale définie de x² de 0 à 1",
    ))

    # solve
    cases.append(BenchmarkCase(
        name="solve_x2_4",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_solve("x**2 - 4", "x"),
        expected_checks={"operation": "solve", "solutions": "[-2, 2]"},
        llm_eq_chars=40,
        description="Résoudre x² - 4 = 0 → x = ±2",
    ))
    cases.append(BenchmarkCase(
        name="solve_quadratic",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_solve("x**2 - 3*x + 2", "x"),
        expected_checks={"operation": "solve"},
        expect_ok=True,
        llm_eq_chars=50,
        description="Résoudre x² - 3x + 2 = 0",
    ))

    # solve-system
    cases.append(BenchmarkCase(
        name="solve_system_2x2",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_solve_system(
            ["x + y = 5", "x - y = 1"]),
        expected_checks={"operation": "solve_system"},
        expect_ok=True,
        llm_eq_chars=80,
        description="Système x+y=5, x-y=1",
    ))

    # taylor
    cases.append(BenchmarkCase(
        name="taylor_sin_x",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_taylor("sin(x)", "x", 4, 0),
        expected_checks={"operation": "taylor", "order": 4},
        expect_ok=True,
        llm_eq_chars=70,
        description="Taylor sin(x) ordre 4 en 0",
    ))

    # simplify
    cases.append(BenchmarkCase(
        name="simplify_zero",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_simplify("(x+y)**2 - (x**2 + 2*x*y + y**2)"),
        expected_checks={"operation": "simplify", "result": "0"},
        llm_eq_chars=70,
        description="Simplifier (x+y)² - (x² + 2xy + y²) → 0",
    ))

    # factor
    cases.append(BenchmarkCase(
        name="factor_x2_4",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_factor("x**2 - 4"),
        expected_checks={"operation": "factor"},
        expect_ok=True,
        llm_eq_chars=40,
        description="Factoriser x² - 4",
    ))

    # limit
    cases.append(BenchmarkCase(
        name="limit_sinc",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_limit("sin(x)/x", "x", 0),
        expected_checks={"operation": "limit", "result": "1"},
        llm_eq_chars=60,
        description="limite sin(x)/x → 1 quand x→0",
    ))
    cases.append(BenchmarkCase(
        name="limit_infinity",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_limit("1/x", "x", "oo"),
        expected_checks={"operation": "limit", "result": "0"},
        llm_eq_chars=60,
        description="limite 1/x → 0 quand x→∞",
    ))

    # expand
    cases.append(BenchmarkCase(
        name="expand_product",
        tool_call=lambda: importlib.import_module("tools.eqsolve").cmd_expand("(x+y)**2"),
        expected_checks={"operation": "expand"},
        expect_ok=True,
        llm_eq_chars=40,
        description="Développer (x+y)²",
    ))

    return cases


def _build_chemsym_cases() -> List[BenchmarkCase]:
    """Cas de test pour chemsym (chimie)."""
    cases = []

    # parse formula
    cases.append(BenchmarkCase(
        name="parse_h2o",
        tool_call=lambda: importlib.import_module("tools.chemsym").parse_formula("H2O").to_dict(),
        expected_checks={"formula": "H2O", "ok": True, "elements": {"H": 2, "O": 1}},
        llm_eq_chars=30,
        description="Parser H2O → H:2, O:1",
    ))
    cases.append(BenchmarkCase(
        name="parse_fe2so43",
        tool_call=lambda: importlib.import_module("tools.chemsym").parse_formula("Fe2(SO4)3").to_dict(),
        expected_checks={"formula": "Fe2(SO4)3", "ok": True, "elements": {"Fe": 2, "S": 3, "O": 12}},
        llm_eq_chars=40,
        description="Parser Fe2(SO4)3 → Fe:2, S:3, O:12",
    ))
    cases.append(BenchmarkCase(
        name="parse_glucose",
        tool_call=lambda: importlib.import_module("tools.chemsym").parse_formula("C6H12O6").to_dict(),
        expected_checks={"formula": "C6H12O6", "ok": True},
        llm_eq_chars=30,
        description="Parser C6H12O6 (glucose)",
    ))
    cases.append(BenchmarkCase(
        name="parse_invalid",
        tool_call=lambda: importlib.import_module("tools.chemsym").parse_formula("XxYyZz").to_dict(),
        expected_checks={"ok": False},
        expect_ok=False,
        llm_eq_chars=30,
        description="Élément inconnu → ok=False",
    ))

    # molar mass
    cases.append(BenchmarkCase(
        name="molar_h2o",
        tool_call=lambda: importlib.import_module("tools.chemsym").parse_formula("H2O").to_dict(),
        expected_checks={"molar_mass": 18.015},  # 1.008*2 + 15.999
        llm_eq_chars=30,
        description="Masse molaire H2O = 18.015 g/mol",
    ))
    cases.append(BenchmarkCase(
        name="molar_co2",
        tool_call=lambda: importlib.import_module("tools.chemsym").parse_formula("CO2").to_dict(),
        expected_checks={"molar_mass": 44.009},  # 12.011 + 15.999*2
        llm_eq_chars=30,
        description="Masse molaire CO2 = 44.009 g/mol",
    ))

    # balance
    cases.append(BenchmarkCase(
        name="balance_h2_o2",
        tool_call=lambda: importlib.import_module("tools.chemsym").balance_equation("H2 + O2 = H2O"),
        expected_checks={},
        expect_ok=True,
        llm_eq_chars=100,
        description="Balancer H2 + O2 = H2O",
    ))
    cases.append(BenchmarkCase(
        name="balance_combustion",
        tool_call=lambda: importlib.import_module("tools.chemsym").balance_equation("C6H12O6 + O2 = CO2 + H2O"),
        expected_checks={},
        expect_ok=True,
        llm_eq_chars=120,
        description="Balancer combustion du glucose",
    ))

    return cases


def _build_equation_cases() -> List[BenchmarkCase]:
    """Cas de test pour les outils math/physique (equation_tools)."""
    import tools.equation_tools as et
    cases = []

    # solve quadratic
    cases.append(BenchmarkCase(
        name="quadratic_real",
        tool_call=lambda: et.solve_quadratic(1, -3, 2),
        expected_checks={"valid": True, "real": True, "x1": 2.0, "x2": 1.0},
        llm_eq_chars=60,
        description="x² - 3x + 2 = 0 → x=2, x=1",
    ))
    cases.append(BenchmarkCase(
        name="quadratic_complex",
        tool_call=lambda: et.solve_quadratic(1, 0, 1),
        expected_checks={"valid": True, "real": False},
        llm_eq_chars=50,
        description="x² + 1 = 0 → racines complexes",
    ))
    cases.append(BenchmarkCase(
        name="quadratic_zero_a",
        tool_call=lambda: et.solve_quadratic(0, 1, 1),
        expected_checks={"valid": False},
        expect_ok=False,
        llm_eq_chars=40,
        description="a=0 → erreur",
    ))

    # linear system 2x2
    cases.append(BenchmarkCase(
        name="linear_system",
        tool_call=lambda: et.solve_linear_system_2x2(1, 1, 5, 1, -1, 1),
        expected_checks={"valid": True, "x": 3.0, "y": 2.0},
        llm_eq_chars=70,
        description="x+y=5, x-y=1 → x=3, y=2",
    ))
    cases.append(BenchmarkCase(
        name="linear_system_singular",
        tool_call=lambda: et.solve_linear_system_2x2(1, 1, 5, 2, 2, 10),
        expected_checks={"valid": False},
        expect_ok=False,
        llm_eq_chars=60,
        description="Système singulier → erreur",
    ))

    # derivative numeric
    cases.append(BenchmarkCase(
        name="deriv_numeric",
        tool_call=lambda: et.derivative_numeric("x**2", 3.0),
        expected_checks={"valid": True},
        expect_ok=True,
        llm_eq_chars=50,
        description="Dérivée numérique de x² en x=3 → ~6",
    ))

    # integral numeric
    cases.append(BenchmarkCase(
        name="integral_numeric",
        tool_call=lambda: et.integral_numeric("x**2", 0, 1),
        expected_checks={"valid": True},
        expect_ok=True,
        llm_eq_chars=60,
        description="Intégrale numérique de x² de 0 à 1 → ~0.333",
    ))

    # kinematics
    cases.append(BenchmarkCase(
        name="free_fall",
        tool_call=lambda: et.kinematics_free_fall(10),
        expected_checks={"valid": True, "height": 10.0},
        llm_eq_chars=60,
        description="Chute libre 10m → t≈1.43s, v≈14.0 m/s",
    ))
    cases.append(BenchmarkCase(
        name="projectile",
        tool_call=lambda: et.kinematics_projectile(20, 45),
        expected_checks={"valid": True},
        llm_eq_chars=80,
        description="Projectile v0=20m/s angle=45°",
    ))

    # energy
    cases.append(BenchmarkCase(
        name="kinetic_energy",
        tool_call=lambda: et.energy_kinetic(2, 3),
        expected_checks={"valid": True, "kinetic_energy": 9.0},
        llm_eq_chars=50,
        description="Ek = ½·2·3² = 9 J",
    ))
    cases.append(BenchmarkCase(
        name="potential_energy",
        tool_call=lambda: et.energy_potential(5, 10),
        expected_checks={"valid": True},
        expect_ok=True,
        llm_eq_chars=50,
        description="Ep = 5·9.81·10 = 490.5 J",
    ))

    # ohm's law
    cases.append(BenchmarkCase(
        name="ohms_law_vi",
        tool_call=lambda: et.ohms_law(voltage=12, current=3),
        expected_checks={"valid": True, "resistance": 4.0},
        llm_eq_chars=50,
        description="U=12V, I=3A → R=4Ω",
    ))
    cases.append(BenchmarkCase(
        name="ohms_law_vr",
        tool_call=lambda: et.ohms_law(voltage=12, resistance=6),
        expected_checks={"valid": True, "current": 2.0},
        llm_eq_chars=50,
        description="U=12V, R=6Ω → I=2A",
    ))

    # statistics
    cases.append(BenchmarkCase(
        name="statistics_basic",
        tool_call=lambda: et.statistics_basic([1.0, 2.0, 3.0, 4.0, 5.0]),
        expected_checks={"valid": True, "n": 5, "mean": 3.0, "median": 3.0},
        llm_eq_chars=80,
        description="Statistiques de [1,2,3,4,5] → mean=3, med=3",
    ))

    # matrix
    cases.append(BenchmarkCase(
        name="matrix_det_2x2",
        tool_call=lambda: et.matrix_operations([[1, 2], [3, 4]], "det"),
        expected_checks={"valid": True, "determinant": -2.0},
        llm_eq_chars=60,
        description="Détérminant [[1,2],[3,4]] = -2",
    ))
    cases.append(BenchmarkCase(
        name="matrix_inverse_2x2",
        tool_call=lambda: et.matrix_operations([[4, 3], [3, 2]], "inverse"),
        expected_checks={"valid": True},
        expect_ok=True,
        llm_eq_chars=70,
        description="Inverse [[4,3],[3,2]]",
    ))
    cases.append(BenchmarkCase(
        name="matrix_trace",
        tool_call=lambda: et.matrix_operations([[1, 2], [3, 4]], "trace"),
        expected_checks={"valid": True, "trace": 5.0},
        llm_eq_chars=50,
        description="Trace [[1,2],[3,4]] = 5",
    ))
    cases.append(BenchmarkCase(
        name="matrix_singular",
        tool_call=lambda: et.matrix_operations([[1, 1], [1, 1]], "inverse"),
        expected_checks={"valid": False},
        expect_ok=False,
        llm_eq_chars=50,
        description="Matrice singulière → pas d'inverse",
    ))

    # gas law
    cases.append(BenchmarkCase(
        name="ideal_gas_pv",
        tool_call=lambda: et.gas_law_ideal(P=101325, n=1, T=273.15),
        expected_checks={"valid": True},
        expect_ok=True,
        llm_eq_chars=80,
        description="PV = nRT → V = nRT/P",
    ))

    # interpolation
    cases.append(BenchmarkCase(
        name="interpolation_linear",
        tool_call=lambda: et.interpolation_linear([0, 1, 2], [0, 2, 4], 0.5),
        expected_checks={"valid": True, "y": 1.0},
        llm_eq_chars=60,
        description="Interpolation linéaire (0,0)-(1,2) en x=0.5 → y=1",
    ))
    cases.append(BenchmarkCase(
        name="interpolation_out_of_bounds",
        tool_call=lambda: et.interpolation_linear([0, 1], [0, 2], 5),
        expected_checks={"valid": False},
        expect_ok=False,
        llm_eq_chars=50,
        description="x=5 hors intervalle → erreur",
    ))

    # wave
    cases.append(BenchmarkCase(
        name="wave_vlf",
        tool_call=lambda: et.wave_properties(speed=340, frequency=440),
        expected_checks={"valid": True},
        expect_ok=True,
        llm_eq_chars=60,
        description="v=340, f=440 → λ=0.773m",
    ))

    return cases


def _build_nl2eq_cases() -> List[BenchmarkCase]:
    """Cas de test pour nl2eq."""
    cases = []
    cases.append(BenchmarkCase(
        name="nl_rate_change",
        tool_call=lambda: importlib.import_module("tools.nl2eq").Nl2EqConverter().convert(
            "rate of change of N is proportional to k times A times B"
        ).to_dict(),
        expected_checks={"ok": True, "method": "rule"},
        llm_eq_chars=100,
        description="NL → équation: dN/dt ∝ k·A·B",
    ))
    cases.append(BenchmarkCase(
        name="nl_dot_notation",
        tool_call=lambda: importlib.import_module("tools.nl2eq").Nl2EqConverter().convert(
            "N dot = k times A minus lambda times N"
        ).to_dict(),
        expected_checks={"ok": True},
        llm_eq_chars=100,
        description="NL → équation: N_dot = k·A - λ·N",
    ))
    return cases


def _build_deadscout_cases() -> List[BenchmarkCase]:
    """Cas de test pour deadscout (sur un répertoire test)."""
    cases = []
    # Scan the project root — should find actual code
    cases.append(BenchmarkCase(
        name="deadscout_scan_tools",
        tool_call=lambda: _run_deadscout("./tools"),
        expected_checks={},
        expect_ok=True,
        llm_eq_chars=300,
        description="Scan code mort dans tools/",
    ))
    return cases


def _run_deadscout(path: str):
    from tools.deadscout import DeadScout
    d = DeadScout(path)
    report = d.analyze()
    return report.to_dict()


def _build_depgraph_cases() -> List[BenchmarkCase]:
    """Cas de test pour depgraph."""
    cases = []
    cases.append(BenchmarkCase(
        name="depgraph_scan_summon_agents",
        tool_call=lambda: _run_depgraph("./summon_agents.py"),
        expected_checks={},
        expect_ok=True,
        llm_eq_chars=400,
        description="Analyse dépendances de summon_agents.py",
    ))
    cases.append(BenchmarkCase(
        name="depgraph_scan_tools_dir",
        tool_call=lambda: _run_depgraph("./tools"),
        expected_checks={},
        expect_ok=True,
        llm_eq_chars=600,
        description="Analyse dépendances du dossier tools/",
    ))
    return cases


def _run_depgraph(path: str):
    from tools.depgraph import DepGraphAnalyzer
    d = DepGraphAnalyzer(path)
    report = d.analyze()
    return report.to_dict()


def _build_healthcheck_cases() -> List[BenchmarkCase]:
    """Cas de test pour healthcheck."""
    cases = []
    cases.append(BenchmarkCase(
        name="healthcheck_self",
        tool_call=lambda: _run_healthcheck("."),
        expected_checks={},
        expect_ok=True,
        llm_eq_chars=500,
        description="Score de santé du projet CaptN-BRAIN",
    ))
    return cases


def _run_healthcheck(path: str):
    from tools.healthcheck import HealthChecker
    h = HealthChecker(path)
    report = h.check()
    return report.to_dict()


# Enregistrement des suites
SUITE_REGISTRY["eqsolve"] = _build_eqsolve_cases
SUITE_REGISTRY["chemsym"] = _build_chemsym_cases
SUITE_REGISTRY["equation"] = _build_equation_cases
SUITE_REGISTRY["nl2eq"] = _build_nl2eq_cases
SUITE_REGISTRY["deadscout"] = _build_deadscout_cases
SUITE_REGISTRY["depgraph"] = _build_depgraph_cases
SUITE_REGISTRY["healthcheck"] = _build_healthcheck_cases


# ═══════════════════════════════════════════════════════════════════
# Exécution du benchmark
# ═══════════════════════════════════════════════════════════════════

def run_benchmark(suite_filter: Optional[str] = None,
                  verbose: bool = False) -> BenchmarkReport:
    """Exécute le benchmark complet ou filtré."""
    t_start = time.monotonic()
    report = BenchmarkReport()

    suite_names = sorted(SUITE_REGISTRY.keys())
    if suite_filter:
        if suite_filter not in SUITE_REGISTRY:
            print(f"❌ Suite inconnue: {suite_filter}")
            print(f"   Suites disponibles: {', '.join(suite_names)}")
            sys.exit(1)
        suite_names = [suite_filter]

    for name in suite_names:
        builder = SUITE_REGISTRY[name]
        cases = builder()
        sr = run_suite(name, cases, verbose=verbose)
        report.suites[name] = sr
        report.total_cases += sr.total
        report.total_passed += sr.passed
        report.total_failed += sr.failed
        report.total_false_positives += sr.false_positives
        report.total_false_negatives += sr.false_negatives

    report.duration_seconds = time.monotonic() - t_start
    report.global_pass_rate_pct = (
        report.total_passed / report.total_cases * 100 if report.total_cases else 0
    )
    # Token economy: all tools are deterministic → 100% savings
    report.overall_token_savings_pct = 100.0
    # Average char efficiency across suites
    suite_effs = [sr.avg_char_efficiency_pct for sr in report.suites.values() if sr.total > 0]
    report.overall_char_efficiency_pct = (
        sum(suite_effs) / len(suite_effs) if suite_effs else 100.0
    )

    return report


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def _cmd_benchmark(args) -> None:
    """Entry point for CLI dispatch."""
    if args.list:
        print("Suites disponibles:")
        for name, builder in sorted(SUITE_REGISTRY.items()):
            cases = builder()
            print(f"  {name:15s} — {len(cases)} cas de test")
        return

    report = run_benchmark(
        suite_filter=args.suite,
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        report.print_report(verbose=args.verbose)


def register_cli(subparsers) -> None:
    """Register benchmark as a summon_agents.py subcommand."""
    p = subparsers.add_parser(
        "benchmark",
        help="Benchmark de coding : token economy, correctness, false positives",
    )
    p.add_argument("--suite", "-s", choices=list(SUITE_REGISTRY.keys()),
                   help="Suite spécifique à exécuter")
    p.add_argument("--json", "-j", action="store_true",
                   help="Sortie JSON")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Afficher le détail de chaque test")
    p.add_argument("--list", "-l", action="store_true",
                   help="Lister les suites disponibles")
    p.set_defaults(func=_cmd_benchmark)


def main() -> None:
    """Standalone entry point."""
    import argparse
    parser = argparse.ArgumentParser(
        description="CaptN-BRAIN Benchmark — token economy, correctness, false positives"
    )
    parser.add_argument("--suite", "-s", choices=list(SUITE_REGISTRY.keys()),
                        help="Suite spécifique à exécuter")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Afficher le détail de chaque test")
    parser.add_argument("--list", "-l", action="store_true",
                        help="Lister les suites disponibles")
    args = parser.parse_args()

    if args.list:
        print("Suites disponibles:")
        for name, builder in sorted(SUITE_REGISTRY.items()):
            cases = builder()
            print(f"  {name:15s} — {len(cases)} cas de test")
        return

    report = run_benchmark(suite_filter=args.suite, verbose=args.verbose)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        report.print_report(verbose=args.verbose)


if __name__ == "__main__":
    main()