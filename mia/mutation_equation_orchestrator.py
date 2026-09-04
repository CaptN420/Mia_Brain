from __future__ import annotations

import random
import re
from typing import Dict, List, Tuple

from equation_debate_orchestrator import EquationDebateOrchestrator
from prompt_guard import prompt_injection_guardrails, wrap_untrusted_block, sanitize_untrusted_text
from prompts import MUTATION_PROMPT, get_agent_prompt
from shared_memory import SharedResearchMemory

# Importer le pipeline déterministe CaptN (20+ mutations)
try:
    from captn.workers.transformation.diversifier_worker import (
        DiversifierWorker as _CaptnDiversifier,
        MUTATION_REGISTRY as _MUTATION_REGISTRY,
    )
    _CAPTN_AVAILABLE = True
except ImportError:
    _CAPTN_AVAILABLE = False

_PARENT_RE = re.compile(r"^\s*Parent\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_OBJECT_RE = re.compile(r"^\s*Objet calculé\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_ARCH_RE = re.compile(r"^\s*Architecture(?: choisie)?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_VAR_RE = re.compile(r"^\s*Variable(?: parent)?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

MECHANISM_LIBRARY = {
    "resistance": {
        "label": "résistance globale",
        "term": "/ (1 + R)",
        "architecture": "parent + résistance globale",
        "effect": "ajoute une limitation résistive mesurable",
        "required_symbols": ["R"],
    },
    "saturation": {
        "label": "saturation d'interface",
        "term": "* (1 - S_eff)",
        "architecture": "parent + saturation",
        "effect": "réduit le flux net quand l'interface se sature",
        "required_symbols": ["S_eff"],
    },
    "loss": {
        "label": "pertes nettes",
        "term": "- pertes",
        "architecture": "parent - pertes",
        "effect": "soustrait une perte globale observable",
        "required_symbols": ["pertes"],
    },
    "geometry": {
        "label": "contrainte géométrique",
        "term": "/ L",
        "architecture": "parent avec longueur explicite",
        "effect": "rend l'épaisseur ou la distance limitante explicite",
        "required_symbols": ["L"],
    },
    "time": {
        "label": "temps caractéristique",
        "term": "/ (1 + τ)",
        "architecture": "parent + relaxation temporelle",
        "effect": "ajoute un temps de relaxation mesurable",
        "required_symbols": ["τ"],
    },
    "coupling": {
        "label": "couplage cinétique",
        "term": "* (1 + k)",
        "architecture": "parent + couplage cinétique",
        "effect": "combine transport et cinétique dans une même loi",
        "required_symbols": ["k"],
    },
}

_SYMBOL_RE = re.compile(r"[A-Za-z_ΔτφηκμρσλΦΨχΩα-ω][A-Za-z0-9_ΔτφηκμρσλΦΨχΩα-ω]*")


class MutationEquationOrchestrator(EquationDebateOrchestrator):
    debate_kind = "mutation"
    turn_prefix = "mutation tour"

    sequence = [
        "Aurelius",
        "EquationValidator",
        "Basilide",
        "Hermes",
        "Chymicus",
        "Sentinelle",
        "Synthetiseur",
        "Archiviste",
        "Hermetica",
        "Reviseur",
        "FinalValidator",
    ]

    def __init__(self, cfg):
        super().__init__(cfg)
        self.state.setdefault("parent_equation", "")
        self.state.setdefault("parent_signature", "")
        self.state.setdefault("parent_object", "")
        self.state.setdefault("parent_architecture", "")
        self.state.setdefault("parent_variable", "")
        self.state.setdefault("parent_source", "")
        self.state.setdefault("target_mechanism", "")
        self.state.setdefault("target_mechanism_term", "")
        self.state.setdefault("target_mechanism_reason", "")

    def run(self, turns: int = 1) -> None:
        self.shared = SharedResearchMemory(self.session_dir)
        ok, reason = self.shared.can_start_mutation()
        if not ok:
            target_eq = str(getattr(self.cfg, "target_equation", "") or "").strip()
            payload = self.shared.get_equation_payload(target_eq) if target_eq else {}
            if any(tok in str(reason or "").lower() for tok in ["fallback", "placeholder"]) or bool(payload.get("fallback_used", False)):
                base_parent = target_eq or str(payload.get("equation", "") or "").strip() or self._pick_locked_equation()
                enriched = self._force_enrich_parent(base_parent)
                if enriched and enriched != base_parent:
                    print("[MUTATION] fallback detected -> forcing structural enrichment")
                    print(f"[MUTATION] parent enriched: {enriched}")
                    self._safe_set_parent_fields(
                        equation=enriched,
                        variable=str(payload.get("variable", "") or ""),
                        obj=str(payload.get("object", "") or payload.get("objet", "") or ""),
                        architecture="consolidated parent",
                        source="v1.422_consolidate",
                    )
                    super().run(turns=turns)
                    return
            print(f"[INFO] Mutation bloquée: {reason}")
            return
        super().run(turns=turns)
    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------
    def _is_blankish(self, value: str) -> bool:
        v = str(value or "").strip()
        return v in {"", "-", "(à compléter)", "(a completer)", "none", "null"}

    def _extract_first(self, pattern: re.Pattern, text: str) -> str:
        if not text:
            return ""
        m = pattern.search(text)
        return m.group(1).strip().rstrip(".") if m else ""

    def _extract_parent_line(self, text: str) -> str:
        return self._extract_first(_PARENT_RE, text)

    def _extract_object_line_local(self, text: str) -> str:
        return self._extract_first(_OBJECT_RE, text)

    def _extract_architecture_line_local(self, text: str) -> str:
        return self._extract_first(_ARCH_RE, text)

    def _extract_variable_line_local(self, text: str) -> str:
        return self._extract_first(_VAR_RE, text)

    def _safe_set_parent_fields(
        self,
        *,
        equation: str = "",
        variable: str = "",
        obj: str = "",
        architecture: str = "",
        source: str = "",
    ) -> None:
        eq = str(equation or "").strip()
        if eq and not self._is_blankish(eq):
            self.state["parent_equation"] = eq
            self.state["parent_signature"] = self._structure_signature(eq)
        if variable and not self._is_blankish(variable):
            self.state["parent_variable"] = str(variable).strip()
        if obj and not self._is_blankish(obj):
            self.state["parent_object"] = str(obj).strip()
        if architecture and not self._is_blankish(architecture):
            self.state["parent_architecture"] = str(architecture).strip()
        if source and not self._is_blankish(source):
            self.state["parent_source"] = str(source).strip()

    def _reset_parent_fields(self) -> None:
        self.state["parent_equation"] = ""
        self.state["parent_signature"] = ""
        self.state["parent_object"] = ""
        self.state["parent_architecture"] = ""
        self.state["parent_variable"] = ""
        self.state["parent_source"] = ""

    def _approved_symbol_set(self) -> set[str]:
        try:
            return set(self._get_approved_variable_symbols())
        except Exception:
            return set()

    def _normalize_mechanism(self, mechanism: str) -> str:
        key = str(mechanism or "").strip().lower()
        return key if key in MECHANISM_LIBRARY else ""

    def _pick_target_mechanism(self) -> str:
        parent = self._current_parent()
        chosen = ""
        reason = ""
        try:
            recs = self.shared.recommend_mechanisms(parent_equation=parent, limit=3)
        except Exception:
            recs = []
        if recs:
            chosen = str(recs[0].get("name", "") or "").strip().lower()
            reason = str(recs[0].get("hint", "") or recs[0].get("label", "") or "").strip()
        if not chosen:
            # Sélection ALÉATOIRE parmi les mécanismes disponibles (résout ordre fixe)
            mechanisms = list(MECHANISM_LIBRARY.keys())
            turn = int(self.state.get("turn", 1))
            random.seed(turn)  # déterministe par tour, mais change à chaque tour
            random.shuffle(mechanisms)
            chosen = mechanisms[0]
            reason = f"tour {turn} — sélection aléatoire"
        meta = MECHANISM_LIBRARY.get(chosen, {})
        self.state["target_mechanism"] = chosen
        self.state["target_mechanism_term"] = str(meta.get("term", "") or "")
        self.state["target_mechanism_reason"] = reason
        return chosen

    def _current_mechanism(self) -> str:
        current = self._normalize_mechanism(str(self.state.get("target_mechanism", "") or ""))
        if current:
            return current
        return self._pick_target_mechanism()

    def _mechanism_meta(self) -> Dict[str, object]:
        return dict(MECHANISM_LIBRARY.get(self._current_mechanism(), {}) or {})

    def _mechanism_block(self) -> str:
        name = self._current_mechanism()
        if not name:
            return ""
        meta = self._mechanism_meta()
        required = ", ".join(meta.get("required_symbols", []) or [])
        lines = [
            "MÉCANISME PHYSIQUE CIBLE :",
            f"- nom : {name}",
            f"- label : {meta.get('label', '')}",
            f"- opérateur attendu : {meta.get('term', '')}",
            f"- architecture visée : {meta.get('architecture', '')}",
            f"- effet causal attendu : {meta.get('effect', '')}",
        ]
        if required:
            lines.append(f"- symboles typiques : {required}")
        reason = str(self.state.get("target_mechanism_reason", "") or "")
        if reason:
            lines.append(f"- pourquoi ce choix : {reason}")
        lines.append("- obligation : la fille doit rendre ce mécanisme visible dans l'équation")
        return "\n".join(lines)

    def _mechanism_memory_block(self) -> str:
        parent = self._current_parent()
        try:
            snap = self.shared.build_mechanism_snapshot(parent_equation=parent, limit=4)
        except Exception:
            snap = {}
        recs = list(snap.get("recommendations", []) or [])
        events = list(snap.get("recent_events", []) or [])
        lines: List[str] = []
        if recs:
            lines.append("MÉCANISMES RECOMMANDÉS :")
            for item in recs[:4]:
                lines.append(f"- {item.get('name', '')} | score={item.get('selection_score', 0)} | {item.get('hint', item.get('label', ''))}")
        if events:
            lines.append("HISTORIQUE MÉCANISMES :")
            for item in events[:4]:
                lines.append(f"- {str(item.get('mechanism', ''))} | {str(item.get('decision', ''))}")
        return "\n".join(lines)

    def _equation_uses_mechanism(self, equation: str, mechanism: str) -> bool:
        eq = str(equation or "")
        eq_compact = eq.replace(' ', '')
        low = eq.lower()
        if mechanism == "resistance":
            return "/(1+R)" in eq_compact
        if mechanism == "saturation":
            return "(1-S_eff)" in eq_compact
        if mechanism == "loss":
            return "pertes" in low or "loss" in low
        if mechanism == "geometry":
            return "/L" in eq_compact
        if mechanism == "time":
            return "τ" in eq or "tau" in low
        if mechanism == "coupling":
            return "*(1+k)" in eq_compact
        return False

    # ---------------------------------------------------------
    # Parent selection / recovery
    # ---------------------------------------------------------
    def _approved_payload_to_parent(self, payload: Dict[str, object], source: str) -> bool:
        eq = str(payload.get("equation", "") or "").strip()
        if self._is_blankish(eq):
            return False
        approved = bool(payload.get("approved", False) or str(payload.get("status", "")).lower() == "approved")
        stable_parent = bool(payload.get("stable_parent", False))
        fallback_used = bool(payload.get("fallback_used", False))
        repair_required = bool(payload.get("repair_required", False))
        if not approved or not stable_parent or fallback_used or repair_required:
            return False
        obj = str(
            payload.get("object")
            or payload.get("objet")
            or payload.get("object_calculated")
            or payload.get("objective")
            or ""
        ).strip()
        arch = str(payload.get("architecture", "") or "").strip()
        var = str(payload.get("variable", "") or "").strip()
        self._safe_set_parent_fields(equation=eq, variable=var, obj=obj, architecture=arch, source=source)
        return True

    def _recover_parent_from_recent_texts(self) -> None:
        candidates: List[Tuple[str, str]] = []
        for source, builder in [
            ("history", lambda: self._history_block(self.sequence, limit=16)),
            ("shared", self._shared_block),
            ("memory", self._memory_block),
        ]:
            try:
                text = builder()
            except Exception:
                text = ""
            if text:
                candidates.append((source, text))

        for source, text in candidates:
            eq = self._extract_equation_line(text)
            if eq and not self._is_blankish(eq):
                self._safe_set_parent_fields(
                    equation=eq,
                    variable=self._extract_variable_line_local(text),
                    obj=self._extract_object_line_local(text),
                    architecture=self._extract_architecture_line_local(text),
                    source=source,
                )
                break

    def _pick_locked_equation(self) -> str:
        self._reset_parent_fields()

        explicit_target = str(getattr(self.cfg, "target_equation", "") or "").strip()
        if explicit_target:
            payload = self.shared.get_equation_payload(explicit_target) or {}
            if payload and self._approved_payload_to_parent(payload, "ui_target"):
                return self.state["parent_equation"]

        try:
            selected = self.shared.select_parent_equation(prefer_stable=True) or {}
        except Exception:
            selected = {}
        if selected and self._approved_payload_to_parent(selected, "natural_selection"):
            return self.state["parent_equation"]

        latest = self.shared.get_latest_stable_equation() or self.shared.get_latest_equation() or {}
        if latest and self._approved_payload_to_parent(latest, "approved_equation"):
            return self.state["parent_equation"]

        partial = self.shared.get_latest_partial_equation() or {}
        if partial and self._approved_payload_to_parent(partial, "partial_equation"):
            return self.state["parent_equation"]

        self._recover_parent_from_recent_texts()
        return str(self.state.get("parent_equation", "") or "").strip()

    def _current_parent(self) -> str:
        parent = str(self.state.get("parent_equation", "") or "").strip()
        if parent:
            return parent
        return self._pick_locked_equation()

    def _must_mutate(self) -> bool:
        return True

    # ---------------------------------------------------------
    # Parent continuity checks
    # ---------------------------------------------------------
    def _split_equation(self, equation: str) -> Tuple[str, str]:
        eq = str(equation or "").strip()
        if "=" not in eq:
            return "Ndot", eq
        lhs, rhs = eq.split("=", 1)
        return lhs.strip(), rhs.strip()

    def _core_parent_tokens(self, parent: str) -> List[str]:
        lhs, rhs = self._split_equation(parent)
        text = f"{lhs} {rhs}"
        ordered = ["J", "A", "R", "L", "S_eff", "S", "pertes", "D_eff", "ΔC", "k"]
        return [tok for tok in ordered if tok in text]

    def _child_keeps_parent_core(self, parent: str, child: str) -> bool:
        tokens = self._core_parent_tokens(parent)
        if not tokens:
            return True
        kept = sum(1 for tok in tokens if tok in child)
        return kept >= max(1, int(round(0.7 * len(tokens))))

    def _detect_variable_ghosts(self, equation: str) -> List[str]:
        approved = set(self._get_approved_variable_symbols())
        allowed = {
            "Ndot", "J", "A", "R", "L", "pertes", "loss", "losses", "D_eff", "k", "alpha", "beta",
            "gamma", "eta", "epsilon", "S", "S_eff", "ΔC", "Δt", "C", "C0", "Cf",
        }
        ghosts: List[str] = []
        for sym in _SYMBOL_RE.findall(str(equation or "")):
            clean = sym.strip()
            if not clean or clean in ghosts:
                continue
            if clean in allowed or clean in approved:
                continue
            if clean.lower() in {"exp", "e"}:
                continue
            ghosts.append(clean)
        return ghosts

    def _force_enrich_parent(self, parent: str) -> str:
        text = str(parent or "").strip()
        if self._is_blankish(text) or "=" not in text:
            return text
        lhs, rhs = self._split_equation(text)
        enriched_rhs = rhs
        compact = rhs.replace(" ", "")
        if "k_eff" in enriched_rhs and "ΔC" not in enriched_rhs:
            enriched_rhs = enriched_rhs.replace("k_eff", "(k_eff * ΔC / L)", 1)
        elif "x_eff" in enriched_rhs:
            enriched_rhs = enriched_rhs.replace("x_eff", "(D_eff * ΔC / L)", 1)
        elif all(tok not in enriched_rhs for tok in ["ΔC", "J", "D_eff"]):
            enriched_rhs = f"({enriched_rhs}) * ΔC / L"
        if "R" in enriched_rhs and "/(1+R)" not in compact and "/ (1 + R)" not in enriched_rhs:
            enriched_rhs = f"({enriched_rhs}) / (1 + R)"
        if enriched_rhs == rhs:
            return text
        return f"{lhs} = {enriched_rhs}"

    def _build_deterministic_child_from_parent(self, parent: str) -> str:
        """Génère une équation fille VARIÉE à partir du parent.

        Utilise le CaptN DiversifierWorker (20+ mutations) pour produire
        une mutation structurellement distincte à chaque tour.
        """
        lhs, rhs = self._split_equation(parent)
        if self._is_blankish(parent):
            return ""

        # Priorité CaptN : 20+ mutations
        if _CAPTN_AVAILABLE:
            try:
                dw = _CaptnDiversifier()
                mutations = dw.mutate(parent, shuffle=True)
                if mutations:
                    turn = int(self.state.get("turn", 1)) or 1
                    idx = (turn - 1) % len(mutations)
                    return mutations[idx]
            except Exception:
                pass

        # Fallback : 4 variantes (mieux que l'ancien code qui en faisait 1)
        low = rhs.lower()
        if "/(1" not in low:
            return f"{lhs} = ({rhs}) / (1 + R)"
        if "s_eff" not in low:
            return f"{lhs} = ({rhs}) * (1 - S_eff)"
        if "pertes" not in low and "loss" not in low:
            return f"{lhs} = {rhs} - pertes"
        if "beta" not in low:
            return f"{lhs} = ({rhs}) / (1 + beta)"
        if "tau" not in low:
            return f"{lhs} = ({rhs}) * exp(-t / tau)"
        return f"{lhs} = ({rhs}) * (1 - S_eff)"

    # ---------------------------------------------------------
    # Validation mutation
    # ---------------------------------------------------------
    def _equation_retry_prompt(self, eval_result: Dict[str, object]) -> str:
        parent = self._current_parent()
        reasons = "; ".join(eval_result.get("issues", [])) or "mutation insuffisante"
        if self._is_blankish(parent):
            return (
                self._compose_prompt("Aurelius")
                + "\n\nMUTATION BLOQUÉE : aucune équation parent valide n'a été récupérée.\n"
                + f"Raisons : {reasons}\n"
                + "Tu dois signaler 'Parent : absent' et ne pas inventer de parent générique."
            )
        return (
            self._compose_prompt("Aurelius")
            + "\n\nMUTATION REJETÉE PAR EquationValidator.\n"
            + f"Parent obligatoire : {parent}\n"
            + f"Variable parent : {self.state.get('parent_variable', '')}\n"
            + f"Objet calculé parent : {self.state.get('parent_object', '')}\n"
            + f"Architecture parent : {self.state.get('parent_architecture', '')}\n"
            + f"Raisons : {reasons}\n"
            + f"Tu dois proposer une équation fille distincte du parent, avec une vraie différence de structure, en injectant le mécanisme cible {self._current_mechanism()}.\n"
            + "Tu dois écrire une équation fille complète sur une ligne commençant par 'Équation :'.\n"
            + "La fille doit réutiliser explicitement la structure parent au lieu de repartir de zéro."
        )

    def _validate_aurelius_equation(self, text: str) -> Dict[str, object]:
        result = super()._validate_aurelius_equation(text)
        eq = self._extract_equation_line(text)
        parent_eq = self._current_parent()
        parent_sig = self._structure_signature(parent_eq)
        eq_sig = self._structure_signature(eq)

        issues = list(result.get("eval", {}).get("issues", []))
        if self._is_blankish(parent_eq):
            issues.append("parent absente")
        if self._is_blankish(eq):
            if "équation absente" not in issues:
                issues.append("équation absente")
        if eq_sig and parent_sig and eq_sig == parent_sig:
            issues.append("mutation absente")
        if parent_eq and eq and not self._child_keeps_parent_core(parent_eq, eq):
            issues.append("perte de structure parent")

        low = (text or "").lower()
        if "(à compléter)" in low or "\nparent :\n" in low:
            issues.append("template non rempli")

        ghosts = self._detect_variable_ghosts(eq)
        if ghosts:
            issues.append("variables fantômes: " + ", ".join(ghosts[:4]))

        target_mechanism = self._current_mechanism()
        if target_mechanism and eq and not self._equation_uses_mechanism(eq, target_mechanism):
            issues.append(f"mécanisme cible absent: {target_mechanism}")

        if eq.count("(1 -") >= 3 or eq.count("- pertes") >= 2 or eq.count("- losses") >= 2:
            issues.append("stacking de pertes")

        unique_issues = list(dict.fromkeys([issue for issue in issues if issue]))
        if unique_issues:
            result["accepted"] = False
            result["message"] = (
                "Statut : rejetée\n"
                f"Équation détectée : {eq or '-'}\n"
                f"Raisons : {'; '.join(unique_issues)}\n"
                "PISTE À EXPLORER : produire une équation fille réellement distincte du parent, complète et explicite."
            )
            result["retry_prompt"] = self._equation_retry_prompt({**result.get("eval", {}), "issues": unique_issues})
            result["eval"] = {**result.get("eval", {}), "issues": unique_issues, "equation": eq}
        return result

    # ---------------------------------------------------------
    # Deterministic fallback
    # ---------------------------------------------------------
    def _build_deterministic_fallback_equation(self) -> str:
        parent = self._current_parent()
        if self._is_blankish(parent):
            return (
                "Statut : rejetée\n"
                "Élément repris : aucun\n"
                "Défaut ancien : aucune équation parent valide disponible\n"
                "Correction : impossible de muter sans parent\n"
                "Gain : aucun\n"
                "Objet calculé : -\n"
                "Type de loi : mutation bloquée\n"
                "Architecture choisie : parent absente\n"
                "Justification : la mutation ne peut pas repartir d'un parent générique\n"
                "Parent : absent\n"
                "Équation : -\n"
                "Remarque : fournir d'abord une équation parent validée"
            )

        child = self._build_deterministic_child_from_parent(parent)
        return (
            "Statut : nouvelle\n"
            f"Élément repris : {parent}\n"
            "Défaut ancien : parent trop stable ou sortie mutation incomplète\n"
            "Correction : mutation déterministe appliquée à la parent exacte\n"
            "Gain : continuité parent -> fille sans repartir d'un squelette générique\n"
            "Objet calculé : débit net de transfert muté\n"
            "Type de loi : mutation structurée\n"
            f"Architecture choisie : {self._mechanism_meta().get('architecture', 'dérivation directe depuis la parent verrouillée')}\n"
            "Justification : l'équation fille dérive directement du parent exact et injecte le mécanisme physique cible au lieu de repartir d'une forme canonique plus ancienne\n"
            f"Parent : {parent}\n"
            f"Équation : {child}\n"
            "Définitions :\n"
            "- Ndot : débit net de transfert\n"
            "- les termes hérités conservent leur sens parent\n"
            "Mécanisme causal : la mutation ajoute un seul mécanisme correctif visible tout en conservant le noyau causal de la parent\n"
            "Liens :\n"
            "- les termes hérités conservent leur effet sur Ndot\n"
            "- le mécanisme ajouté modifie la limitation globale\n"
            "Expérience : comparer parent et fille dans le même protocole et mesurer l'amélioration prédictive\n"
            f"Remarque : fallback déterministe de mutation basé sur la parent exacte | mécanisme={self._current_mechanism()}"
        )

    # ---------------------------------------------------------
    # Prompt composition
    # ---------------------------------------------------------
    def _mutation_memory_block(self) -> str:
        try:
            recent = self.shared.get_recent_equation_mutations(limit=4)
        except Exception:
            recent = []
        if not recent:
            return ""
        lines = ["MUTATIONS RÉCENTES :"]
        for item in recent:
            parent = str(item.get("parent_equation", ""))[:80]
            child = str(item.get("mutated_equation", ""))[:80]
            decision = str(item.get("decision", ""))
            lines.append(f"- {parent} -> {child} | {decision}")
        return "\n".join(lines)

    def _equation_guard_block(self) -> str:
        parent = self._current_parent()
        if self._is_blankish(parent):
            return (
                "OBLIGATIONS STRICTES DE MUTATION :\n"
                "- aucune mutation autorisée sans équation parent valide\n"
                "- interdit d'inventer un parent générique\n"
                "- si le parent est absent, signaler explicitement le blocage"
            )
        return (
            "OBLIGATIONS STRICTES DE MUTATION :\n"
            "- partir de l'équation parent fournie par la mémoire\n"
            "- conserver le noyau de la structure parent\n"
            "- produire une mutation structurelle visible\n"
            "- interdiction de recopier l'équation parent à l'identique\n"
            "- interdiction d'un simple renommage de symboles\n"
            "- interdiction d'écrire '(à compléter)' ou '-'\n"
            "- interdiction d'une réponse sans équation explicite\n"
            "- garder un lien causal testable avec les variables validées\n"
            f"- variable parent : {self.state.get('parent_variable', '')}\n"
            f"- objet parent : {self.state.get('parent_object', '')}\n"
            f"- architecture parent : {self.state.get('parent_architecture', '')}\n"
            f"- parent : {parent}"
        )

    def _repair_memory_block(self) -> str:
        try:
            block = self.shared.build_repair_memory_block(limit=4)
        except Exception:
            block = ""
        if not block:
            return ""
        return "MÉMOIRE DES RÉPARATIONS\n" + block

    def _parent_block(self) -> str:
        parent = self._current_parent()
        if self._is_blankish(parent):
            return (
                "PARENT INDISPONIBLE :\n"
                "- aucune équation parent valide n'a été récupérée\n"
                "- tu dois signaler l'absence de parent et ne pas inventer de parent générique"
            )
        return (
            "ÉQUATION PARENT VERROUILLÉE POUR CE TOUR :\n"
            f"- source : {self.state.get('parent_source', '')}\n"
            f"- variable parent : {self.state.get('parent_variable', '')}\n"
            f"- objet parent : {self.state.get('parent_object', '')}\n"
            f"- architecture parent : {self.state.get('parent_architecture', '')}\n"
            f"- parent : {parent}"
        )

    def _register_mechanism_outcome(self, equation_text: str, decision: str = "") -> None:
        mechanism = self._current_mechanism()
        if not mechanism:
            return
        eq = self._extract_equation_line(equation_text)
        if not eq:
            return
        try:
            self.shared.record_mechanism_event(
                mechanism,
                parent_equation=self._current_parent(),
                child_equation=eq,
                decision=decision or "observed",
                source_turn=int(self.state.get("turn", 0) or 0),
            )
        except Exception:
            return

    def _remember_final_equation(self, text: str) -> None:
        try:
            super()._remember_final_equation(text)
        finally:
            self._register_mechanism_outcome(text, decision="accepted")

    def _remember_partial_equation(self, text: str, summary: str = "") -> None:
        try:
            super()._remember_partial_equation(text, summary=summary)
        finally:
            self._register_mechanism_outcome(text, decision="partial")

    def _compose_prompt(self, speaker: str) -> str:
        if speaker == "EquationValidator":
            return "EquationValidator est un garde déterministe de mutation, pas un agent modèle."

        parent = self._current_parent()
        mutation_prompt = MUTATION_PROMPT.format(
            parent_variable=self.state.get("parent_variable", ""),
            parent_equation=parent or "absent",
            parent_object=self.state.get("parent_object", ""),
            parent_architecture=self.state.get("parent_architecture", ""),
        )

        base = [
            get_agent_prompt(speaker, "mutation").strip(),
            mutation_prompt,
            f"Thème : {self.theme}",
            f"Tour : {self.state.get('turn', 0)}",
            f"Agent : {speaker}",
            self._approved_variables_block(),
            self._equation_guard_block(),
            self._parent_block(),
            self._shared_block(),
            self._memory_block(),
            self._mutation_memory_block(),
            self._repair_memory_block(),
            self._mechanism_block(),
            self._mechanism_memory_block(),
            self._stagnation_block(),
            self._score_guidance_block(),
            self._fusion_guidance_block(),
            "Contexte récent :",
            self._history_block(self.sequence, limit=8),
            "Objectif : muter une équation parent déjà existante en une équation fille plus forte, sans repartir de zéro.",
            "Diversité : si plusieurs agents convergent déjà vers la même forme, produire une variante structurellement différente.",
            "Rappel : une bonne mutation change la structure mathématique ou le mécanisme, pas juste les noms.",
            "Interdiction absolue : ne jamais écrire '(à compléter)' ou '-'.",
        ]

        if speaker == "Aurelius":
            extra = (
                "Rôle : proposer l'équation fille principale.\n"
                f"Parent : {parent or 'absent'}\n"
                f"Variable parent : {self.state.get('parent_variable', '')}\n"
                f"Objet calculé parent : {self.state.get('parent_object', '')}\n"
                f"Architecture parent : {self.state.get('parent_architecture', '')}\n"
                f"Mécanisme cible : {self._current_mechanism()}\n"
                "Format obligatoire :\n"
                "Objet calculé :\n"
                "Type de loi :\n"
                "Architecture choisie :\n"
                "Delta structurel :\n"
                "Justification :\n"
                "Équation :"
            )
        elif speaker == "Basilide":
            extra = (
                "Rôle : enrichir chimiquement la mutation.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Définitions :\n- ...\n- ...\n"
                "Mécanisme causal :\n"
                "Gain prévu :\n"
                "Liens :\n- ...\n- ..."
            )
        elif speaker == "Hermes":
            extra = (
                "Rôle : injecter une micro-mutation utile.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Variable ou terme ajouté :\n"
                f"Mécanisme physique choisi : {self._current_mechanism()}\n"
                "Pourquoi c'est une vraie mutation :\n"
                "Testabilité :"
            )
        elif speaker == "Chymicus":
            extra = (
                "Rôle : critique de mutation.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Diagnostic :\n"
                "Défaut 1 :\n"
                "Défaut 2 :\n"
                "Correction minimale :\n"
                "Remplacement équation : aucun\n"
                "Verdict :"
            )
        elif speaker == "Sentinelle":
            extra = (
                "Rôle : vérifier si la fille est mesurable et distincte du parent.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Check-list :\n"
                "- distinction parent/fille :\n"
                "- unités :\n"
                "- mesurabilité :\n"
                "- cohérence :\n"
                "Verdict structurel :"
            )
        elif speaker == "Synthetiseur":
            extra = (
                "Rôle : produire l'équation fille finale.\n"
                "Format obligatoire :\n"
                "Statut : reprise / réparée / nouvelle\n"
                "Élément repris :\n"
                "Défaut ancien :\n"
                "Correction :\n"
                "Gain :\n"
                "Objet calculé :\n"
                "Type de loi :\n"
                "Architecture choisie :\n"
                "Mécanisme injecté :\n"
                "Justification :\n"
                "Équation :\n"
                "Définitions :\n- ...\n- ...\n"
                "Mécanisme causal :\n"
                "Liens :\n- ...\n- ...\n"
                "Expérience :\n"
                "Remarque :"
            )
        elif speaker == "Archiviste":
            extra = (
                "Rôle : décider du statut mémoriel de la mutation.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Statut : replace / keep_as_branch / reject\n"
                "Décision mémoire :\n"
                "Pourquoi :\n"
                "À reprendre :\n"
                "À vérifier plus tard :"
            )
        else:
            extra = (
                "Rôle : verdict final de mutation.\n"
                "Format obligatoire :\n"
                "Validation :\n"
                "Point fort :\n"
                "Point faible :\n"
                "Test conseillé :\n"
                "Décision : replace / keep_as_branch / reject"
            )

        base.append(extra)
        return "\n\n".join(part for part in base if part)

    # ---------------------------------------------------------
    # Decision parsing / finalization
    # ---------------------------------------------------------
    def _parse_mutation_decision(self, text: str) -> str:
        low = (text or "").lower()
        if "keep_as_branch" in low or "branch" in low or "branche" in low:
            return "keep_as_branch"
        if "replace" in low or "remplace" in low:
            return "replace"
        if "reject" in low or "rejette" in low or "rejet" in low:
            return "reject"
        return "keep_as_branch"

    def _finalize_turn(self, turn: int) -> None:
        parent = self._current_parent()
        try:
            parent_score = self.shared.get_equation_score(parent)
        except Exception:
            parent_score = 0

        super()._finalize_turn(turn)

        latest = self.shared.get_latest_equation() or self.shared.get_latest_partial_equation() or {}
        child = str(latest.get("equation", "")).strip()
        try:
            child_score = self.shared.get_equation_score(child)
        except Exception:
            child_score = 0

        decision_text = self._last_by("FinalValidator") or self._last_by("Archiviste")
        decision = self._parse_mutation_decision(decision_text)
        self.shared.add_equation_mutation(
            parent_equation=parent,
            mutated_equation=child,
            decision=decision,
            turn=turn,
            score_delta=child_score - parent_score,
            note=(decision_text or "")[:300],
            parent_source=self.state.get("parent_source", ""),
        )
        self.shared.add_debate_summary(
            "mutation",
            turn,
            f"parent={parent} | child={child} | decision={decision} | score_delta={child_score - parent_score}",
        )
        self._reset_parent_fields()
