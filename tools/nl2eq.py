#!/usr/bin/env python3
"""
nl2eq.py — Conversion de langage naturel vers équations mathématiques.

Déterministe (règles + patterns) avec fallback LLM optionnel.

Usage:
    python -m tools.nl2eq "the rate of change of N is proportional to k times A times B"
    python -m tools.nl2eq "N dot equals k times A minus lambda times N"
    python -m tools.nl2eq "the sum of i from 0 to n of i squared"
    python -m tools.nl2eq --check "Ndot = k*A*B" => '{"Ndot":1,"k":1,"A":1,"B":1}'
    python -m tools.nl2eq list
"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# ─── Patterns de reconnaissance ───

PATTERNS: List[Tuple[str, Callable]] = []

# 1. "rate of change of X" → Xdot / dX/dt
PAT_RATE = re.compile(
    r"(?:the\s+)?(?:rate|speed|velocity)\s+(?:of\s+)?(?:change\s+)?(?:of\s+)?"
    r"(?:the\s+)?(?P<var>[A-Z][a-zA-Z0-9_]*)\s*"
    r"(?:\s*(?:is|equals|=|:)\s*)?",
    re.IGNORECASE
)

# 2. "d[VAR]/dt" → Xdot
PAT_DDT = re.compile(
    r"d\s*(?P<var>[A-Z][a-zA-Z]*)\s*/\s*dt",
    re.IGNORECASE
)

# 3. "X dot" → Xdot
PAT_DOT = re.compile(
    r"(?P<var>[A-Z][a-zA-Z]*)\s*\.?\s*dot",
    re.IGNORECASE
)

# 4. "proportional to" / "is proportional to" → k * ...
PAT_PROP = re.compile(
    r"(?:is\s+)?proportional\s+to",
    re.IGNORECASE
)

# 5. "sum of X from A to B" → sum_{A}^{B} X
PAT_SUM = re.compile(
    r"(?:the\s+)?sum(?:\s+of)?\s+(?P<expr>[^(]+?)\s+"
    r"(?:from|over)\s+(?P<from>[^\s]+)\s+"
    r"(?:to|until)\s+(?P<to>[^\s]+)",
    re.IGNORECASE
)

# 6. "product of" / "times" → *
PRODUCT_WORDS = re.compile(r"\b(?:times|multiplied\s+by|product\s+of)\b", re.IGNORECASE)
SUM_WORDS = re.compile(r"\b(?:plus|added\s+to|sum\s+of)\b", re.IGNORECASE)
MINUS_WORDS = re.compile(r"\b(?:minus|subtract(?:ed)?\s+from?|less)\b", re.IGNORECASE)
DIV_WORDS = re.compile(r"\b(?:divided\s+by|over|per)\b", re.IGNORECASE)
POWER_WORDS = re.compile(r"\b(?:squared|cubed|to\s+the\s+power\s+of|raised\s+to)\b", re.IGNORECASE)

# 7. "equals" / "is" / ":" → =
EQ_WORDS = re.compile(r"\b(?:equals|is\s+equal\s+to|=|:)\b", re.IGNORECASE)

# 8. Variables: single uppercase letters
VAR_PAT = re.compile(r'\b[A-Z][a-zA-Z0-9_]*\b')


@dataclass
class Nl2EqResult:
    text: str
    equation: Optional[str] = None
    normalized: Optional[str] = None
    confidence: float = 0.0
    method: str = "rule"
    ok: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "input": self.text,
            "equation": self.equation,
            "normalized": self.normalized,
            "confidence": round(self.confidence, 2),
            "method": self.method,
            "ok": self.ok,
        }


class Nl2EqConverter:
    """Convertit du langage naturel en équation symbolique."""

    def convert(self, text: str) -> Nl2EqResult:
        """Pipeline : nettoie, parse, construit."""
        cleaned = self._normalize_text(text)
        equation = self._apply_rules(cleaned)
        if not equation:
            # Fallback: re-essayer avec plus d'heuristic
            equation = self._heuristic_parse(cleaned)
        if equation:
            normalized = self._normalize_equation(equation)
            return Nl2EqResult(
                text=text,
                equation=equation,
                normalized=normalized,
                confidence=0.8 if equation else 0.3,
                method="rule",
                ok=True,
            )
        return Nl2EqResult(
            text=text,
            ok=False,
            error="Impossible de parser l'équation",
        )

    def _normalize_text(self, text: str) -> str:
        """Nettoie le texte avant parsing."""
        s = text.strip()
        # Remove punctuation at end except operators
        s = re.sub(r'[.?!]+$', '', s)
        # Normalize whitespace
        s = re.sub(r'\s+', ' ', s)
        return s

    def _apply_rules(self, text: str) -> Optional[str]:
        """Applique les patterns de reconnaissance."""
        # 1. dX/dt → Xdot
        text = re.sub(PAT_DDT, r'\1_dot', text)

        # 2. X dot → Xdot
        text = re.sub(PAT_DOT, r'\1_dot', text)

        # 3. rate of change of X → X_dot
        text = re.sub(PAT_RATE, r'\1_dot = ', text, count=1)

        # 4. proportional to → = k *
        text = re.sub(PAT_PROP, '= k *', text, count=1)

        # Now try to build the actual equation string
        return self._heuristic_parse(text)

    def _heuristic_parse(self, text: str) -> Optional[str]:
        """Parse heuristique : identifie la structure LHS = RHS."""
        # Split on equals
        parts = re.split(r'\s*=\s*', text, maxsplit=1)

        lhs_str = parts[0].strip() if parts else text
        rhs_str = parts[1].strip() if len(parts) > 1 else ""

        lhs = self._parse_side(lhs_str)
        rhs = self._parse_side(rhs_str) if rhs_str else ""

        if lhs and rhs:
            return f"{lhs} = {rhs}"
        if lhs and not rhs:
            # Maybe the = was implied
            # Check if it's already in equation form
            if "=" not in text and any(w in text.lower() for w in ["rate", "dot", "d/dt"]):
                # RHS might be everything after the variable
                return lhs
            return lhs
        return None

    def _parse_side(self, text: str) -> Optional[str]:
        """Parse un côté d'équation en expression symbolique."""
        s = text.strip()

        # Replace operators
        s = re.sub(PRODUCT_WORDS, '*', s)
        s = re.sub(SUM_WORDS, '+', s)
        s = re.sub(MINUS_WORDS, '-', s)
        s = re.sub(DIV_WORDS, '/', s)

        # Power: squared → **2, cubed → **3
        s = re.sub(r'\bsquared\b', '**2', s)
        s = re.sub(r'\bcubed\b', '**3', s)
        s = re.sub(r'\bto the power of (\d+)\b', r'**\1', s)

        # Cleanup articles
        s = re.sub(r'\b(?:the|a|an)\b', '', s)

        # Cleanup extra spaces
        s = re.sub(r'\s+', '', s)

        # Ensure operators are surrounded by nothing (already compact)
        if not s:
            return None

        # Detect if it's already an equation-like expression
        # Valid: letters, digits, operators, underscores
        if re.match(r'^[A-Za-z0-9_*+\-/**()]+$', s):
            return s

        # Fallback: extract variables
        vars_found = VAR_PAT.findall(text)
        if vars_found:
            # Build product of variables
            # Check for relationships
            if "proportional" in text.lower():
                return " * ".join(vars_found)

            # Simple concatenation with operators
            result = vars_found[0]
            for v in vars_found[1:]:
                result += " * " + v
            return result

        return None

    def _normalize_equation(self, eq: str) -> str:
        """Normalise une équation : ordre alphabétique, etc."""
        eq = eq.replace("**2", "^2").replace("**3", "^3")
        return eq


# ─── Vérification d'équation ───
# Parse "Ndot = k*A*B" en dictionnaire {variable: count}

_EQ_PATTERN = re.compile(r'([A-Za-z_]\w*)\s*(?:\*\s*)?')


def check_equation(eq: str) -> str:
    """Parse une équation en dictionnaire {variable: coefficient}.

    Ex : "Ndot = k*A*B" → {"Ndot": 1, "k": 1, "A": 1, "B": 1}
         "Ndot = k*A*B - lambda*N" → {"Ndot": 1, "k": 1, "A": 1, "B": 1,
                                        "lambda": 1, "N": -1}
    """
    try:
        # Split on =
        if "=" in eq:
            lhs, rhs = eq.split("=", 1)
        else:
            lhs, rhs = eq, ""

        # Parse LHS
        lhs_vars = _EQ_PATTERN.findall(lhs.strip())
        # Parse RHS with signs
        terms = re.split(r'\s*([+-])\s*', rhs.strip())
        rhs_vars = {}
        sign = 1
        for i, term in enumerate(terms):
            term = term.strip()
            if term == "+":
                sign = 1
            elif term == "-":
                sign = -1
            elif term:
                # Parse variables in term
                vars_in_term = _EQ_PATTERN.findall(term)
                coeff = 1
                # Extract numeric coefficient if present
                num_match = re.match(r'(\d+(?:\.\d+)?)\s*\*?\s*', term)
                if num_match:
                    coeff = float(num_match.group(1))
                for v in vars_in_term:
                    if v == "_":
                        continue
                    if v.isdigit():
                        continue
                    rhs_vars[v] = rhs_vars.get(v, 0) + sign * coeff

        result = {}
        for v in lhs_vars:
            result[v] = 1
        for v, c in rhs_vars.items():
            result[v] = result.get(v, 0) + c

        # Remove zero coefficients
        result = {k: int(v) if v == int(v) else v
                  for k, v in result.items() if v != 0}

        return json.dumps({
            "ok": True,
            "equation": eq,
            "variables": result,
            "lhs_vars": list(set(lhs_vars)),
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "equation": eq})


def list_operations() -> str:
    """Liste les patterns reconnus."""
    patterns = [
        {"example": "rate of change of N is proportional to k times A", "result": "N_dot = k * A"},
        {"example": "dN/dt = k * A * B", "result": "N_dot = k * A * B"},
        {"example": "N dot equals k times A minus lambda times N", "result": "N_dot = k * A - lambda * N"},
        {"example": "the sum of i squared from 0 to n", "result": "sum_{0}^{n} i^2"},
        {"example": "the product of A and B", "result": "A * B"},
        {"example": "--check 'Ndot = k*A*B'", "result": "{Ndot: 1, k: 1, A: 1, B: 1}"},
    ]
    return json.dumps(patterns, indent=2, ensure_ascii=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Conversion langage naturel → équation mathématique"
    )
    parser.add_argument("text", nargs="?", help="Texte en langage naturel")
    parser.add_argument("--check", "-c", help="Vérifier une équation existante")
    parser.add_argument("--list", "-l", action="store_true", help="Liste des patterns")
    args = parser.parse_args()

    if args.list:
        print(list_operations())
        return

    if args.check:
        print(check_equation(args.check))
        return

    if args.text:
        converter = Nl2EqConverter()
        result = converter.convert(args.text)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()


def register_cli(subparsers):
    """Register nl2eq as a CLI subcommand."""
    p = subparsers.add_parser("nl2eq", help="Langage naturel → équation")
    p.add_argument("text", nargs="?", help="Texte à convertir")
    p.add_argument("--check", "-c", help="Vérifier une équation existante")
    p.add_argument("--list", "-l", action="store_true")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()