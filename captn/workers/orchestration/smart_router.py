"""SmartRouter — route chaque tâche au meilleur worker déterministiquement.

Remplace le câblage en dur des pipelines par un scoring intelligent :

1. Table de routage explicite (WORKER_ROUTES) — mots-clés → workers
2. Score par recouvrement de description
3. Boost des workers sous-utilisés (fairness)
4. Mode opposition optionnel pour diversifier les exécutions

Utilisation:
    router = SmartRouter()
    result = router.route("generate python code for fibonacci")
    # -> {"selected": ["deterministic_coder"], "scores": {...}, ...}
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SmartRouter")

# ──────────────────────────────────────────────────────────────
# WORKER_ROUTES — table de routage mot-clé → workers
# ──────────────────────────────────────────────────────────────
WORKER_ROUTES: Dict[str, List[str]] = {
    # ── Code generation ────────────────────────────────────
    "code": ["deterministic_coder", "polyglot_coder", "autogen", "code_backend_worker"],
    "python": ["deterministic_coder", "autogen", "code_backend_worker"],
    "generate": ["deterministic_coder", "polyglot_coder", "autogen"],
    "generation": ["deterministic_coder", "polyglot_coder", "autogen"],
    "transform": ["deterministic_coder", "normalizer", "translator"],
    "ast": ["deterministic_coder", "validator"],
    "scaffold": ["polyglot_coder", "fix_generator"],
    "template": ["polyglot_coder", "fix_generator"],
    "function": ["deterministic_coder", "autogen"],
    # ── Debug / Fix ────────────────────────────────────────
    "debug": ["fix_generator", "pgm_worker", "code_backend_worker"],
    "fix": ["fix_generator", "pgm_worker", "code_backend_worker"],
    "repair": ["fix_generator", "pgm_worker"],
    "bug": ["bug_worker", "pgm_worker", "fix_generator"],
    "error": ["bug_worker", "fix_generator", "validator"],
    "patch": ["fix_generator", "pgm_worker", "validator"],
    "problem": ["pgm_worker", "fix_generator"],
    # ── Data ingestion ─────────────────────────────────────
    "crawl": ["crawler", "raw2json", "raw2json_worker", "extractor"],
    "crawler": ["crawler", "raw2json"],
    "ingest": ["raw2json", "crawler", "extractor"],
    "dataset": ["raw2json", "raw2json_worker", "extractor"],
    "jsonl": ["raw2json", "raw2json_worker"],
    "github": ["crawler", "raw2json"],
    "collect": ["crawler", "raw2json"],
    # ── Analyse / Lint ─────────────────────────────────────
    "syntax": ["syntax_worker", "validator"],
    "lint": ["syntax_worker", "validator"],
    "analyse": ["syntax_worker", "bug_worker", "validator"],
    "analysis": ["syntax_worker", "bug_worker", "pgm_worker"],
    "inspect": ["syntax_worker", "bug_worker"],
    "scan": ["syntax_worker", "bug_worker", "crawler"],
    "validate": ["validator", "math_validation_worker", "chemistry_validation_worker"],
    "quality": ["validator", "diversity_checker_worker", "no_repetition_worker"],
    # ── Diversity / Dedup ──────────────────────────────────
    "diversity": ["diversifier_worker", "no_repetition_worker", "diversity_checker_worker"],
    "diversify": ["diversifier_worker", "no_repetition_worker"],
    "dedup": ["no_repetition_worker", "diversity_checker_worker"],
    "repetition": ["no_repetition_worker", "diversity_checker_worker"],
    "unique": ["no_repetition_worker", "diversifier_worker"],
    # ── Math ───────────────────────────────────────────────
    "math": ["math_validation_worker", "diversifier_worker", "validator"],
    "equation": ["math_validation_worker", "diversifier_worker"],
    "calculus": ["math_validation_worker"],
    "algebra": ["math_validation_worker", "diversifier_worker"],
    "numeric": ["math_validation_worker", "deterministic_coder"],
    # ── Chemistry / Alchemy ────────────────────────────────
    "chemistry": ["chemistry_validation_worker", "diversifier_worker"],
    "chemical": ["chemistry_validation_worker"],
    "alchemy": ["chemistry_validation_worker", "diversifier_worker"],
    "alchimie": ["chemistry_validation_worker", "diversifier_worker"],
    "molecule": ["chemistry_validation_worker"],
    "reaction": ["chemistry_validation_worker"],
    # ── Translation / Normalisation ────────────────────────
    "translate": ["translator", "normalizer"],
    "translation": ["translator", "normalizer"],
    "normalize": ["normalizer", "translator"],
    "normalise": ["normalizer", "translator"],
    "language": ["translator", "polyglot_coder"],
    # ── Pipeline / Orchestration ────────────────────────────
    "pipeline": ["distributor", "opposition_router"],
    "orchestrate": ["distributor", "opposition_router"],
    "route": ["distributor", "opposition_router"],
    "distribute": ["distributor"],
    "opposition": ["opposition_router", "distributor"],
    "debate": ["opposition_router", "distributor"],
    # ── Général / Fallback ─────────────────────────────────
    "generate": ["deterministic_coder", "polyglot_coder", "autogen"],
    "create": ["deterministic_coder", "polyglot_coder", "autogen"],
    "write": ["deterministic_coder", "polyglot_coder", "fix_generator"],
    "read": ["crawler", "raw2json", "extractor"],
    "parse": ["extractor", "normalizer", "raw2json"],
    "convert": ["raw2json", "translator", "normalizer"],
    "mirror": ["diversifier_worker", "math_validation_worker"],
    "reflect": ["diversifier_worker", "opposition_router"],
    # ── Domaine / Classification ────────────────────────────
    "domain": ["domain_classifier_worker", "diversity_checker_worker"],
    "classify": ["domain_classifier_worker", "math_validation_worker"],
    "classification": ["domain_classifier_worker", "math_validation_worker"],
    "architecture": ["domain_classifier_worker", "diversity_checker_worker"],
    "equation.*domain": ["domain_classifier_worker", "math_validation_worker"],
    "discipline": ["domain_classifier_worker", "distributor"],
    # ── Mathématiques anciennes ─────────────────────────────
    "pythagore": ["math_validation_worker", "domain_classifier_worker"],
    "euclide": ["math_validation_worker"],
    "archimede": ["math_validation_worker"],
    "egyptien": ["math_validation_worker"],
    "babylonien": ["math_validation_worker"],
    "fibonacci": ["math_validation_worker", "deterministic_coder"],
    "phi": ["math_validation_worker", "domain_classifier_worker"],
    "nombre d'or": ["math_validation_worker", "domain_classifier_worker"],
}

# Capacités déclaratives de chaque worker (description + domaines)
WORKER_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "deterministic_coder": {
        "tags": ["code", "python", "ast", "transform", "generation"],
        "domain": "coding",
        "description": "AST transforms: docstrings, type hints, normalisation",
    },
    "polyglot_coder": {
        "tags": ["code", "multi-lang", "scaffold", "template"],
        "domain": "coding",
        "description": "Generate code scaffolds in any language (Go, Rust, JS, etc.)",
    },
    "autogen": {
        "tags": ["code", "python", "generation", "llm-fallback"],
        "domain": "coding",
        "description": "Autogen loop with LLM fallback for code generation",
    },
    "code_backend_worker": {
        "tags": ["code", "python", "backend", "execution"],
        "domain": "coding",
        "description": "Sandboxed code execution and validation backend",
    },
    "fix_generator": {
        "tags": ["fix", "repair", "patch", "scaffold", "template"],
        "domain": "fixing",
        "description": "Generate fix templates from problem graph models (PGM)",
    },
    "pgm_worker": {
        "tags": ["problem", "fix", "debug", "analysis", "graph"],
        "domain": "fixing",
        "description": "Problem graph modelling for debug/fix workflows",
    },
    "bug_worker": {
        "tags": ["bug", "error", "scan", "analysis", "inspect"],
        "domain": "analysis",
        "description": "Static bug detection (eval/exec, security patterns)",
    },
    "syntax_worker": {
        "tags": ["syntax", "lint", "analysis", "inspect", "scan"],
        "domain": "analysis",
        "description": "Syntax validation and file integrity checks",
    },
    "crawler": {
        "tags": ["crawl", "github", "collect", "ingest", "read"],
        "domain": "acquisition",
        "description": "GitHub open-source code crawler (networked ingestion)",
    },
    "raw2json": {
        "tags": ["ingest", "dataset", "jsonl", "convert", "parse"],
        "domain": "acquisition",
        "description": "Convert raw source directories to JSONL datasets",
    },
    "raw2json_worker": {
        "tags": ["ingest", "dataset", "jsonl", "convert", "raw2json"],
        "domain": "acquisition",
        "description": "Bus wrapper for raw2json conversion",
    },
    "extractor": {
        "tags": ["extract", "parse", "read", "ingest", "data"],
        "domain": "acquisition",
        "description": "Parse and extract structured data from sources",
    },
    "validator": {
        "tags": ["validate", "quality", "syntax", "scan", "lint"],
        "domain": "quality",
        "description": "Final validation rules and quality gates",
    },
    "diversity_checker_worker": {
        "tags": ["diversity", "quality", "dedup", "unique"],
        "domain": "quality",
        "description": "Check and enforce diversity across outputs",
    },
    "no_repetition_worker": {
        "tags": ["repetition", "dedup", "diversity", "unique", "filter",
                 "structure", "signature", "registry", "persistent"],
        "domain": "quality",
        "pipeline_priority": 3,  # filtre APRÈS génération, AVANT classification
        "description": "Filtre les équations répétées par signature structurelle fine, registre persistant cross-cycle",
    },
    "domain_classifier_worker": {
        "tags": ["domain", "classify", "classification", "equation", "architecture",
                 "physics", "chemistry", "alchemy", "biology", "mathematics",
                 "ancient", "modern", "diversity"],
        "domain": "quality",
        "pipeline_priority": 4,  # classification APRÈS filtrage
        "description": "Analyse l'architecture des équations pour déterminer leur domaine (physique, chimie, alchimie, math ancienne/modern, biologie)",
    },
    "diversifier_worker": {
        "tags": ["diversity", "diversify", "math", "equation", "mirror"],
        "domain": "transformation",
        "description": "Diversify outputs via mathematical/structural transforms",
    },
    "math_validation_worker": {
        "tags": ["math", "equation", "calculus", "algebra", "numeric", "validate",
                 "pythagore", "euclide", "archimede", "egyptian", "babylonian",
                 "ancient", "modern", "prime", "fibonacci", "combinatorics"],
        "domain": "transformation",
        "description": "Expert en mathématiques anciennes ET modernes — Pythagore, Euclide, Archimède, Égypte, Babylone, Chine, calcul, algèbre, combinatoire, nombres premiers",
    },
    "chemistry_validation_worker": {
        "tags": ["chemistry", "chemical", "alchemy", "molecule", "reaction"],
        "domain": "transformation",
        "description": "Chemistry and alchemy validation rules",
    },
    "normalizer": {
        "tags": ["normalize", "normalise", "translate", "parse", "convert"],
        "domain": "transformation",
        "description": "Data normalisation and formatting",
    },
    "translator": {
        "tags": ["translate", "translation", "language", "normalize"],
        "domain": "transformation",
        "description": "Language translation and cross-lingual transformation",
    },
    "distributor": {
        "tags": ["route", "distribute", "pipeline", "orchestrate"],
        "domain": "coordination",
        "description": "Route tasks to best-suited thinkers by keyword scoring",
    },
    "opposition_router": {
        "tags": ["opposition", "debate", "route", "distribute", "pipeline"],
        "domain": "coordination",
        "description": "Route to deliberately opposing thinkers for debate",
    },
}

# Domaines contrastés pour le mode opposition
OPPOSITION_DOMAINS: Dict[str, str] = {
    "coding": "quality",
    "quality": "coding",
    "acquisition": "transformation",
    "transformation": "acquisition",
    "fixing": "analysis",
    "analysis": "fixing",
    "coordination": "acquisition",
}

_WORD_RE = re.compile(r"[a-zàâçéèêëîïôûùüÿñæœ]{3,}")
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "between",
    "their", "have", "has", "are", "was", "were", "not", "but", "all", "any",
    "how", "what", "when", "who", "why", "which", "his", "her", "its", "our",
    "les", "des", "une", "dan", "pour", "avec", "sur", "they", "them", "can",
    "will", "would", "could", "should", "may", "might", "about", "than",
    "task", "need", "want", "please", "just", "also",
}


class SmartRouter:
    """Routeur déterministe de workers par description de tâche.

    Choisit le(s) meilleur(s) worker(s) pour une tâche donnée en combinant :
    - Table de routage explicite (WORKER_ROUTES)
    - Recouvrement de mots-clés description/capacités
    - Fairness d'usage (workers sous-utilisés boostés)
    - Mode opposition optionnel (domaine contrasté)
    - Intent Parser (NL → mode/domain structuré pour meilleur scoring)
    - Fragment Registry (déterministe, 0 token)
    """

    def __init__(self):
        self.usage: Dict[str, int] = {name: 0 for name in WORKER_CAPABILITIES}
        self._rotation: Dict[str, int] = {}  # topic-key -> counter
        self._last_opposition: Dict[str, str] = {}  # topic -> last primary domain
        self._intent_parser_available = False
        self._fragment_registry_available = False
        self._init_extensions()

    def _init_extensions(self) -> None:
        """Try to load optional extensions (Intent Parser, Fragment Registry)."""
        try:
            from captn.runtime.intent_parser import parse_intent
            self._parse_intent = parse_intent
            self._intent_parser_available = True
        except Exception:
            self._parse_intent = None
        try:
            from captn.runtime.fragment_registry import registry as _freg
            self._fragment_registry = _freg
            self._fragment_registry_available = True
        except Exception:
            self._fragment_registry = None

    # ──────────────────────────────────────────────────────────
    def score_worker(self, name: str, description: str) -> float:
        """Score déterministe d'un worker pour une description de tâche."""
        cap = WORKER_CAPABILITIES.get(name)
        if not cap:
            return 0.0

        low_desc = (description or "").lower()
        words = {w for w in _WORD_RE.findall(low_desc) if w not in _STOPWORDS}
        score = 0.0

        # 1. Route table (match de mots-clés)
        for key, targets in WORKER_ROUTES.items():
            if name in targets:
                # Match exact
                if key in low_desc:
                    score += 5.0
                # Match de racine (stem: 'generat' pour 'generate'/'generation')
                elif len(key) >= 4 and key[:4] in low_desc:
                    score += 3.0

        # Bonus pour routes multiples correspondant
        route_matches = sum(
            1 for key, targets in WORKER_ROUTES.items()
            if name in targets and (key in low_desc or (len(key) >= 4 and key[:4] in low_desc))
        )
        score += min(2.0, route_matches * 0.3)

        # 2. Tags du worker dans la description
        tag_hits = sum(1 for tag in cap.get("tags", []) if tag in words or tag in low_desc)
        score += tag_hits * 1.5

        # 3. Recouvrement avec la description du worker
        desc_words = {w for w in _WORD_RE.findall(cap.get("description", "").lower())
                      if w not in _STOPWORDS}
        overlap = len(words & desc_words)
        score += overlap * 0.8

        # 4. Boost de fairness (workers sous-utilisés)
        total = sum(self.usage.values()) or 1
        avg = total / max(1, len(self.usage))
        underuse = max(0.0, (avg - self.usage[name]) * 0.1)
        score += int(underuse * 10) / 10.0  # quantifié

        return round(score, 3)

    # ──────────────────────────────────────────────────────────
    def route(
        self,
        description: str,
        top_k: int = 1,
        opposition_mode: bool = False,
        exclude: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Sélectionne les top_k workers pour une description de tâche.

        Args:
            description: Description de la tâche (ex. "generate python fibonacci")
            top_k: Nombre de workers à sélectionner (défaut: 1)
            opposition_mode: Si True, ajoute un worker d'un domaine contrasté
            exclude: Workers à exclure

        Retourne:
            Dict avec selected, scores, usage, intent, fragments, etc.
        """
        # ── Intent Parser en amont (NL → mode/domain structuré) ──
        intent = None
        if self._intent_parser_available:
            try:
                intent = self._parse_intent(description)
            except Exception:
                pass

        exclude_set: set[str] = set(exclude) if exclude else set()
        scored: List[Tuple[str, float]] = [
            (name, self.score_worker(name, description))
            for name in WORKER_CAPABILITIES
            if name not in exclude_set
        ]

        # Boost workers whose domain matches the parsed intent domain
        if intent and intent.domain != "general":
            for i, (name, score) in enumerate(scored):
                cap = WORKER_CAPABILITIES.get(name, {})
                if cap.get("domain") == intent.domain:
                    scored[i] = (name, score + 2.0)
                # Also boost by mode
                if intent.mode == "BUILD" and cap.get("domain") in ("coding", "transformation"):
                    scored[i] = (name, scored[i][1] + 1.0)
                elif intent.mode == "DEBUG" and cap.get("domain") in ("fixing", "analysis"):
                    scored[i] = (name, scored[i][1] + 1.0)
                elif intent.mode == "ANALYZE" and cap.get("domain") == "analysis":
                    scored[i] = (name, scored[i][1] + 1.0)
                elif intent.mode == "MIRROR" and cap.get("domain") in ("transformation", "quality"):
                    scored[i] = (name, scored[i][1] + 1.0)

        # Garder ceux avec score > 0
        scored = [(n, s) for n, s in scored if s > 0]

        # Si aucun score > 0, prendre les workers génériques avec un petit score
        if not scored:
            scored = [
                (name, 0.1)
                for name in WORKER_CAPABILITIES
                if name not in exclude_set
            ]

        # Trier par score descendant
        scored.sort(key=lambda x: -x[1])

        # Rotation round-robin dans les groupes à égalité
        key_words = _WORD_RE.findall((description or "").lower())
        rot_key = "|".join(sorted(set(key_words))) or "_"
        offset = self._rotation.get(rot_key, 0)
        self._rotation[rot_key] = offset + 1

        # Grouper par score égal
        groups: List[List[Tuple[str, float]]] = []
        for item in scored:
            if groups and abs(groups[-1][-1][1] - item[1]) < 1e-9:
                groups[-1].append(item)
            else:
                groups.append([item])

        flat: List[Tuple[str, float]] = []
        for group in groups:
            n = len(group)
            rotated = [group[(offset + i) % n] for i in range(n)]
            flat.extend(rotated)

        chosen = flat[:max(1, top_k)]
        for name, _ in chosen:
            self.usage[name] += 1

        # ── Mode opposition ──
        opposing = []
        if opposition_mode and chosen:
            primary_domain = WORKER_CAPABILITIES.get(chosen[0][0], {}).get("domain", "")
            opp_domain = OPPOSITION_DOMAINS.get(primary_domain)

            if opp_domain:
                for name, cap in WORKER_CAPABILITIES.items():
                    if (cap.get("domain") == opp_domain
                            and name not in {n for n, _ in chosen}
                            and name not in exclude_set):
                        opp_score = self.score_worker(name, description)
                        opposing.append((name, round(opp_score + 0.01, 3)))
                        if len(opposing) >= max(1, top_k):
                            break

                self._last_opposition[description] = opp_domain

        combined = chosen + opposing
        final_selection = combined[:max(top_k, len(chosen) + len(opposing))]

        return {
            "description": description,
            "selected": [name for name, _ in final_selection],
            "scores": dict(final_selection),
            "usage": dict(self.usage),
            "opposition_mode": opposition_mode,
            "opposition_count": len(opposing),
            "timestamp": time.time(),
            # Intent Parser enrichment
            "intent": {
                "mode": intent.mode if intent else "CHAT",
                "domain": intent.domain if intent else "general",
                "confidence": intent.confidence if intent else 0.0,
            } if intent else None,
            # Fragment Registry recommendations
            "fragments": self._route_fragments(description) if self._fragment_registry_available else [],
        }

    # ──────────────────────────────────────────────────────────
    def _route_fragments(self, description: str, top_k: int = 5) -> list[dict]:
        """Route a description to matching fragments from the Fragment Registry.

        Returns a list of {name, input_type, output_type, description, score}.
        """
        if not self._fragment_registry_available or not self._fragment_registry:
            return []
        try:
            fragments = self._fragment_registry.route(description, top_k=top_k)
            return [
                {"name": f.name, "input_type": f.input_type,
                 "output_type": f.output_type, "description": f.description,
                 "tags": f.tags}
                for f in fragments
            ]
        except Exception:
            return []

    # ──────────────────────────────────────────────────────────
    def resolve_pipeline_steps(self, description: str, top_k: int = 2) -> List[str]:
        """Génère une séquence de steps de pipeline à partir d'une description.

        Prend les top_k workers les mieux notés et les ordonne par domaine
        (acquisition → analysis → coding → quality → coordination).

        Utile pour construire dynamiquement un Pipeline dans Captn.
        """
        result = self.route(description, top_k=top_k)
        selected = result["selected"]

        # Ordre de précédence des domaines
        DOMAIN_ORDER = {
            "acquisition": 0,
            "analysis": 1,
            "coding": 2,
            "fixing": 2,
            "transformation": 3,
            "quality": 4,
            "coordination": 5,
        }

        # Trier par ordre de domaine, puis par pipeline_priority au sein du même domaine
        def sort_key(name: str) -> tuple:
            cap = WORKER_CAPABILITIES.get(name, {})
            domain_order = DOMAIN_ORDER.get(cap.get("domain", ""), 99)
            priority = cap.get("pipeline_priority", 99)  # défaut: à la fin
            return (domain_order, priority)

        ordered = sorted(selected, key=sort_key)
        return ordered

    # ──────────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        """État actuel du routeur (workers + métriques d'usage)."""
        return {
            "workers_loaded": sorted(WORKER_CAPABILITIES.keys()),
            "routes": len(WORKER_ROUTES),
            "usage": dict(self.usage),
        }


class SmartRouterWorker(SmartRouter):
    """Wrapper bus pour le SmartRouter (interface Plugin Captn)."""

    def __init__(self, bus=None):
        SmartRouter.__init__(self)
        self.bus = bus
        self.is_active = False
        self.name = "smart_router"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Routeur intelligent initialisé : "
                    f"{len(WORKER_CAPABILITIES)} workers, {len(WORKER_ROUTES)} routes.")
        self.is_active = True
        return True

    def execute(self, message) -> None:
        from captn.runtime.base import Message

        payload = getattr(message, "payload", {}) or {}
        task_id = payload.get("task_id")
        description = str(payload.get("description", payload.get("topic", "")) or "")
        top_k = int(payload.get("top_k", 1) or 1)
        opposition = bool(payload.get("opposition_mode", False))

        result = self.route(description, top_k=top_k, opposition_mode=opposition)

        logger.info(f"[{self.name}] '{description[:60]}' -> {result['selected']}")

        if self.bus is not None:
            self.bus.publish(Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "valid": True,
                    "routing": result,
                },
            ))

    def shutdown(self) -> bool:
        self.is_active = False
        return True