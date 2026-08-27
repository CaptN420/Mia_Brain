"""Base class for deterministic corpus thinkers.

Parses corpus entries shaped like:
    1. Dmitri Mendeleev - Created the Periodic Table of Elements in 1869, ...
into structured findings. Pure text processing - no LLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

CAPTN_DIR = Path(__file__).resolve().parent.parent

_ENTRY_RE = re.compile(
    r"^\s*(\d+)\.\s*(?P<name>[A-Z][^:\-]+?)\s*[-–:]\s*(?P<detail>.+?)\s*$"
)


class CorpusThinker:
    """One thinker per knowledge corpus (deterministic, no LLM)."""

    #: override in subclasses
    corpus_file: str = ""
    domain: str = "general"

    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.available = False
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.corpus_file:
            return
        path = CAPTN_DIR / self.corpus_file
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return
        for line in text.splitlines():
            m = _ENTRY_RE.match(line)
            if m and len(m.group("detail")) >= 10:
                self.entries.append({
                    "rank": int(m.group(1)),
                    "source": m.group("name").strip(),
                    "detail": m.group("detail").strip(),
                    "domain": self.domain,
                    "rule_version": f"corpus-thinker-{self.domain}-v1",
                })
        self.available = bool(self.entries)

    # ------------------------------------------------------------------
    def findings(self, topic: str = "", limit: int = 12) -> List[Dict[str, Any]]:
        """Return entries relevant to a topic (keyword match), or all if no match."""
        low = (topic or "").lower().strip()
        if not low:
            hits = self.entries
        else:
            keywords = [w for w in re.findall(r"[a-zàâçéèêëîïôûùüÿñæœ]{3,}", low)]
            hits = [
                e for e in self.entries
                if any(k in (e["source"] + " " + e["detail"]).lower() for k in keywords)
            ]
            if not hits:
                hits = self.entries
        return hits[:limit]

    def synthesize(self, topic: str = "") -> str:
        """Compact knowledge block for injection into agent proposals."""
        fs = self.findings(topic)
        if not fs:
            return ""
        lines = [f"[{self.domain}] Apports thinkers (déterministe) :"]
        for e in fs[:6]:
            lines.append(f"- {e['source']} : {e['detail'][:140]}")
        lines.append(f"- Confiance : {len(fs) * 10}/100 ({len(fs)} entrées)")
        return "\n".join(lines)

    def status(self) -> Dict[str, Any]:
        return {
            "thinker": type(self).__name__,
            "domain": self.domain,
            "available": self.available,
            "entries": len(self.entries),
            "corpus": self.corpus_file,
        }
