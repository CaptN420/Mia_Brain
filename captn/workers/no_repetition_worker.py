#!/usr/bin/env python3
"""No-repetition worker: enforces structural diversity across the whole run.

Deterministic (no LLM). Two responsibilities:

1. PERSISTENT REGISTRY - tracks every equation ever produced in the session
   (approved, partial, archived, variant) by structure signature AND by
   symbol-set fingerprint, so repetition is detected across cycles, not just
   within one turn.

2. DIVERSITY ENFORCEMENT - before an equation batch is archived, rejects any
   entry whose structure was already seen too often, and reports diversity
   metrics. Works with DiversifierWorker which consults the registry to know
   which structures are exhausted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

SYMBOL_RE = re.compile(r"[A-Za-z_Δτφηκμρσλ][A-Za-z0-9_Δτφηκμρσλ]*")
NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


def structure_signature(equation: str) -> str:
    """Abstract operator skeleton of an equation ('X*X/X' style)."""
    eq = str(equation or "").lower().replace("é", "e").strip()
    eq = SYMBOL_RE.sub("X", eq)
    eq = NUM_RE.sub("N", eq)
    eq = re.sub(r"\s+", "", eq)
    eq = re.sub(r"X+", "X", eq)
    return eq


def symbol_fingerprint(equation: str) -> frozenset:
    """Set of symbols used - catches 'same symbols reshuffled' repetition."""
    return frozenset(s.lower() for s in SYMBOL_RE.findall(str(equation or "")))


class NoRepetitionWorker:
    """Session-wide anti-repetition registry + gate."""

    def __init__(self, session_path: Path, max_same_structure: int = 2,
                 max_same_symbols: int = 3):
        self.session_path = Path(session_path)
        self.registry_file = self.session_path / "no_repeat_registry.json"
        self.max_same_structure = max_same_structure
        self.max_same_symbols = max_same_symbols
        self.structure_counts: Dict[str, int] = {}
        self.symbol_counts: Dict[str, int] = {}
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.registry_file.exists():
            try:
                data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                self.structure_counts = dict(data.get("structure_counts", {}))
                self.symbol_counts = dict(data.get("symbol_counts", {}))
            except Exception:
                pass

    def _save(self) -> None:
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            self.registry_file.write_text(json.dumps({
                "structure_counts": self.structure_counts,
                "symbol_counts": self.symbol_counts,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    def register(self, equation: str) -> None:
        sig = structure_signature(equation)
        fp_key = "|".join(sorted(symbol_fingerprint(equation)))
        if sig:
            self.structure_counts[sig] = self.structure_counts.get(sig, 0) + 1
        if fp_key:
            self.symbol_counts[fp_key] = self.symbol_counts.get(fp_key, 0) + 1
        self._save()

    def register_batch(self, equations: List[Dict[str, Any]]) -> None:
        for e in equations:
            self.register(str(e.get("equation", "") or ""))

    # ------------------------------------------------------------------
    def check(self, equation: str) -> Dict[str, Any]:
        """Verdict for a single equation. Pure function of the registry."""
        eq = str(equation or "").strip()
        sig = structure_signature(eq)
        fp = symbol_fingerprint(eq)
        fp_key = "|".join(sorted(fp))
        reasons: List[str] = []

        struct_count = self.structure_counts.get(sig, 0)
        sym_count = self.symbol_counts.get(fp_key, 0)

        if not sig or "=" not in eq:
            return {"accepted": False, "reason": "équation absente ou mal formée",
                    "structure_count": struct_count, "symbol_count": sym_count}
        if struct_count >= self.max_same_structure:
            reasons.append(f"structure déjà vue {struct_count}x (limite {self.max_same_structure})")
        if len(fp) >= 2 and sym_count >= self.max_same_symbols:
            reasons.append(f"mêmes symboles réutilisés {sym_count}x (limite {self.max_same_symbols})")

        if reasons:
            return {"accepted": False, "reason": "; ".join(reasons),
                    "structure_count": struct_count, "symbol_count": sym_count}
        return {"accepted": True, "reason": "structure et symboles acceptables",
                "structure_count": struct_count, "symbol_count": sym_count}

    # ------------------------------------------------------------------
    def filter_batch(self, equations: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Split a batch into accepted / rejected by repetition.

        Registration happens DURING the scan: each accepted entry increments
        its counters before the next entry is checked, so duplicates inside
        a single batch are caught too.
        """
        accepted, rejected = [], []
        for entry in equations:
            eq = str(entry.get("equation", "") or "")
            verdict = self.check(eq)
            tagged = dict(entry)
            tagged["no_repeat_check"] = verdict
            if verdict["accepted"]:
                self.register(eq)
                accepted.append(tagged)
            else:
                rejected.append(tagged)
        return {"accepted": accepted, "rejected": rejected}

    # ------------------------------------------------------------------
    def exhausted_structures(self) -> Set[str]:
        """Signatures that hit the repetition limit - DiversifierWorker must avoid these."""
        return {s for s, c in self.structure_counts.items() if c >= self.max_same_structure}

    def known_signatures(self) -> Set[str]:
        return set(self.structure_counts.keys())

    def diversity_metrics(self) -> Dict[str, Any]:
        total = sum(self.structure_counts.values())
        distinct = len(self.structure_counts)
        return {
            "total_equations_seen": total,
            "distinct_structures": distinct,
            "diversity_ratio": round(distinct / max(1, total), 3),
            "exhausted_structures": len(self.exhausted_structures()),
        }
