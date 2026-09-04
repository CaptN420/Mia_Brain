"""
OppositionRouter — Routes tasks to deliberately opposing thinkers for debate.

This worker intentionally selects thinkers with CONTRADICTORY or
complementary viewpoints to generate diverse, well-rounded answers.

Strategy:
1. Primary selection: best-matching thinker(s) for the topic (via
   Distributor).
2. Opposition selection: pick thinker(s) from the OPPOSITE domain
   (e.g. pick a philosopher when the primary is a physicist).
3. Combine and verify diversity via DiversityCheckerWorker.

Message contract (bus):
    request : Message(type="task", destination="opposition_router", payload={
                  "task_id": str,
                  "topic": str,
                  "top_k": int,            # total thinkers to select
                  "opposition_ratio": float,  # 0.0-1.0: how many should oppose
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "opposition_router",
                  "valid": bool, "selection": list[str],
                  "primary": list[str], "opposing": list[str],
                  "error_message": str (when invalid)
              })
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OppositionRouter")


class OppositionRouter:
    """Routeur d'opposition déterministe : choisit des thinkers aux
    perspectives opposées pour maximiser la diversité des réponses.
    """

    name = "opposition_router"

    def __init__(self, bus=None):
        self.bus = bus

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Routeur d'opposition initialisé.")
        return True

    def shutdown(self) -> bool:
        return True

    # ── Core ──────────────────────────────────────────────────────────

    def route(
        self,
        topic: str,
        top_k: int = 3,
        opposition_ratio: float = 0.5,
    ) -> Dict[str, Any]:
        """Select primary + opposing thinkers for a topic.

        Args:
            topic: The topic/question to route.
            top_k: Total number of thinkers to select.
            opposition_ratio: Fraction (0-1) that should be opposition picks.

        Returns:
            Dict with primary, opposing, and combined selection lists.
        """
        try:
            from captn.thinkers import (
                load_all_thinkers,
                get_opposing_domain,
                load_thinkers_by_domain,
            )
            from captn.workers.orchestration.distributor import Distributor
        except ImportError as e:
            return {
                "valid": False,
                "primary": [],
                "opposing": [],
                "selection": [],
                "error": f"Dependency import failed: {e}",
            }

        distributor = Distributor()
        all_thinkers = load_all_thinkers()

        if not all_thinkers:
            return {
                "valid": False,
                "primary": [],
                "opposing": [],
                "selection": [],
                "error": "No thinkers available.",
            }

        # ── 1. Primary: best-matching thinkers ──
        dist_result = distributor.distribute(topic, top_k=top_k)
        primary = dist_result["selected"]
        primary_scores = dist_result["scores"]

        # ── 2. Opposition: find thinkers from contrasting domains ──
        n_oppose = max(1, int(top_k * opposition_ratio))
        opposing = []

        # Get domains of primary selections
        primary_domains = set()
        for name in primary:
            t = all_thinkers.get(name)
            if t and hasattr(t, "domain"):
                primary_domains.add(t.domain)

        # For each primary domain, find opposing-domain thinkers not already selected
        for domain in primary_domains:
            opp_domain = get_opposing_domain(domain)
            domain_thinkers = load_thinkers_by_domain(opp_domain)
            for name in domain_thinkers:
                if name not in primary and name not in opposing:
                    opposing.append(name)
                    if len(opposing) >= n_oppose:
                        break
            if len(opposing) >= n_oppose:
                break

        # Fallback: if no opposing domain thinkers found, pick any unselected thinker
        if not opposing:
            for name in all_thinkers:
                if name not in primary:
                    opposing.append(name)
                    if len(opposing) >= n_oppose:
                        break

        # ── 3. Combine (primary first, then opposing) ──
        combined = primary + opposing

        return {
            "valid": True,
            "primary": primary,
            "primary_scores": primary_scores,
            "opposing": opposing,
            "selection": combined[:max(1, top_k + n_oppose)],
            "opposition_ratio": opposition_ratio,
            "error": None,
        }

    # ── Plugin bus interface ──────────────────────────────────────────

    def execute(self, message) -> None:
        from captn.runtime.base import Message
        task_id = message.payload.get("task_id")
        topic = message.payload.get("topic", "")
        top_k = int(message.payload.get("top_k", 3) or 3)
        opp_ratio = float(message.payload.get("opposition_ratio", 0.5) or 0.5)

        logger.info(f"[{self.name}] Routing '{topic}' with opposition "
                    f"(top_k={top_k}, ratio={opp_ratio})")

        result = None
        error = None
        try:
            result = self.route(topic, top_k=top_k, opposition_ratio=opp_ratio)
        except Exception as e:
            error = str(e)

        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "valid": error is None and (result is None or result.get("valid", True)),
                "result": result,
                "error_message": error,
            },
        )
        if self.bus:
            self.bus.publish(response)