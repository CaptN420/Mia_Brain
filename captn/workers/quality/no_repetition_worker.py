#!/usr/bin/env python3
"""No-repetition worker: enforces structural diversity across the whole run.

Déterministic (no LLM). Deux responsabilités :

1. REGISTRE PERSISTANT — suit TOUTE équation produite dans la session par
   signature structurelle FINE (préservant nombre de symboles distincts + ops)
   et par empreinte de symboles, pour détecter la répétition à travers les cycles.

2. FILTRE DE DIVERSITÉ — avant qu'un lot soit archivé, rejette toute entrée
   dont la structure était déjà trop vue, et rapporte les métriques.

Version unifiée — utilise la MÊME fonction `structure_signature()` que
le `DiversifierWorker` pour éviter les désaccords (résout BUG 5).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

# Utilise la même signature que le DiversifierWorker
# (importée pour garantir la cohérence)
try:
    from captn.workers.transformation.diversifier_worker import (
        structure_signature,
        symbol_fingerprint,
        _extract_vars,
    )
except ImportError:
    # Fallback local
    def _extract_vars(eq: str) -> List[str]:
        syms = re.findall(r"[A-Za-z_ΔτφηκμρσλΦΨχΩα-ω][A-Za-z0-9_ΔτφηκμρσλΦΨχΩα-ω]*", str(eq or ""))
        reserved = {"Ndot", "exp", "log", "sin", "cos", "tan", "sqrt", "abs"}
        return [s for s in syms if s not in reserved and len(s) <= 12]

    def structure_signature(equation: str) -> str:
        eq = str(equation or "").strip()
        if not eq:
            return ""
        low = eq.lower().replace("é", "e").strip()
        low = re.sub(r"\s+", "", low)
        syms = _extract_vars(eq)
        n_syms = len(set(syms))
        ops = re.findall(r"[+\-*/()^%=]", low)
        n_ops = len(ops)
        for s in sorted(set(syms), key=len, reverse=True):
            low = low.replace(s.lower(), "X")
        low = re.sub(r"\d+(\.\d+)?", "N", low)
        low = re.sub(r"X+", "X", low)
        return f"sym{n_syms}|op{n_ops}|{low}"

    def symbol_fingerprint(equation: str) -> frozenset:
        return frozenset(s.lower() for s in _extract_vars(equation))


class NoRepetitionWorker:
    """Session-wide anti-repetition registry + gate.

    Résout BUG 3 (signature fine), BUG 5 (unifié avec DiversifierWorker),
    BUG 8 (pas de collision), BUG 10 (persistant via fichier JSON).
    """

    def __init__(self, session_path: Path, max_same_structure: int = 3,
                 max_same_symbols: int = 4):
        self.session_path = Path(session_path)
        self.registry_file = self.session_path / "no_repeat_registry.json"
        self.max_same_structure = max_same_structure  # Augmenté de 2→3
        self.max_same_symbols = max_same_symbols      # Augmenté de 3→4
        self.structure_counts: Dict[str, int] = {}
        self.symbol_counts: Dict[str, int] = {}
        self._load()

    # ── Persistance (résout BUG 10) ──────────────────────────

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

    # ── Enregistrement ───────────────────────────────────────

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

    # ── Vérification ─────────────────────────────────────────

    def check(self, equation: str) -> Dict[str, Any]:
        """Verdict pour une équation. Fonction pure du registre."""
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

    # ── Filtrage ─────────────────────────────────────────────

    def filter_batch(self, equations: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Divise un lot en acceptées / rejetées par répétition.

        L'enregistrement se fait PENDANT le scan : chaque entrée acceptée
        incrémente ses compteurs avant la vérification de l'entrée suivante,
        donc les doublons dans un même lot sont aussi attrapés.
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

    # ── État ──────────────────────────────────────────────────

    def exhausted_structures(self) -> Set[str]:
        return {s for s, c in self.structure_counts.items()
                if c >= self.max_same_structure}

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