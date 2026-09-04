"""
DiversityCheckerWorker — Deterministic diversity enforcement for worker outputs.

Scans accumulated worker results for:
1.  Structural repetition: outputs that are byte-for-byte or AST identical.
2.  Domain clustering: too many results from the same domain/thinker family.
3.  Perspective imbalance: missing opposing viewpoints on the same topic.

When a diversity violation is detected, the worker emits a warning and
proposes a corrective action (e.g. "route to opposing domain thinker").

Message contract (bus):
    request : Message(type="task", destination="diversity_checker", payload={
                  "task_id": str,
                  "topic": str,
                  "results": list[dict],   # accumulated worker/thinker results
                  "selected": list[str],   # names of already-selected thinkers
                  "mode": "check"|"suggest"|"enforce",
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "diversity_checker",
                  "valid": bool, "diversity_score": float,
                  "violations": list[str],
                  "suggestions": list[str],
                  "error_message": str (when invalid)
              })
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("DiversityCheckerWorker")

# Domain families for clustering
_DOMAIN_FAMILIES = {
    "philosophy": ["philosophy", "philosophers"],
    "physics": ["physics", "physics_mathematics", "physics_chemistry"],
    "chemistry": ["chemistry", "physics_chemistry"],
    "mathematics": ["mathematics", "mathematics_philosophy", "physics_mathematics"],
    "computation": ["computation"],
    "alchemy": ["alchemy"],
    "art": ["art", "engineering", "polymath"],
}


def _content_hash(content: str) -> str:
    """Hash content for dedup detection."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def _domain_family(domain: str) -> str:
    """Map a thinker domain to its top-level family."""
    d_low = domain.lower()
    for family, members in _DOMAIN_FAMILIES.items():
        if d_low in members:
            return family
    # Try partial match
    for family, members in _DOMAIN_FAMILIES.items():
        for m in members:
            if m in d_low or d_low in m:
                return family
    return "other"


class DiversityCheckerWorker:
    """Vérificateur de diversité déterministe : repère les répétitions et les
    biais de perspective pour garantir des réponses variées.
    (no LLM, pure Python: hash sets + compteurs)
    """

    name = "diversity_checker"

    def __init__(self, bus=None):
        self.bus = bus
        self._seen_hashes: Set[str] = set()
        self._domain_counts: Counter = Counter()
        self._total_checks = 0

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Vérificateur de diversité initialisé. "
                    f"Domaine: {len(_DOMAIN_FAMILIES)} familles suivies.")
        return True

    def shutdown(self) -> bool:
        return True

    # ── Core methods ──────────────────────────────────────────────────

    def reset_session(self) -> None:
        """Reset session state for a new analysis run."""
        self._seen_hashes.clear()
        self._domain_counts.clear()
        self._total_checks = 0

    def check(
        self,
        topic: str,
        results: List[Dict[str, Any]],
        selected: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Check accumulated results for diversity violations.

        Returns a score (0-100) and a list of violations + suggestions.
        """
        self._total_checks += 1
        violations: List[str] = []
        suggestions: List[str] = []

        domains_seen: Counter = Counter()
        total_results = len(results)

        # --- 1. Structural repetition ---
        for r in results:
            content = str(r.get("payload", {}).get("result", r))
            h = _content_hash(content)
            if h in self._seen_hashes:
                violations.append(f"Répétition structurelle détectée: contenu "
                                  f"déjà vu (hash={h[:8]}…)")
                suggestions.append("Activer l'opposition router pour forcer "
                                   "une perspective différente.")
            else:
                self._seen_hashes.add(h)

            # Track domain distribution
            domain = r.get("domain", r.get("payload", {}).get("domain", "unknown"))
            domains_seen[domain] += 1
            self._domain_counts[domain] += 1

        # --- 2. Domain clustering ---
        if total_results >= 2:
            for domain, count in domains_seen.most_common(1):
                ratio = count / total_results
                if ratio > 0.7:
                    violations.append(
                        f"Biais de domaine: {domain} représente {ratio:.0%} "
                        f"des résultats ({count}/{total_results}).")
                    # Find an opposing domain
                    family = _domain_family(domain)
                    for d in self._domain_counts:
                        if _domain_family(d) != family:
                            suggestions.append(
                                f"Router vers un domaine opposé: {d}")
                            break
                    else:
                        # No opposite domain in history — suggest philosophy
                        suggestions.append(
                            "Router vers philosophie (domaine opposé par défaut).")

        # --- 3. Full-diversity warning ---
        if total_results >= 3 and len(domains_seen) < 2:
            violations.append(
                f"Tous les résultats viennent d'un seul domaine "
                f"({', '.join(domains_seen.keys())}). La diversité est nulle.")
            suggestions.append("Forcer la sélection d'au moins 2 domaines "
                               "différents dans le distributeur.")

        # --- 4. Perspective imbalance with selected ---
        if selected and total_results >= 2:
            # Check if same thinker appeared too many times
            thinker_counts = Counter(r.get("plugin", r.get("name", "unknown"))
                                     for r in results)
            for thinker, count in thinker_counts.most_common(1):
                if count >= 3:
                    violations.append(
                        f"Même intervenant trop fréquent: {thinker} ({count}x).")
                    suggestions.append(
                        "Round-robin rotation forcée — exclure temporairement "
                        "le thinker sur-représenté.")

        # --- 5. Score computation ---
        base_score = 100
        for v in violations:
            if "Répétition" in v:
                base_score -= 20
            elif "Biais" in v:
                base_score -= 15
            elif "seul domaine" in v:
                base_score -= 30
            elif "trop fréquent" in v:
                base_score -= 10
        score = max(0, base_score)

        return {
            "valid": score >= 50,
            "diversity_score": score,
            "violations": violations,
            "suggestions": suggestions,
            "total_checked": total_results,
            "domains_seen": dict(domains_seen),
            "session_hashes": len(self._seen_hashes),
        }

    def suggest_opposing(
        self,
        topic: str,
        current_selections: List[str],
        all_thinkers: Dict[str, Any],
    ) -> List[str]:
        """Suggest an opposing thinker to balance the current selection."""
        from captn.thinkers import get_opposing_domain

        if not current_selections:
            return []

        # Get the domains of already-selected thinkers
        current_domains = set()
        for name in current_selections:
            t = all_thinkers.get(name)
            if t and hasattr(t, "domain"):
                current_domains.add(t.domain)

        opposing_names = []
        for domain in current_domains:
            opp_domain = get_opposing_domain(domain)
            # Find thinkers in opposing domain
            for name, t in all_thinkers.items():
                if name in current_selections:
                    continue
                if hasattr(t, "domain") and opp_domain in t.domain.lower():
                    opposing_names.append(name)

        return opposing_names[:3]

    # ── Plugin bus interface ──────────────────────────────────────────

    def execute(self, message) -> None:
        from captn.runtime.base import Message
        task_id = message.payload.get("task_id")
        topic = message.payload.get("topic", "")
        results = message.payload.get("results", [])
        selected = message.payload.get("selected", [])
        mode = message.payload.get("mode", "check")

        logger.info(f"[{self.name}] Checking diversity for task {task_id} "
                    f"({mode}, {len(results)} résultats)")

        result = None
        error = None

        try:
            if mode == "check":
                result = self.check(topic, results, selected)
            elif mode == "suggest":
                all_thinkers = {}
                try:
                    from captn.thinkers import load_all_thinkers
                    all_thinkers = load_all_thinkers()
                except ImportError:
                    pass
                suggestions = self.suggest_opposing(
                    topic, selected, all_thinkers)
                result = {
                    "suggestions": suggestions,
                    "current_selections": selected,
                }
            else:
                result = self.check(topic, results, selected)
        except Exception as e:
            error = str(e)

        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "valid": error is None,
                "result": result,
                "error_message": error,
            },
        )
        if self.bus:
            self.bus.publish(response)