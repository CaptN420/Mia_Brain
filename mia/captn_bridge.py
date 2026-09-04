#!/usr/bin/env python3
"""Captn <-> MIA bridge: connects captn thinkers/workers into the debate workflow.

Deterministic by design:
- Workers (extractor -> normalizer -> translator -> validator) process the
  thinker knowledge corpora (alchimie_thinker.txt, physicists.txt, ...) with
  pure text processing - no LLM.
- The Thinker synthesis becomes a knowledge block injected in agent proposals.
- MathValidator and MirrorAgent are exposed as extra deterministic gates.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root = directory containing the captn package (CaptN-BRAIN-main/CaptN-BRAIN-main)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

CAPTN_DIR = Path(__file__).resolve().parent.parent / "captn"

THINKER_CORPORA = [
    "alchimie_thinker.txt",
    "chemists.txt",
    "mathematicians.txt",
    "philosophers.txt",
    "physicists.txt",
    "scientists.txt",
]

# Deterministic domain keywords used to route corpus entries to agents
DOMAIN_KEYWORDS = {
    "flux": ["flux", "transport", "diffusion", "gradient"],
    "equation": ["equation", "law", "theory", "relativity", "quantum", "maxwell"],
    "structure": ["surface", "geometry", "structure", "material"],
    "cinetique": ["kinetic", "reaction", "rate", "distillation"],
}


class CaptnBridge:
    """Bridge exposing captn thinkers/workers to the MIA debate runtime.
    
    Singleton: one bridge per process. Components (Thinker, MathValidator,
    MirrorAgent) are initialized once and reused across debate sessions.
    """
    
    _instance = None

    def __new__(cls, cfg=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, cfg=None):
        if self._initialized:
            return
        self._initialized = True
        self.cfg = cfg
        self.available = False
        self.thinker = None
        self.math_validator = None
        self.mirror_agent = None
        self._corpus: Dict[str, str] = {}
        self._knowledge_block_cache: Optional[str] = None
        self._init_captn_components()
        self._load_corpus()

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------
    def _init_captn_components(self) -> None:
        """Import captn runtime components lazily; degrade gracefully.
        
        Each component is cached on the instance so re-initialization
        (e.g. when BaseDebateCore is recreated) reuses the same objects.
        """
        try:
            from captn.runtime.thinker import Thinker  # noqa: E402
            if self.thinker is None:
                self.thinker = Thinker(alchimie_manager=None)
            self.available = True
        except Exception as exc:  # pragma: no cover
            print(f"[BRIDGE] captn Thinker indisponible: {type(exc).__name__}: {exc}")
        try:
            from tools.math_validator import MathValidator  # noqa: E402
            if self.math_validator is None:
                self.math_validator = MathValidator()
            self.available = True
        except Exception as exc:
            print(f"[BRIDGE] captn MathValidator indisponible: {exc}")
        try:
            from captn.runtime.mirror_agent import MirrorAgent  # noqa: E402
            if self.mirror_agent is None:
                self.mirror_agent = MirrorAgent(bus=None, alchimie_manager=None)
                self.mirror_agent.initialize()
            self.available = True
        except Exception as exc:
            print(f"[BRIDGE] captn MirrorAgent indisponible: {exc}")

    def _load_corpus(self) -> None:
        for name in THINKER_CORPORA:
            path = CAPTN_DIR / name
            if path.exists():
                try:
                    self._corpus[name] = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Worker pipeline (deterministic): extract -> normalize -> translate -> validate
    # ------------------------------------------------------------------
    def run_worker_pipeline(self, topic: str = "") -> Dict[str, Any]:
        """Run the 4-step worker chain over the corpus for a given topic."""
        # Step 1 - Extractor: pull raw lines matching the topic
        raw_lines: List[str] = []
        low_topic = (topic or "").lower().strip()
        keywords = set()
        for kws in DOMAIN_KEYWORDS.values():
            keywords.update(kws)
        if low_topic:
            keywords.add(low_topic)

        for _name, text in sorted(self._corpus.items()):
            for line in text.splitlines():
                line = line.strip()
                if not line or line[0].isdigit() is False and ":" not in line and len(line) < 40:
                    continue
                low = line.lower()
                if any(k in low for k in keywords):
                    raw_lines.append(line)
        raw_lines = raw_lines[:12]

        # Step 2 - Normalizer: strip numbering/markdown, collapse spaces
        normalized: List[str] = []
        for line in raw_lines:
            clean = re.sub(r"^[\d\.\)\-\*\s]+", "", line).strip()
            clean = re.sub(r"\s+", " ", clean)
            if len(clean) > 15:
                normalized.append(clean)

        # Step 3 - Translator: convert each entry into a structured finding
        findings: List[Dict[str, Any]] = []
        for entry in normalized:
            name_part, _, detail = entry.partition(" - ")
            findings.append({
                "source": name_part.strip() or entry[:40],
                "detail": detail.strip() or entry,
                "rule_version": "captn-worker-v1",
            })

        # Step 4 - Validator: keep only complete findings
        valid_findings = [f for f in findings if f["source"] and f["detail"]]

        return {
            "extracted": len(raw_lines),
            "normalized": len(normalized),
            "translated": len(findings),
            "validated": valid_findings,
            "status": "success" if valid_findings else "warning",
        }

    # ------------------------------------------------------------------
    # Thinker synthesis
    # ------------------------------------------------------------------
    def synthesize(self, topic: str = "") -> str:
        """Synthesize worker results into a compact knowledge block."""
        results = self.run_worker_pipeline(topic)
        findings = results["validated"]
        if not findings:
            return ""
        lines = ["Apports workers/thinkers (déterministe) :"]
        for f in findings[:6]:
            lines.append(f"- {f['source']} : {f['detail'][:140]}")
        confidence = max(0, 100 - (results["normalized"] - len(findings)) * 5)
        lines.append(f"- Confiance synthèse : {confidence}/100")
        return "\n".join(lines)

    def knowledge_block(self, topic: str = "", force_refresh: bool = False) -> str:
        if force_refresh or self._knowledge_block_cache is None:
            self._knowledge_block_cache = self.synthesize(topic)
        return self._knowledge_block_cache

    # ------------------------------------------------------------------
    # Extra deterministic gates for the debate
    # ------------------------------------------------------------------
    def math_classify(self, expression: str, rule_used: str = "STANDARD_MATHEMATICS") -> Dict[str, Any]:
        """Classify an equation/expression via captn MathValidator."""
        if not self.math_validator:
            return {"classification": "UNDEFINED", "available": False}
        try:
            result = self.math_validator.validate_transformation({
                "rule_used": rule_used,
                "expression": expression,
            })
            result["available"] = True
            return result
        except Exception as exc:
            return {"classification": "UNDEFINED", "error": str(exc), "available": False}

    def mirror_score(self, hypothesis: str) -> Dict[str, Any]:
        """Mirror reasoning score via captn MirrorAgent (deterministic rules)."""
        if not self.mirror_agent:
            return {"score": None, "available": False}
        try:
            mirrored = self.mirror_agent._generate_mirrored_reasoning(hypothesis, [])
            validation = self.mirror_agent._validate_mirror_operation(hypothesis, mirrored)
            score = self.mirror_agent._calculate_mirror_score(mirrored, validation)
            return {"score": score, "mirrored": mirrored, "available": True}
        except Exception as exc:
            return {"score": None, "error": str(exc), "available": False}

    def status(self) -> Dict[str, Any]:
        return {
            "thinker": self.thinker is not None,
            "math_validator": self.math_validator is not None,
            "mirror_agent": self.mirror_agent is not None,
            "corpus_files": sorted(self._corpus.keys()),
        }
