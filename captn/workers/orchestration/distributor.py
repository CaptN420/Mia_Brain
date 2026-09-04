"""Distributor worker: routes each task/topic to the best-suited thinkers/workers.

Deterministic (no LLM). Maximizes diversity in the workflow by:
1. Keyword scoring against each corpus thinker's domain + entry text.
2. Round-robin rotation across equally-scored candidates so repeated topics
   visit different thinkers instead of always the same one.
3. Usage accounting: under-used thinkers get a priority boost.
4. **Opposition mode**: when enabled, selects thinkers from deliberately
   contrasting domains to produce more diverse, well-rounded answers.

20 thinkers in the registry covering: philosophy, physics, chemistry,
mathematics, alchemy, computation, and interdisciplinary domains.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Runtime")

try:
    from captn.thinkers import load_all_thinkers  # project-root on sys.path
except ImportError:  # pragma: no cover - direct execution fallback
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from captn.thinkers import load_all_thinkers


# ---------------------------------------------------------------------------
# Enhanced domain route table — maps topic keywords to thinker names
# ---------------------------------------------------------------------------
DOMAIN_ROUTES: Dict[str, List[str]] = {
    # ── Chemistry ─────────────────────────────────────────────────────
    "chemistry": ["chemists", "marie_curie", "alchimie"],
    "chemical": ["chemists", "marie_curie", "alchimie"],
    "reaction": ["chemists", "alchimie", "marie_curie"],
    "molecule": ["chemists", "marie_curie", "physicists"],
    "element": ["chemists", "marie_curie", "alchimie"],
    "radioactivity": ["marie_curie", "physicists", "albert_einstein"],
    "periodic": ["chemists", "marie_curie"],
    # ── Alchemy ───────────────────────────────────────────────────────
    "alchemy": ["alchimie", "chemists", "pythagoras"],
    "alchimie": ["alchimie", "pythagoras"],
    "transmutation": ["alchimie", "marie_curie", "albert_einstein"],
    "philosopher_stone": ["alchimie", "philosophers"],
    "occult": ["alchimie", "plato", "pythagoras"],
    "hermetic": ["alchimie", "pythagoras", "plato"],
    # ── Mathematics ──────────────────────────────────────────────────
    "math": ["mathematicians", "pythagoras", "isaac_newton", "alan_turing"],
    "mathematics": ["mathematicians", "pythagoras", "isaac_newton", "alan_turing"],
    "geometry": ["pythagoras", "mathematicians", "plato"],
    "algebra": ["mathematicians", "alan_turing", "pythagoras"],
    "calculus": ["isaac_newton", "mathematicians", "albert_einstein"],
    "number": ["pythagoras", "mathematicians", "alan_turing"],
    "computation": ["alan_turing", "mathematicians", "leonardo_da_vinci"],
    "algorithm": ["alan_turing", "mathematicians", "leonardo_da_vinci"],
    "logic": ["aristotle", "alan_turing", "plato", "socrates"],
    "proof": ["pythagoras", "aristotle", "mathematicians"],
    "equation": ["mathematicians", "isaac_newton", "albert_einstein"],
    # ── Physics ───────────────────────────────────────────────────────
    "physics": ["physicists", "albert_einstein", "isaac_newton", "scientists"],
    "mechanics": ["isaac_newton", "physicists", "leonardo_da_vinci"],
    "gravity": ["isaac_newton", "albert_einstein", "physicists"],
    "relativity": ["albert_einstein", "physicists", "isaac_newton"],
    "quantum": ["physicists", "albert_einstein", "marie_curie"],
    "energy": ["albert_einstein", "physicists", "isaac_newton"],
    "force": ["isaac_newton", "physicists", "aristotle"],
    "light": ["albert_einstein", "isaac_newton", "physicists"],
    "wave": ["physicists", "albert_einstein", "leonardo_da_vinci"],
    "thermodynamic": ["physicists", "isaac_newton", "scientists"],
    # ── Coding / Computation ───────────────────────────────────────────
    "coding": ["alan_turing", "leonardo_da_vinci", "mathematicians"],
    "code": ["alan_turing", "leonardo_da_vinci", "pythagoras"],
    "programming": ["alan_turing", "mathematicians", "leonardo_da_vinci"],
    "computer": ["alan_turing", "mathematicians", "aristotle"],
    "software": ["alan_turing", "leonardo_da_vinci"],
    "algorithm": ["alan_turing", "mathematicians", "pythagoras"],
    "ai": ["alan_turing", "socrates", "plato", "rene_descartes"],
    "intelligence": ["alan_turing", "rene_descartes", "plato", "socrates"],
    # ── Philosophy ────────────────────────────────────────────────────
    "philosophy": ["philosophers", "socrates", "plato", "aristotle",
                   "confucius", "immanuel_kant", "friedrich_nietzsche",
                   "rene_descartes", "john_locke"],
    "ethics": ["immanuel_kant", "confucius", "socrates", "philosophers",
               "aristotle"],
    "politics": ["john_locke", "aristotle", "plato", "confucius"],
    "justice": ["plato", "aristotle", "immanuel_kant", "john_locke"],
    "existence": ["rene_descartes", "socrates", "plato", "sartre"],
    "knowledge": ["plato", "rene_descartes", "socrates", "aristotle",
                  "immanuel_kant"],
    "truth": ["plato", "socrates", "aristotle", "confucius"],
    "mind": ["rene_descartes", "plato", "immanuel_kant", "socrates"],
    "consciousness": ["rene_descartes", "plato", "immanuel_kant"],
    "free_will": ["immanuel_kant", "socrates", "aristotle",
                  "friedrich_nietzsche"],
    # ── Art & Engineering ─────────────────────────────────────────────
    "art": ["leonardo_da_vinci", "plato", "aristotle"],
    "engineering": ["leonardo_da_vinci", "isaac_newton", "alan_turing"],
    "invention": ["leonardo_da_vinci", "isaac_newton", "alan_turing"],
    "architecture": ["leonardo_da_vinci", "pythagoras", "plato"],
    "design": ["leonardo_da_vinci", "pythagoras", "plato"],
    # ── Biology ───────────────────────────────────────────────────────
    "biology": ["scientists", "leonardo_da_vinci", "aristotle"],
    "life": ["scientists", "aristotle", "leonardo_da_vinci"],
    "evolution": ["scientists", "aristotle", "philosophers"],
    "anatomy": ["leonardo_da_vinci", "scientists", "aristotle"],
    # ── General / Interdisciplinary ──────────────────────────────────
    "science": ["scientists", "physicists", "marie_curie", "isaac_newton"],
    "nature": ["aristotle", "scientists", "pythagoras", "leonardo_da_vinci"],
    "universe": ["albert_einstein", "plato", "pythagoras", "physicists"],
    "time": ["albert_einstein", "isaac_newton", "plato", "immanuel_kant"],
    "space": ["albert_einstein", "pythagoras", "plato", "isaac_newton"],
    "human": ["socrates", "rene_descartes", "immanuel_kant",
              "john_locke", "confucius"],
    "society": ["john_locke", "plato", "aristotle", "confucius"],
    "education": ["socrates", "plato", "aristotle", "confucius"],
}

_WORD_RE = re.compile(r"[a-zàâçéèêëîïôûùüÿñæœ]{3,}")

# Generic words excluded from corpus-overlap scoring
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "between",
    "their", "have", "has", "are", "was", "were", "not", "but", "all", "any",
    "how", "what", "when", "who", "why", "which", "his", "her", "its", "our",
    "les", "des", "une", "dan", "pour", "avec", "sur", "they", "them", "can",
    "will", "would", "could", "should", "may", "might", "about", "than",
}


class Distributor:
    """Standalone deterministic router (usable without the message bus)."""

    def __init__(self):
        self.thinkers: Dict[str, Any] = load_all_thinkers()
        self.usage: Dict[str, int] = {name: 0 for name in self.thinkers}
        self._rotation: Dict[str, int] = {}  # topic-key -> counter
        self._last_opposition: Dict[str, str] = {}  # topic -> last primary domain

    # ------------------------------------------------------------------
    def score_thinker(self, name: str, topic: str) -> float:
        """Deterministic relevance score of one thinker for one topic."""
        t = self.thinkers[name]
        low_topic = (topic or "").lower()
        words = {w for w in _WORD_RE.findall(low_topic) if w not in _STOPWORDS}
        score = 0.0

        # 1. Explicit route table (stem-tolerant: 'chemical' matches 'chemistry')
        for key, targets in DOMAIN_ROUTES.items():
            stem = key[:max(4, len(key) - 4)]
            if name in targets and (key in low_topic or low_topic.find(stem) >= 0):
                score += 5.0

        # Bonus for multiple matching route keys
        route_matches = sum(
            1 for key, targets in DOMAIN_ROUTES.items()
            if name in targets and (key in low_topic or low_topic.find(key[:4]) >= 0)
        )
        score += min(2.0, route_matches * 0.5)

        # 2. Domain-name match (e.g. topic mentions 'chemistry')
        if t.domain.lower() in low_topic or t.domain in words:
            score += 3.0

        # 3. Entry-text keyword overlap
        hits = t.findings(topic or "", limit=12)
        if len(hits) < len(t.entries):
            ratio = len(hits) / max(1, len(t.entries))
            score += 2.0 * ratio + 0.3 * len(hits)

        # 4. Diversity boost for under-used thinkers (quantized so equal
        # usage tiers stay exactly tied and rotation can kick in)
        total = sum(self.usage.values()) or 1
        avg = total / max(1, len(self.usage))
        score += int(max(0.0, (avg - self.usage[name]) * 0.1) * 10) / 10.0

        return round(score, 3)

    # ------------------------------------------------------------------
    def distribute(
        self,
        topic: str,
        top_k: int = 3,
        opposition_mode: bool = False,
        exclude: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Pick the top_k thinkers/workers for a topic.

        Equal scores are rotated round-robin so consecutive calls with the
        same topic yield different orderings -> more diverse debates.

        When ``opposition_mode`` is True, includes thinkers from deliberately
        contrasting domains for more diverse, well-rounded answers.
        """
        exclude = set(exclude or [])
        scored: List[Tuple[str, float]] = [
            (name, self.score_thinker(name, topic))
            for name in self.thinkers
            if name not in exclude
        ]
        # Group by descending score; rotate within equal-score groups
        scored.sort(key=lambda x: -x[1])
        groups: List[List[Tuple[str, float]]] = []
        for item in scored:
            if groups and abs(groups[-1][-1][1] - item[1]) < 1e-9:
                groups[-1].append(item)
            else:
                groups.append([item])

        key = _WORD_RE.findall((topic or "").lower())
        rot_key = "|".join(sorted(set(key))) or "_"
        offset = self._rotation.get(rot_key, 0)
        self._rotation[rot_key] = offset + 1

        flat: List[Tuple[str, float]] = []
        for group in groups:
            n = len(group)
            rotated = [group[(offset + i) % n] for i in range(n)]
            flat.extend(rotated)

        chosen = flat[:max(1, top_k)]
        for name, _ in chosen:
            self.usage[name] += 1

        # ── Opposition mode: pick thinkers from contrasting domains ──
        opposing = []
        if opposition_mode and len(chosen) >= 1:
            try:
                from captn.thinkers import get_opposing_domain, load_thinkers_by_domain
            except ImportError:
                pass
            else:
                # Get the domain of the best-matching thinker
                best_name = chosen[0][0]
                best_t = self.thinkers.get(best_name)
                if best_t and hasattr(best_t, "domain"):
                    opp_domain = get_opposing_domain(best_t.domain)
                    # Find unselected thinkers in opposing domain
                    domain_thinkers = load_thinkers_by_domain(opp_domain)
                    for name, t in sorted(domain_thinkers.items()):
                        if name not in {n for n, _ in chosen} and name not in exclude:
                            score = self.score_thinker(name, topic)
                            opposing.append((name, round(score + 0.01, 3)))
                            if len(opposing) >= max(1, top_k // 2):
                                break
                    # Ensure diversity by tracking last opposition
                    self._last_opposition[topic] = opp_domain

        combined = chosen + opposing
        final_selection = combined[:max(top_k, len(chosen) + len(opposing))]

        return {
            "topic": topic,
            "selected": [name for name, _ in final_selection],
            "scores": dict(final_selection),
            "usage": dict(self.usage),
            "opposition_mode": opposition_mode,
            "opposition_count": len(opposing),
            "timestamp": time.time(),
        }

    def status(self) -> Dict[str, Any]:
        return {
            "thinkers_loaded": sorted(self.thinkers.keys()),
            "usage": dict(self.usage),
        }


class DistributorWorker(Distributor):
    """Message-bus plugin wrapper (captn Plugin interface)."""

    def __init__(self, bus=None):
        Distributor.__init__(self)
        self.bus = bus
        self.is_active = False
        self.name = "distributor"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Loaded {len(self.thinkers)} thinkers for routing "
                    f"(20 thinkers, {len(DOMAIN_ROUTES)} domain routes).")
        self.is_active = True
        return True

    def execute(self, message) -> None:
        from captn.runtime.base import Message

        payload = getattr(message, "payload", {}) or {}
        task_id = payload.get("task_id")
        topic = str(payload.get("topic", "") or "")
        top_k = int(payload.get("top_k", 3) or 3)
        opposition_mode = bool(payload.get("opposition_mode", False))

        result = self.distribute(topic, top_k=top_k, opposition_mode=opposition_mode)
        logger.info(f"[{self.name}] '{topic}' -> {result['selected']} "
                    f"(opposition={'on' if opposition_mode else 'off'})")

        if self.bus is not None:
            self.bus.publish(Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "distribution": result,
                },
            ))

    def shutdown(self) -> bool:
        self.is_active = False
        return True