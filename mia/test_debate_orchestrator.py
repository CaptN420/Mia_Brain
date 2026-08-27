from __future__ import annotations

from typing import Dict, List

from base_debate_core import BaseDebateCore


class TestDebateOrchestrator(BaseDebateCore):
    debate_kind = "test"
    turn_prefix = "test debate tour"
    sequence = ["Aurelius", "Basilide", "Chymicus", "Sentinelle", "Synthetiseur"]

    def __init__(self, cfg, payload: Dict[str, object]):
        super().__init__(cfg)
        self.payload = dict(payload or {})
        self.target_equation = str(self.payload.get("equation", "") or "").strip()
        self.object_calculated = str(self.payload.get("object_calculated", "") or "").strip()
        self.law_type = str(self.payload.get("law_type", "") or "").strip()
        self.architecture = str(self.payload.get("architecture", "") or "").strip()
        self.mechanism = str(self.payload.get("mechanism", "") or "").strip()
        self.experiment = str(self.payload.get("experiment", "") or "").strip()
        self.links = [str(x).strip() for x in (self.payload.get("links", []) or []) if str(x).strip()]
        raw_defs = self.payload.get("definitions", None)
        if raw_defs is None:
            raw_defs = self.payload.get("defs", {})
        self.defs = {
            str(k).strip(): str(v).strip()
            for k, v in dict(raw_defs or {}).items()
            if str(k).strip()
        }

    def _append_log(self, speaker: str, text: str) -> None:
        return

    def _flush_memory(self) -> None:
        return

    def _record_turn_metric(self, turn: int) -> None:
        return

    def _shared_context_block(self) -> str:
        parts: List[str] = [f"Équation cible : {self.target_equation or '-'}"]
        if self.object_calculated:
            parts.append(f"Objet calculé : {self.object_calculated}")
        if self.law_type:
            parts.append(f"Type de loi : {self.law_type}")
        if self.architecture:
            parts.append(f"Architecture : {self.architecture}")
        if self.mechanism:
            parts.append(f"Mécanisme stocké : {self.mechanism}")
        if self.experiment:
            parts.append(f"Expérience stockée : {self.experiment}")
        if self.defs:
            defs = " | ".join(f"{k}={v}" for k, v in self.defs.items())
            parts.append(f"Définitions : {defs}")
        if self.links:
            parts.append("Liens stockés : " + " | ".join(self.links[:6]))
        return "\n".join(parts)

    def _compose_prompt(self, speaker: str) -> str:
        recent = self._history_block(self.sequence, limit=8)
        common = (
            "MODE TEST-DEBATE MULTI-AGENT\n"
            "Tu ne crées pas une nouvelle session, tu ne modifies pas la mémoire, tu analyses seulement l'équation cible.\n"
            "Objectif : prouver, réfuter ou corriger minimalement l'équation cible par un mini débat scientifique.\n"
            "Interdiction : proposer une équation hors sujet, inventer une nouvelle théorie, parler de sécurité système.\n"
            "Si l'équation est trop faible, il faut le dire clairement et proposer une correction minimale testable.\n"
            "Toujours rester centré sur l'équation exacte ci-dessous.\n\n"
            f"Tour : {self.state.get('turn', 0)}\n"
            f"Agent : {speaker}\n"
            f"{self._shared_context_block()}\n\n"
            "Contexte récent :\n"
            f"{recent or '-'}\n"
        )
        if speaker == "Aurelius":
            return common + (
                "Rôle : analyse math/physique.\n"
                "Format obligatoire :\n"
                "Lecture :\n"
                "Variables clés :\n"
                "Faiblesse principale :\n"
                "Correction minimale :\n"
                "Test de calcul :\n"
            )
        if speaker == "Basilide":
            return common + (
                "Rôle : mécanisme chimique/expérimental.\n"
                "Format obligatoire :\n"
                "Mécanisme plausible :\n"
                "Variable manquante ou inutile :\n"
                "Protocole court :\n"
                "Verdict local :\n"
            )
        if speaker == "Chymicus":
            return common + (
                "Rôle : critique logique.\n"
                "Format obligatoire :\n"
                "Blocage :\n"
                "Pourquoi :\n"
                "Réparation minimale exigée :\n"
                "Verdict local :\n"
            )
        if speaker == "Sentinelle":
            return common + (
                "Rôle : validation.\n"
                "Format obligatoire :\n"
                "Unités :\n"
                "Mesurabilité :\n"
                "Cohérence :\n"
                "Verdict local :\n"
            )
        if speaker == "Synthetiseur":
            return common + (
                "Rôle : conclusion du débat.\n"
                "Format obligatoire :\n"
                "Consensus :\n"
                "Équation retenue :\n"
                "Justification :\n"
                "Décision finale : validée / à réparer / rejetée\n"
            )
        return common


    def _build_demonstration(self) -> str:
        equation = self.target_equation or "-"
        if not equation or equation == "-":
            return "- Hypothesis: equation missing\n- Result: no demonstration possible"
        rhs = equation.split("=", 1)[1].strip() if "=" in equation else equation.strip()
        example_var = next((k for k in self.defs.keys() if k != "Ndot"), "")
        if example_var:
            return (
                f"- Hypothesis: {example_var} is measurable and stable during the test\n"
                f"- Example substitution: {example_var} = 2.0\n"
                f"- Computation path: solve RHS '{rhs}' then compare with LHS\n"
                f"- Interpretation: the equation predicts the target quantity from the retained variables"
            )
        return (
            "- Hypothesis: variables are measurable\n"
            f"- Computation path: solve RHS '{rhs}'\n"
            "- Interpretation: direct proportionality or direct mapping is assumed"
        )

    def _build_counterexample(self) -> str:
        equation = self.target_equation or ""
        lowered_defs = " ".join(self.defs.values()).lower()
        if "k_eff" in equation and "s⁻¹" in lowered_defs:
            return "- If k_eff is in s^-1 while Ndot is a flow, the equation is dimensionally incomplete\n- A conversion factor or physical quantity is missing"
        if "=" not in equation:
            return "- Equation is not explicit\n- No counterfactual test can be evaluated reliably"
        if len(self.defs) < 2:
            return "- Too few defined variables to guarantee physical grounding\n- Missing definitions increase the risk of hidden ambiguity"
        return "- If one retained variable is held constant while the measured target still changes, the law is incomplete\n- The debate should then request a minimal repair"

    def _minimal_repair_proposal(self) -> str:
        equation = self.target_equation or ""
        if "Ndot = k_eff" in equation:
            return "Ndot = k_eff * A"
        if equation.startswith("J =") and "D" in equation and "ΔC" in equation and "/L" not in equation and "/ L" not in equation:
            return "J = (D * ΔC) / L"
        if equation.startswith("Ndot = J") and "A" not in equation:
            return "Ndot = J * A"
        return equation or "-"

    def _compute_score_and_verdict(self) -> tuple[int, str, str]:
        score = 0
        if self.target_equation and "=" in self.target_equation:
            score += 20
        if self.defs:
            score += min(20, len(self.defs) * 5)
        if self.links:
            score += min(20, len(self.links) * 5)
        if self.object_calculated:
            score += 10
        if self.mechanism:
            score += 10
        if self.experiment:
            score += 10
        conclusion = self._last_by("Synthetiseur").lower()
        if "rejet" in conclusion or "invalid" in conclusion:
            score = min(score, 45)
        elif "à réparer" in conclusion or "repair" in conclusion:
            score = min(max(score, 55), 78)
        else:
            score = max(score, 70)
        score = max(0, min(100, score))
        if score >= 85:
            return score, "VALID", "READY_FOR_MUTATION"
        if score >= 60:
            return score, "PARTIAL", "NEED_REPAIR"
        return score, "INVALID", "REJECT"


    def render_report(self) -> str:
        blocks: List[str] = []
        score, status, action = self._compute_score_and_verdict()

        blocks.append("=== TEST DEBATE V2 ===")
        blocks.append(f"Equation: {self.target_equation or '-'}")
        blocks.append("")
        for speaker in self.sequence:
            text = self._last_by(speaker).strip()
            blocks.append(f"[{speaker}]")
            blocks.append(text or "No output.")
            blocks.append("")

        blocks.append("[Demonstration]")
        blocks.append(self._build_demonstration())
        blocks.append("")

        blocks.append("[Counter-example]")
        blocks.append(self._build_counterexample())
        blocks.append("")

        blocks.append("[Minimal repair proposal]")
        blocks.append(self._minimal_repair_proposal())
        blocks.append("")

        blocks.append("[Final verdict]")
        blocks.append(f"Status: {status}")
        blocks.append(f"Action: {action}")
        blocks.append(f"Score: {score}/100")
        return "\n".join(blocks).strip() + "\n"
