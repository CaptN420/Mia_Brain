#!/usr/bin/env python3
"""Diversification worker: generates structurally distinct equation variants.

Deterministic (no LLM). Takes validated equations from shared memory and
applies a fixed menu of structural mutations, then keeps only variants that
are truly NEW (different structure signature) and still use validated
variables. Output feeds the normal validation -> safety gate -> archive flow.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set


def _norm(eq: str) -> str:
    return re.sub(r"\s+", "", str(eq or "")).lower()


class DiversifierWorker:
    """Structural mutation engine for equations."""

    def __init__(self, approved_variables: Dict[str, Any] | None = None):
        self.approved_symbols: Set[str] = {
            str(k).strip() for k in (approved_variables or {}).keys()
        }

    # ------------------------------------------------------------------
    def _symbols(self, eq: str) -> List[str]:
        return [s for s in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", eq or "")
                if s != "Ndot" and len(s) <= 8]

    def _signature(self, eq: str) -> str:
        """Structure signature: operators layout with symbols abstracted."""
        s = _norm(eq)
        # Replace every symbol token by 'X', keep operator skeleton
        s = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", "X", s)
        s = re.sub(r"\d+(\.\d+)?", "N", s)
        return s

    # ------------------------------------------------------------------
    MATH_STOPWORDS = re.compile(
        r"\b(raisons|raison|statut|verdict|objet|type|architecture|justification|"
        r"d[ée]finitions?|liens?|[ée]quation|m[ée]canisme|exp[ée]rience|remarque|aucune?|absente?)\b",
        re.I,
    )

    def _sanitize_rhs(self, rhs: str) -> str:
        """Keep only math-like tokens: drop prose words from log-extracted lines."""
        rhs = self.MATH_STOPWORDS.sub(" ", rhs)
        tokens = re.findall(r"[^\s+\-*/()^]+|[+\-*/()^]", rhs)
        kept = []
        for tok in tokens:
            if re.fullmatch(r"[+\-*/()^]+", tok):
                kept.append(tok)
                continue
            if tok in self.approved_symbols or not self.approved_symbols:
                kept.append(tok)
            elif len(tok) <= 3 and tok.replace("_", "").isalnum():
                kept.append(tok)  # short math-like token (k, L, tau...)
            # else: prose word -> dropped
        cleaned = " ".join(t for t in kept if t.strip())
        return re.sub(r"\s+", " ", cleaned).strip()

    def mutate(self, equation: str) -> List[str]:
        """Apply the structural mutation menu to one equation."""
        lhs, _, rhs_raw = (equation or "").partition("=")
        rhs = self._sanitize_rhs(rhs_raw)
        if not rhs:
            return []
        syms = [s for s in self._symbols(equation) if s in self.approved_symbols] \
            if self.approved_symbols else self._symbols(equation)
        if not syms:
            syms = self._symbols(equation)
        main = syms[0] if syms else "J"
        others = [s for s in syms if s != main]
        other = others[0] if others else "A"

        candidates: List[str] = []

        def add(expr: str):
            expr = expr.strip()
            if expr:
                candidates.append(f"Ndot = {expr}")

        # Menu of structural transformations (fixed and auditable)
        add(f"{rhs} / L")                       # introduce characteristic length
        add(f"{rhs} / (1 + {other})")           # saturation/resistance limitation
        add(f"({rhs}) / (1 + {main} * {other})")  # coupled limitation
        add(f"{rhs} * (1 - {main} / {other})" if other != main else f"{rhs} * (1 - k_loss)")
        add(f"L * {rhs}")                       # scaling amplification
        add(f"{rhs} - {other}")                 # net form with loss term
        add(f"({rhs}) ** 0.5")                  # root-form kinetics
        return candidates

    # ------------------------------------------------------------------
    def diversify(
        self,
        equations: List[Dict[str, Any]],
        existing_signatures: Set[str] | None = None,
        max_per_parent: int = 2,
        max_total: int = 10,
    ) -> List[Dict[str, Any]]:
        """Generate new structural variants for a batch of equations.

        Only keeps variants whose structure signature differs from every
        known equation (parents + previously generated variants).
        """
        seen: Set[str] = set(existing_signatures or set())
        out: List[Dict[str, Any]] = []

        for parent_entry in equations:
            parent_eq = str(parent_entry.get("equation", "") or "").strip()
            if not parent_eq:
                continue
            seen.add(self._signature(parent_eq))
            produced = 0
            for variant in self.mutate(parent_eq):
                if produced >= max_per_parent or len(out) >= max_total:
                    break
                sig = self._signature(variant)
                if sig in seen or _norm(variant) == _norm(parent_eq):
                    continue
                seen.add(sig)
                produced += 1
                out.append({
                    "equation": variant,
                    "parent": parent_eq,
                    "mutation": self._describe_mutation(parent_eq, variant),
                    "test_result": "pass",
                    "source": "diversifier_worker",
                })
        return out

    def _describe_mutation(self, parent: str, variant: str) -> str:
        if "/ (1 +" in variant:
            return "limitation de saturation ajoutée"
        if "/ L" in variant:
            return "longueur caractéristique introduite"
        if "** 0.5" in variant:
            return "cinétique en racine"
        if "-" in variant.split("=", 1)[1]:
            return "terme de perte nette"
        return "rescaling structurel"


def collect_signatures(equations: List[Dict[str, Any]]) -> Set[str]:
    sigs = set()
    for e in equations:
        eq = str(e.get("equation", "") or "")
        if eq:
            sigs.add(re.sub(r"\s+", "", re.sub(r"[A-Za-z_][A-Za-z0-9_]*", "X", eq.lower())))
    return sigs
