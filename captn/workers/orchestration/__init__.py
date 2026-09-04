"""Orchestration workers — route tasks and manage workflow.

Workers
--------
distributor        : Route each task/topic to the best-suited thinkers/workers.
                     Deterministic (no LLM). Keyword scoring + round-robin
                     rotation + usage accounting for fairness.
opposition_router  : Deliberately selects thinkers with CONTRADICTORY or
                     complementary viewpoints for well-rounded answers.
                     Combines Distributor picks with opposing-domain picks.
smart_router       : Route chaque tâche au meilleur worker déterministiquement.
                     Remplace le câblage en dur des pipelines par un scoring
                     intelligent basé sur WORKER_ROUTES + description overlap.
"""