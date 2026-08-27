#!/usr/bin/env python3
"""Deterministic safety gate for archiving equations into the Alchimie library.

An equation produced by the debate loop must pass this test BEFORE it is
stored in alchimie/library. Purely rule-based (no LLM):

- SAFE      : equation is physically/scientifically benign -> archive allowed
- QUARANTINE: ambiguous domain or missing context -> stored aside, NOT archived
- DANGEROUS : matches hazardous-domain signals (explosives, toxins, weapons,
              radioactivity handling, illicit substances) -> NEVER archived

The test runs once per evolution cycle (after validation, before archiving).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Hazard signal table: (category, compiled regex). Deterministic and auditable.
HAZARD_SIGNALS: List[Tuple[str, re.Pattern]] = [
    ("explosives", re.compile(
        r"explos\w*|tnt|nitro(?!g[ée]ne)|trinitrotol|pentrite|hexog[èe]ne|c4|detonat\w*|d[ée]flagrat\w*",
        re.I)),
    ("toxins", re.compile(
        r"poison\w*|toxin\w*|cyanur\w*|cyanure|sarin|vx|novitchok|ricine|arsenic\b|plomb\s*(métal|metal)?",
        re.I)),
    ("chemical_weapons", re.compile(
        r"arme\s+chimique|chemical\s+weapon|moutarde|yp[ée]rite|chloramine|nerve\s+agent",
        re.I)),
    ("nuclear_hazard", re.compile(
        r"uranium\s+(enrichi|235)|plutonium|criticit[ée]|masse\s+critique|arme\s+nucl[ée]aire|nuclear\s+weapon",
        re.I)),
    ("illicit_drugs", re.compile(
        r"stup[ée]fiant\w*|narcotique\w*|coca[iï]ne|h[ée]ro[ïi]ne|m[ée]thamph[ée]tamine|fentanyl",
        re.I)),
    ("biohazard", re.compile(
        r"bioterror\w*|pathog[èe]ne\s+mortel|agent\s+biologique|virus\s+l[ée]tal",
        re.I)),
    ("dangerous_reactions", re.compile(
        r"thermite|r[ée]action\s+(exothermique\s+)?(violente|dangereuse)|peroxide\s+explosif|ac[ée]tyl[èe]ne\s+cuivre",
        re.I)),
]

# Domains considered inherently sensitive even without explicit signals.
SENSITIVE_DOMAINS = {
    "explosifs", "explosives", "armement", "weapons", "toxicologie_militaire",
    "nucleaire_militaire", "guerre_chimique",
}

# Verdicts
SAFE = "safe"
QUARANTINE = "quarantine"
DANGEROUS = "dangerous"


class SafetyGate:
    """Pre-archive deterministic safety test."""

    def __init__(self, strictness: str = "normal"):
        # normal: unknown domains go to quarantine
        # strict: anything not clearly transport/mass-transfer science is quarantined
        self.strictness = strictness

    # ------------------------------------------------------------------
    def test_equation(self, equation_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Run the safety test on one equation entry.

        Returns {verdict, reasons, category} - verdict in SAFE/QUARANTINE/DANGEROUS.
        """
        eq_text = str(equation_entry.get("equation", "") or "")
        blob_parts = [eq_text]
        for key in ("object_calculated", "law_type", "architecture", "mechanism",
                    "summary", "source", "domain", "definition"):
            val = equation_entry.get(key)
            if val:
                blob_parts.append(str(val))
        blob = "\n".join(blob_parts)

        reasons: List[str] = []
        category = ""

        # Gate A: hazardous signal scan
        for cat, pattern in HAZARD_SIGNALS:
            match = pattern.search(blob)
            if match:
                category = cat
                reasons.append(f"signal de danger '{cat}': '{match.group(0)}'")
                return {"verdict": DANGEROUS, "reasons": reasons, "category": category,
                        "tested": True, "archivable": False}

        # Gate B: explicitly sensitive domain field
        domain = str(equation_entry.get("domain", "") or "").lower().strip()
        if domain in SENSITIVE_DOMAINS:
            reasons.append(f"domaine sensible déclaré: {domain}")
            return {"verdict": DANGEROUS, "reasons": reasons, "category": "sensitive_domain",
                    "tested": True, "archivable": False}

        # Gate C: structure sanity - must look like an actual scientific equation
        if "=" not in eq_text or len(eq_text) < 5:
            reasons.append("équation mal formée ou absente")
            return {"verdict": QUARANTINE, "reasons": reasons, "category": "malformed",
                    "tested": True, "archivable": False}
        # No exotic characters suggesting pasted prose instead of math
        letters = sum(ch.isalpha() for ch in eq_text)
        if letters > len(eq_text) * 0.6:
            reasons.append("trop de texte libre pour une équation")
            return {"verdict": QUARANTINE, "reasons": reasons, "category": "malformed",
                    "tested": True, "archivable": False}

        # Gate D (strict mode): whitelist of accepted physics domains
        if self.strictness == "strict":
            allowed_tokens = ("transport", "flux", "diffusion", "concentration",
                              "cinétique", "cinetique", "structure", "temps",
                              "résistance", "resistance", "surface", "gradient")
            haystack = f"{blob.lower()} {domain}"
            if not any(tok in haystack for tok in allowed_tokens):
                reasons.append("domaine hors périmètre scientifique autorisé (mode strict)")
                return {"verdict": QUARANTINE, "reasons": reasons, "category": "out_of_scope",
                        "tested": True, "archivable": False}

        reasons.append("aucun signal de danger; équation scientifique bénigne")
        return {"verdict": SAFE, "reasons": reasons, "category": "",
                "tested": True, "archivable": True}

    def test_batch(self, equations: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Test a batch; returns {'safe': [...], 'quarantine': [...], 'dangerous': [...]}."""
        buckets: Dict[str, List[Dict[str, Any]]] = {"safe": [], "quarantine": [], "dangerous": []}
        for entry in equations:
            result = self.test_equation(entry)
            tagged = dict(entry)
            tagged["safety_test"] = result
            buckets[result["verdict"]].append(tagged)
        return buckets
