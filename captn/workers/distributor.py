"""Distributor worker: routes each task/topic to the best-suited thinkers/workers.

Deterministic (no LLM). Maximizes diversity in the workflow by:
1. Keyword scoring against each corpus thinker's domain + entry text.
2. Round-robin rotation across equally-scored candidates so repeated topics
   visit different thinkers instead of always the same one.
3. Usage accounting: under-used thinkers get a priority boost.
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


# Deterministic domain keyword map (topic -> thinker names)
DOMAIN_ROUTES: Dict[str, List[str]] = {
    "chemistry": ["chemists", "alchimie"],
    "alchemy": ["alchimie", "chemists"],
    "math": ["mathematicians"],
    "physics": ["physicists", "scientists", "albert_einstein"],
    "biology": ["scientists"],
    "philosophy": ["philosophers", "socrates", "plato", "aristotle",
                    "confucius", "immanuel_kant", "friedrich_nietzsche",
                    "rene_descartes", "john_locke"],
    "ethics": ["immanuel_kant", "confucius", "socrates", "philosophers"],
    "politics": ["john_locke", "aristotle", "plato"],
    "art": ["leonardo_da_vinci"],
    "engineering": ["leonardo_da_vinci"],
    "relativity": ["albert_einstein", "physicists"],
}

_WORD_RE = re.compile(r"[a-zàâçéèêëîïôûùüÿñæœ]{3,}")

# Generic words excluded from corpus-overlap scoring
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "between",
    "their", "have", "has", "are", "was", "were", "not", "but", "all", "any",
    "how", "what", "when", "who", "why", "which", "his", "her", "its", "our",
    "les", "des", "une", "dan", "pour", "avec", "sur",
}


class Distributor:
    """Standalone deterministic router (usable without the message bus)."""

    def __init__(self):
        self.thinkers: Dict[str, Any] = load_all_thinkers()
        self.usage: Dict[str, int] = {name: 0 for name in self.thinkers}
        self._rotation: Dict[str, int] = {}  # topic-key -> counter

    # ------------------------------------------------------------------
    def score_thinker(self, name: str, topic: str) -> float:
        """Deterministic relevance score of one thinker for one topic."""
        t = self.thinkers[name]
        low_topic = (topic or "").lower()
        words = {w for w in _WORD_RE.findall(low_topic) if w not in _STOPWORDS}
        score = 0.0

        # 1. Explicit route table (stem-tolerant: 'chemical' matches 'chemistry')
        for key, targets in DOMAIN_ROUTES.items():
            stem = key[: max(4, len(key) - 4)]
            if name in targets and (key in low_topic or low_topic.find(stem) >= 0):
                score += 5.0

        # 2. Domain-name match (e.g. topic mentions 'chemistry')
        if t.domain.lower() in low_topic or t.domain in words:
            score += 3.0

        # 3. Entry-text keyword overlap (uses the thinker's own corpus),
        # scored by the fraction of the corpus that matched - so a thinker
        # whose whole corpus "matches" via stopwords gains nothing.
        hits = t.findings(topic or "", limit=12)
        if len(hits) < len(t.entries):
            ratio = len(hits) / max(1, len(t.entries))
            score += 2.0 * ratio + 0.3 * len(hits)

        # 4. Diversity boost for under-used thinkers (quantized so equal
        # usage tiers stay exactly tied and rotation can kick in)
        total = sum(self.usage.values()) or 1
        avg = total / len(self.usage)
        score += int(max(0.0, (avg - self.usage[name]) * 0.1) * 10) / 10.0

        return round(score, 3)

    # ------------------------------------------------------------------
    def distribute(self, topic: str, top_k: int = 3) -> Dict[str, Any]:
        """Pick the top_k thinkers/workers for a topic.

        Equal scores are rotated round-robin so consecutive calls with the
        same topic yield different orderings -> more diverse debates.
        """
        scored: List[Tuple[str, float]] = [
            (name, self.score_thinker(name, topic)) for name in self.thinkers
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

        return {
            "topic": topic,
            "selected": [name for name, _ in chosen],
            "scores": dict(chosen),
            "usage": dict(self.usage),
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
        logger.info(f"[{self.name}] Loaded {len(self.thinkers)} thinkers for routing.")
        self.is_active = True
        return True

    def execute(self, message) -> None:
        from captn.runtime.base import Message  # local import avoids cycle

        payload = getattr(message, "payload", {}) or {}
        task_id = payload.get("task_id")
        topic = str(payload.get("topic", "") or "")
        top_k = int(payload.get("top_k", 3) or 3)

        result = self.distribute(topic, top_k=top_k)
        logger.info(f"[{self.name}] '{topic}' -> {result['selected']}")

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
