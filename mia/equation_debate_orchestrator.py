from __future__ import annotations

import re
from typing import Dict, List

from base_debate_core import BaseDebateCore
from prompt_guard import prompt_injection_guardrails, wrap_untrusted_block, sanitize_untrusted_text
from prompts import get_agent_prompt
from shared_memory import SharedEquation


_EQ_LINE_RE = re.compile(r"^\s*(?:Équation|Equation)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_REPLACE_EQ_RE = re.compile(
    r"(?:remplacement équation|equation de remplacement|remplacer l['’]équation par)\s*:\s*(.+)",
    re.IGNORECASE,
)


class EquationDebateOrchestrator(BaseDebateCore):
    debate_kind = "equation"
    turn_prefix = "equation tour"

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

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------
    def _extract_equation_line(self, text: str) -> str:
        if not text:
            return ""
        m = _EQ_LINE_RE.search(text)
        if m:
            return m.group(1).strip().rstrip(".")
        return BaseDebateCore._extract_equation_line(self, text).strip().rstrip(".")

    def _normalized_equation(self, text: str) -> str:
        eq = self._extract_equation_line(text)
        return re.sub(r"\s+", "", eq).lower()

    # ------------------------------------------------------------------
    # Memory / guards
    # ------------------------------------------------------------------

    def _structure_signature(self, equation: str) -> str:
        eq = (equation or "").lower().strip()
        eq = eq.replace("é", "e")
        eq = re.sub(r"\b[a-zA-Z_Δτφηκμρσλ][a-zA-Z0-9_Δτφηκμρσλ]*\b", "X", eq)
        eq = re.sub(r"\b\d+(?:\.\d+)?\b", "N", eq)
        eq = re.sub(r"\s+", "", eq)
        eq = re.sub(r"X+", "X", eq)
        return eq

    def _known_equation_signatures(self) -> List[str]:
        signatures = list(self.state.get("equation_signatures", []))
        try:
            latest = self.shared.get_latest_equation()
            if latest and latest.get("equation"):
                signatures.append(self._structure_signature(str(latest["equation"])))
        except Exception:
            pass
        try:
            partial = self.shared.get_latest_partial_equation()
            if partial and partial.get("equation"):
                signatures.append(self._structure_signature(str(partial["equation"])))
        except Exception:
            pass
        locked = self.state.get("locked_equation", "")
        if locked:
            signatures.append(self._structure_signature(locked))
        return [s for s in signatures if s]

    def _signature_seen(self, equation: str) -> bool:
        sig = self._structure_signature(equation)
        if not sig:
            return False
        limit = int(getattr(self.cfg, "equation_signature_soft_limit", 2))
        return self._known_equation_signatures().count(sig) >= limit

    def _pick_distinct_equation(self, candidates: List[str], banned_signatures: List[str]) -> str:
        banned = {s for s in banned_signatures if s}
        for eq in candidates:
            sig = self._structure_signature(eq)
            if sig and sig not in banned:
                return eq

        if not candidates:
            cycle = self._cycle_variable_symbol()
            return f"Ndot = {cycle}" if cycle else "Ndot = J"
        turn = int(self.state.get("turn", 0) or 0)
        idx = turn % len(candidates)
        return candidates[idx]

    def _fallback_equation_candidates(self) -> List[str]:
        approved = self._core_equation_symbols()
        cycle = self._cycle_variable_symbol()
        candidates: List[str] = []

        def add(expr: str) -> None:
            expr = (expr or "").strip()
            if not expr:
                return
            if cycle and cycle not in expr:
                return
            if expr not in candidates:
                candidates.append(expr)

        if cycle:
            add(f"Ndot = {cycle}")
        if cycle and "A" in approved:
            add(f"Ndot = {cycle} * A")
        if cycle and "J" in approved:
            add(f"Ndot = J * {cycle}")
        if cycle and "R" in approved:
            add(f"Ndot = {cycle} / (1 + R)")
        if cycle and "L" in approved:
            add(f"Ndot = {cycle} / L")
        if cycle and "A" in approved and "R" in approved:
            add(f"Ndot = ({cycle} * A) / (1 + R)")
        if cycle and "A" in approved and "L" in approved:
            add(f"Ndot = ({cycle} * A) / L")

        if not candidates:
            if cycle:
                candidates = [f"Ndot = {cycle}"]
            else:
                candidates = ["Ndot = J"]
        return candidates

    def _get_approved_variable_symbols(self) -> List[str]:
        try:
            approved = self.shared.get_approved_variables() or {}
            return list(approved.keys())
        except Exception:
            return []
    def _core_equation_symbols(self) -> List[str]:
        symbols = list(self._get_approved_variable_symbols())
        cycle = self._cycle_variable_symbol()
        if cycle and cycle not in symbols:
            symbols.append(cycle)
        return [s for s in symbols if s]


    def _approved_variables_block(self) -> str:
        symbols = self._get_approved_variable_symbols()
        if not symbols:
            return "VARIABLES VALIDÉES DISPONIBLES :\n- aucune"
        return "VARIABLES VALIDÉES DISPONIBLES :\n- " + ", ".join(symbols)

    def _selection_variables_block(self) -> str:
        try:
            ranked = self.shared.get_best_variables(limit=5, approved_only=True)
        except Exception:
            ranked = []
        if not ranked:
            return "SÉLECTION NATURELLE VARIABLES :\n- aucune"
        lines = ["SÉLECTION NATURELLE VARIABLES :"]
        for row in ranked:
            name = str(row.get("name", row.get("symbol", ""))).strip()
            family = str(row.get("family", "")).strip()
            score = row.get("selection_score", 0)
            unit = str(row.get("unit", "")).strip()
            if not name:
                continue
            lines.append(f"- {name} | score={score} | famille={family or '-'} | unité={unit or '-'}")
        return "\n".join(lines)

    def _cycle_variable_payload(self) -> Dict[str, str]:
        try:
            payload = self.shared.get_last_validated_variable() or {}
        except Exception:
            payload = {}
        return {k: str(v) for k, v in payload.items() if v not in (None, "")}

    def _cycle_variable_symbol(self) -> str:
        return str(self._cycle_variable_payload().get("symbol", "")).strip()

    def _cycle_variable_block(self) -> str:
        payload = self._cycle_variable_payload()
        symbol = str(payload.get("symbol", "")).strip()
        if not symbol:
            return ""
        definition = str(payload.get("definition", "")).strip()
        unit = str(payload.get("unit", "")).strip()
        role = str(payload.get("role", "")).strip()
        return (
            "VARIABLE DU CYCLE (OBLIGATOIRE) :\n"
            f"- symbole : {symbol}\n"
            f"- définition : {definition or '-'}\n"
            f"- unité : {unit or '-'}\n"
            f"- rôle causal : {role or '-'}\n"
            "OBLIGATION : l'équation doit contenir explicitement cette variable.\n"
            "Si elle est absente, l'équation est invalide.\n"
        )

    def _equation_uses_cycle_variable(self, equation: str) -> bool:
        symbol = self._cycle_variable_symbol()
        if not symbol:
            return True
        return symbol.lower() in (equation or "").lower()

    def _extract_equation_symbols(self, equation: str) -> List[str]:
        eq = equation or ""
        raw = re.findall(r"[A-Za-zΑ-Ωα-ω_][A-Za-z0-9Α-Ωα-ω_]*", eq)
        banned = {"Ndot", "t", "dt", "d", "x"}
        out = []
        for sym in raw:
            if sym not in out and sym not in banned:
                out.append(sym)
        return out

    def _unknown_symbols(self, equation: str) -> List[str]:
        approved = set(self._core_equation_symbols())
        allowed_extra = {"Ndot"}
        out = []
        for sym in self._extract_equation_symbols(equation):
            if sym not in approved and sym not in allowed_extra and sym not in out:
                out.append(sym)
        return out

    def _equation_guard_block(self) -> str:
        return (
            "OBLIGATIONS STRICTES :\n"
            "- L'équation doit utiliser AU MOINS une variable validée si la mémoire en contient\n"
            "- Interdiction d'ignorer la mémoire\n"
            "- Interdiction d'introduire une loi sans lien direct avec les variables validées\n"
            "- Interdit : Darcy-Weisbach, Reynolds, friction factor, lois hydrauliques génériques\n"
            "- Interdit : liens markdown, URL, références web dans la section Liens\n"
            "- Les liens doivent être causaux et textuels uniquement\n"
            "- Une ligne 'Liens' valide ressemble à : D augmente Ndot ; L diminue Ndot ; C_eff contrôle Ndot\n"
            "- Si le dernier tour a déjà produit la même forme, diverger par limitation, saturation, résistance ou géométrie"
        )

    def _equation_uses_memory(self, equation: str) -> bool:
        approved_vars = self._get_approved_variable_symbols()
        if not approved_vars:
            return True
        eq_low = equation.lower()
        return any(v.lower() in eq_low for v in approved_vars)

    def _is_causal_link(self, line: str) -> bool:
        bad_tokens = ["http", "www", "[", "]", "url", "URL-INTERDITE"]
        if any(tok.lower() in line.lower() for tok in bad_tokens):
            return False
        causal_verbs = [
            "augmente", "diminue", "controle", "contrôle", "relie",
            "accelere", "accélère", "ralentit", "module", "influence",
            "convertit", "limite", "freine", "alimente",
        ]
        return any(v in line.lower() for v in causal_verbs)

    def _sanitize_equation(self, equation: str) -> str:
        eq = (equation or "").strip()
        if "=" not in eq:
            return self._pick_distinct_equation(
                self._fallback_equation_candidates(),
                self._known_equation_signatures(),
            )
        forbidden_tokens = ["darcy", "weisbach", "reynolds", "friction", "friction factor"]
        if any(tok in eq.lower() for tok in forbidden_tokens):
            return self._pick_distinct_equation(
                self._fallback_equation_candidates(),
                self._known_equation_signatures(),
            )
        if not self._equation_uses_memory(eq):
            return self._pick_distinct_equation(
                self._fallback_equation_candidates(),
                self._known_equation_signatures(),
            )
        if self._signature_seen(eq):
            return self._pick_distinct_equation(
                self._fallback_equation_candidates(),
                self._known_equation_signatures(),
            )
        return eq

    def _equation_retry_prompt(self, eval_result: Dict[str, object]) -> str:
        issues = "; ".join(eval_result.get("issues", [])) or "équation insuffisante"
        approved = self._get_approved_variable_symbols()
        approved_text = ", ".join(approved) if approved else "aucune"
        return (
            self._compose_prompt("Aurelius")
            + "\n\nÉQUATION REJETÉE PAR EquationValidator.\n"
            + f"Raisons : {issues}\n"
            + f"Variables validées à utiliser : {approved_text}\n"
            + f"Variable du cycle obligatoire : {self._cycle_variable_symbol() or '-'}\n"
            + "PISTE À EXPLORER : rester sur diffusion, transport, limitation, saturation ou couplage local-global.\n"
            + "Évite les signatures déjà vues, même si tu changes juste un symbole ou un coefficient.\n"
            + "INTERDICTIONS : Darcy-Weisbach, Reynolds, friction factor, liens markdown/URL.\n"
            + "Propose une NOUVELLE équation explicite avec '=' et au moins deux liens causaux textuels."
        )

    def _validate_aurelius_equation(self, text: str) -> Dict[str, object]:
        eval_result = self._evaluate_equation_structure(text)
        eq = self._extract_equation_line(text)
        sanitized = self._sanitize_equation(eq)
        issues = list(eval_result.get("issues", []))

        if sanitized != eq:
            if "équation hors mémoire" not in issues and not self._equation_uses_memory(eq):
                issues.append("équation hors mémoire")
            low = (eq or "").lower()
            if any(tok in low for tok in ["darcy", "weisbach", "reynolds", "friction"]):
                issues.append("loi interdite")
            if "=" not in (eq or ""):
                issues.append("équation absente")
            if self._signature_seen(eq):
                issues.append("signature déjà vue")

        raw_links = self._extract_links(text)
        links = [l for l in raw_links if self._is_causal_link(l)]
        if len(links) < 2:
            if "moins de 2 liens" not in issues:
                issues.append("moins de 2 liens")
        if eq and not self._equation_uses_cycle_variable(eq):
            if "variable du cycle absente" not in issues:
                issues.append("variable du cycle absente")
        unknown = self._unknown_symbols(eq or "")
        if unknown:
            issues.append("variables non validées: " + ", ".join(unknown))

        # Captn MathValidator deterministic classification (bridge)
        if eq and self.captn_bridge is not None and self.captn_bridge.math_validator is not None:
            try:
                mres = self.captn_bridge.math_classify(eq)
                cls = str(mres.get("classification", ""))
                if cls == "CONTRADICTORY":
                    issues.append("équation contradictoire (MathValidator)")
            except Exception:
                pass

        accepted = bool(eq) and (sanitized == eq) and (len(links) >= 2) and not self._signature_seen(eq) and self._equation_uses_cycle_variable(eq) and not unknown

        if accepted:
            return {
                "accepted": True,
                "message": (
                    "Statut : acceptée\n"
                    f"Équation détectée : {eq}\n"
                    "Raison : équation exploitable, ancrée en mémoire et avec liens causaux valides"
                ),
                "retry_prompt": "",
                "eval": {**eval_result, "issues": issues, "equation": eq},
            }

        return {
            "accepted": False,
            "message": (
                "Statut : rejetée\n"
                f"Équation détectée : {eq or '-'}\n"
                f"Raisons : {'; '.join(dict.fromkeys(issues))}\n"
                "PISTE À EXPLORER : utiliser les variables validées, expliciter le transport, la limitation ou la saturation."
            ),
            "retry_prompt": self._equation_retry_prompt({**eval_result, "issues": issues}),
            "eval": {**eval_result, "issues": issues, "equation": eq},
        }

    # ------------------------------------------------------------------
    # Locking and repetition
    # ------------------------------------------------------------------
    def _pick_locked_equation(self) -> str:
        aurelius = self._last_by("Aurelius")
        eq = self._extract_equation_line(aurelius)
        if eq:
            return eq
        partial = self.shared.get_latest_partial_equation()
        if partial and partial.get("equation"):
            return str(partial["equation"]).strip()
        latest = self.shared.get_latest_equation()
        if latest and latest.get("equation"):
            return str(latest["equation"]).strip()
        return ""

    def _get_locked_equation(self) -> str:
        locked = self.state.get("locked_equation", "")
        if locked:
            return locked
        locked = self._pick_locked_equation()
        self.state["locked_equation"] = locked
        return locked

    def _replacement_requested(self) -> str:
        critique = self._last_by("Chymicus")
        if not critique:
            return ""
        m = _REPLACE_EQ_RE.search(critique)
        if not m:
            return ""
        return m.group(1).strip().rstrip(".")

    def _repeat_count(self) -> int:
        return int(self.state.get("equation_repeat_count", 0))

    def _must_mutate(self) -> bool:
        return self._repeat_count() >= 1

    def _mutation_menu(self) -> str:
        return (
            "Choisis UNE mutation visible parmi :\n"
            "- expliciter J par une loi de diffusion\n"
            "- remplacer J par k * ΔC\n"
            "- ajouter un terme de perte\n"
            "- ajouter une résistance au dénominateur\n"
            "- ajouter une saturation\n"
            "- proposer une architecture bilan ou couplage\n"
            "Exemples autorisés :\n"
            "- Ndot = A * D * (C0 - Cf) / L\n"
            "- Ndot = k * A * ΔC\n"
            "- Ndot = (J * A) / (1 + R)\n"
            ""
        )

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------
    def _stagnation_block(self) -> str:
        try:
            info = self.shared.detect_stagnation("equation")
        except Exception:
            return ""
        if not info.get("stagnant"):
            return ""
        return (
            "MODE STAGNATION ÉQUATION\n"
            "- les dernières équations sont trop proches ou trop faibles\n"
            "- obligation : changer la structure mathématique ou le mécanisme causal\n"
            "- ne pas faire juste un renommage de symboles\n"
        )

    def _fusion_guidance_block(self) -> str:
        try:
            fusion = self.shared.suggest_fusion()
        except Exception:
            fusion = None
        if not fusion:
            return ""
        return "FUSION INTELLIGENTE CONSEILLÉE\n- " + fusion

    def _score_guidance_block(self) -> str:
        try:
            recent = self.shared.recent_metrics(kind="equation", limit=3)
        except Exception:
            recent = []
        if not recent:
            return ""
        return (
            "PRESSION ÉVOLUTIVE\n- scores récents : "
            + " | ".join(f"{m.get('element','?')}={m.get('score',0)}" for m in recent)
            + "\n- améliorer la testabilité, les liens causaux et l'usage de la mémoire"
        )

    def _compose_prompt(self, speaker: str) -> str:
        if speaker == "EquationValidator":
            return "EquationValidator est un garde déterministe, pas un agent modèle."

        recent = self._history_block(self.sequence, limit=8)
        shared = self._shared_block()
        memory = self._memory_block()

        locked_eq = self._get_locked_equation()
        lock_block = ""
        if locked_eq:
            lock_block = (
                "ÉQUATION VERROUILLÉE POUR CE TOUR :\n"
                f"- équation cible : {locked_eq}\n"
                "- interdiction de changer l'objet calculé sans justification\n"
                "- interdiction de remplacer l'architecture complète\n"
                "- seul Chymicus peut demander un remplacement explicite\n"
                "- sans remplacement explicite, toute nouvelle équation principale est invalide"
            )

        mutation_block = ""
        if self._must_mutate():
            mutation_block = (
                "MUTATION OBLIGATOIRE : la même structure a été répétée trop souvent.\n"
                "Le tour en cours doit produire un changement visible dans l'équation finale.\n"
                f"{self._mutation_menu()}"
            )

        base = [
            get_agent_prompt(speaker, "equation").strip(),
            f"Thème : {self.theme}",
            f"Tour : {self.state.get('turn', 0)}",
            f"Agent : {speaker}",
            self._approved_variables_block(),
            self._selection_variables_block(),
            self._cycle_variable_block(),
            self._equation_guard_block(),
            shared,
            memory,
            lock_block,
            mutation_block,
            self._stagnation_block(),
            self._score_guidance_block(),
            self._fusion_guidance_block(),
            "Contexte récent :",
            recent,
            "Objectif : raffiner UNE équation-cible et la réparer sans dériver vers une autre loi.",
            "Rappel important : J = flux surfacique ; Ndot = débit total ; A convertit souvent J en Ndot ; ne pas confondre les unités.",
            "Interdiction absolue : ne jamais écrire '(à compléter)' ou '-'.",
        ]

        if speaker == "Aurelius":
            extra = (
                "Rôle : architecte physique.\n"
                "Tu choisis l'objet calculé, la structure de loi et l'équation de base.\n"
                "Tu dois utiliser les variables validées si elles existent.\n"
                "Si mutation obligatoire, tu n'as pas le droit de reproposer la même équation visible.\n"
                "Format obligatoire :\n"
                "Objet calculé :\n"
                "Type de loi :\n"
                "Architecture choisie :\n"
                "Justification :\n"
                "Équation :"
            )
        elif speaker == "Basilide":
            extra = (
                "Rôle : enrichisseur chimique.\n"
                "Travaille uniquement sur l'équation verrouillée.\n"
                "Utilise les variables validées si elles existent.\n"
                "Si mutation obligatoire, tu dois développer le mécanisme moteur ou la limitation.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Définitions :\n"
                "- ...\n"
                "- ...\n"
                "Mécanisme causal :\n"
                "Expérience :\n"
                "Liens :\n"
                "- ...\n"
                "- ..."
            )
        elif speaker == "Hermes":
            extra = (
                "Rôle : injecteur local.\n"
                "Tu dois ajouter au maximum un seul terme utile OU une seule variable utile.\n"
                "Tu dois rester cohérent avec les variables validées.\n"
                "Tu n'as pas le droit d'ajouter seulement une phrase vague.\n"
                "Si mutation obligatoire, ton ajout doit être structurellement visible dans l'équation finale.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Variable ou terme ajouté :\n"
                "Gain :\n"
                "Testabilité :"
            )
        elif speaker == "Chymicus":
            extra = (
                "Rôle : réparateur critique.\n"
                "Tu ne changes pas l'équation par défaut.\n"
                "Si le même défaut revient, tu dois imposer une correction structurelle explicite.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Diagnostic :\n"
                "Défaut 1 :\n"
                "Défaut 2 :\n"
                "Défaut 3 :\n"
                "Correction minimale :\n"
                "Remplacement équation : aucun\n"
                "Verdict :\n"
                "Si et seulement si l'équation verrouillée est irrécupérable, écris : Remplacement équation : <nouvelle équation>"
            )
        elif speaker == "Sentinelle":
            extra = (
                "Rôle : validateur dimensionnel / mesurabilité.\n"
                "Travaille uniquement sur l'équation verrouillée.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Check-list :\n"
                "- unités :\n"
                "- mesurabilité :\n"
                "- cohérence :\n"
                "- objet calculé :\n"
                "Verdict structurel :"
            )
        elif speaker == "Synthetiseur":
            extra = (
                "Rôle : équation finale.\n"
                "Tu synthétises l'équation verrouillée, sans en inventer une autre.\n"
                "Tu dois utiliser les variables validées si elles existent.\n"
                "Si mutation obligatoire ou si un nouveau terme a été demandé, l'équation finale doit changer visiblement.\n"
                "Une synthèse identique est invalide quand mutation obligatoire est active.\n"
                "Format obligatoire :\n"
                "Statut : reprise / réparée / nouvelle\n"
                "Élément repris :\n"
                "Défaut ancien :\n"
                "Correction :\n"
                "Gain :\n"
                "Objet calculé :\n"
                "Type de loi :\n"
                "Architecture choisie :\n"
                "Justification :\n"
                "Équation :\n"
                "Définitions :\n"
                "- ...\n"
                "- ...\n"
                "Mécanisme causal :\n"
                "Liens :\n"
                "- ...\n"
                "- ...\n"
                "Expérience :\n"
                "Remarque :"
            )
        elif speaker == "Archiviste":
            extra = (
                "Rôle : mémoire équation.\n"
                "Tu ne changes jamais l'équation verrouillée.\n"
                "RÈGLE CRITIQUE : si l'équation courante est un fallback, un secours, une reprise déterministe ou une réparation temporaire, tu n'as PAS le droit de la déclarer stable ni parent final.\n"
                "Dans ce cas, le statut doit rester : partielle ou à réparer.\n"
                "La décision mémoire doit mentionner explicitement : secours temporaire, non promu comme parent stable.\n"
                "Format obligatoire :\n"
                "Équation :\n"
                "Statut : approuvée / partielle / à réparer / à fusionner plus tard\n"
                "Décision mémoire :\n"
                "Pourquoi :\n"
                "À reprendre :\n"
                "À vérifier plus tard :"
            )
        else:
            extra = (
                "Rôle : verdict final.\n"
                "Travaille uniquement sur l'équation verrouillée.\n"
                "Si mutation obligatoire était active et que l'équation finale reste identique, la décision doit être : à réparer.\n"
                "Format obligatoire :\n"
                "Validation :\n"
                "Point fort :\n"
                "Point faible :\n"
                "Test conseillé :\n"
                "Décision : approuvée / partielle / à réparer / à fusionner plus tard"
            )

        base.append(extra)
        return "\n\n".join(part for part in base if part)

    # ------------------------------------------------------------------
    # EquationValidator deterministic gate
    # ------------------------------------------------------------------
    def _evaluate_agent_output(self, speaker: str, text: str) -> Dict[str, object]:
        if speaker == "EquationValidator":
            return {"issues": [], "score": 0, "is_valid": True}
        return self._evaluate_equation_structure(text)

    def _build_deterministic_fallback_equation(self) -> str:
        candidates = self._fallback_equation_candidates()
        banned = self._known_equation_signatures()
        eq = self._pick_distinct_equation(candidates, banned)
        return (
            "Statut : nouvelle\n"
            "Élément repris : variables validées disponibles\n"
            "Défaut ancien : équation rejetée ou répétée\n"
            "Correction : fallback déterministe distinct\n"
            "Gain : structure exploitable pour le reste du débat\n"
            "Objet calculé : débit net de transfert\n"
            "Type de loi : transport\n"
            "Architecture choisie : relation explicite entre gradient, surface et limitation\n"
            "Justification : proposition de secours construite à partir des variables validées et d'une signature non déjà vue\n"
            f"Équation : {eq}\n"
            "Définitions :\n"
            "- Ndot : débit net de transfert\n"
            "- A : surface d'échange active\n"
            "Mécanisme causal : le débit augmente avec la force motrice et diminue avec les limitations internes\n"
            "Liens :\n"
            "- A augmente Ndot\n"
            "- une limitation augmente et diminue Ndot\n"
            "Expérience : suivi du flux ou du débit net dans une cellule instrumentée\n"
            "Remarque : fallback déterministe pour éviter une boucle de répétition"
        )

    def _run_agent_turn(self, speaker: str) -> None:
        # Deterministic gates: validators never call the LLM.
        if speaker in {"Hermetica", "Reviseur"}:
            result = self._run_hermetic_gate(speaker)
            status = "OK" if result.get("accepted") else "FLAG"
            print(f"{speaker}: {result['message']}")
            print(f"[AGENT {status}] {speaker} (deterministic gate, no LLM)")
            self.debate_state.add(speaker, result["message"], max_history_messages=self.cfg.max_history_messages)
            self._append_log(speaker, result["message"])
            return
        if speaker == "FinalValidator":
            candidate = (
                self._last_by("Synthetiseur")
                or self._last_by("Basilide")
                or self._last_by("Aurelius")
                or ""
            )
            eq_line = self._extract_equation_line(candidate)
            issues: List[str] = []
            if not eq_line or "=" not in eq_line:
                issues.append("équation absente")
            else:
                eval_result = self._evaluate_equation_structure(candidate)
                issues = list(eval_result.get("issues", []))
            status = "OK" if not issues else "FLAG"
            message = (
                f"Statut : {'acceptée' if not issues else 'rejetée'}\n"
                f"Équation détectée : {eq_line or 'aucune'}\n"
                f"Raisons : {'; '.join(issues) if issues else 'aucune'}"
            )
            print(f"FinalValidator (deterministic gate, no LLM): {message.splitlines()[0]}")
            print(f"[AGENT {status}] FinalValidator issues={'; '.join(issues)}")
            self.debate_state.add("FinalValidator", message, max_history_messages=self.cfg.max_history_messages)
            self._append_log("FinalValidator", message)
            return
        if speaker != "EquationValidator":
            return super()._run_agent_turn(speaker)

        aurelius_output = self._last_by("Aurelius")
        result = self._validate_aurelius_equation(aurelius_output)
        print(f"EquationValidator: {result['message']}")
        self.debate_state.add("EquationValidator", result["message"], max_history_messages=self.cfg.max_history_messages)
        self._append_log("EquationValidator", result["message"])

        if result["accepted"]:
            return

        if not result.get("accepted"):
            # Deterministic-first: only retry with the LLM when explicitly enabled.
            if not getattr(self.cfg, "llm_enabled", False):
                print("EquationValidator: rejet, fallback déterministe activé (LLM désactivé)")
                fallback_text = self._build_deterministic_fallback_equation()
                fallback_result = self._validate_aurelius_equation(fallback_text)
                print(f"Aurelius: {fallback_text}")
                self.debate_state.add("Aurelius", fallback_text, max_history_messages=self.cfg.max_history_messages)
                self._append_log("Aurelius", fallback_text)
                self.debate_state.add("EquationValidator", fallback_result["message"], max_history_messages=self.cfg.max_history_messages)
                self._append_log("EquationValidator", fallback_result["message"])
                return
            model = self._model_for("Aurelius")
            num_predict = min(self._num_predict_for("Aurelius") + 35, 260)
            temperature = self._temperature_for("Aurelius")
            print("[RETRY] Aurelius relancé avec piste")
        retry_text = self.client.ask(
            model=model,
            messages=[{"role": "user", "content": result["retry_prompt"]}],
            temperature=temperature,
            max_tokens=num_predict,
        )
        retry_result = self._validate_aurelius_equation(retry_text)
        retry_eval = retry_result.get("eval", {})
        status = "OK" if retry_result.get("accepted") else "FLAG"
        print(
            f"[AGENT {status}] Aurelius score={retry_eval.get('score', 0)} "
            f"predict={num_predict} issues={'; '.join(retry_eval.get('issues', []))}"
        )

        if not retry_result.get("accepted"):
            print("EquationValidator: deuxième rejet, fallback déterministe activé")
            fallback_text = self._build_deterministic_fallback_equation()
            fallback_result = self._validate_aurelius_equation(fallback_text)
            print(f"Aurelius: {fallback_text}")
            self.debate_state.add("Aurelius", fallback_text, max_history_messages=self.cfg.max_history_messages)
            self._append_log("Aurelius", fallback_text)
            self.debate_state.add("EquationValidator", fallback_result["message"], max_history_messages=self.cfg.max_history_messages)
            self._append_log("EquationValidator", fallback_result["message"])
            return

        print(f"Aurelius: {retry_text}")
        self.debate_state.add("Aurelius", retry_text, max_history_messages=self.cfg.max_history_messages)
        self._append_log("Aurelius", retry_text)

    def _text_marks_fallback(self, text: str) -> bool:
        low = (text or "").lower()
        return any(token in low for token in ["fallback", "secours", "reprise déterministe", "reprise deterministe"])

    def _equation_is_provisional_fallback(self, final_eq: str, *texts: str) -> bool:
        eq_norm = re.sub(r"\s+", "", final_eq or "").lower()
        if not eq_norm:
            return False
        for text in texts:
            if not text:
                continue
            if self._text_marks_fallback(text):
                text_eq = self._extract_equation_line(text)
                text_norm = re.sub(r"\s+", "", text_eq or "").lower()
                if not text_norm or text_norm == eq_norm:
                    return True
        return False


    def _extract_link_symbols(self, links: List[str]) -> List[str]:
        out: List[str] = []
        for line in links or []:
            for sym in self._extract_equation_symbols(line):
                if sym not in out:
                    out.append(sym)
        return out

    def _missing_link_symbols(self, equation: str, links: List[str]) -> List[str]:
        eq_symbols = [s for s in self._extract_equation_symbols(equation) if s != "Ndot"]
        link_symbols = set(self._extract_link_symbols(links))
        return [s for s in eq_symbols if s not in link_symbols]

    def _detect_link_contradictions(self, links: List[str]) -> List[str]:
        pairs: Dict[tuple[str, str], set[str]] = {}
        for raw in links or []:
            line = str(raw or "").strip()
            low = line.lower()
            direction = None
            if " augmente " in f" {low} ":
                direction = "augmente"
            elif " diminue " in f" {low} ":
                direction = "diminue"
            elif " limite " in f" {low} ":
                direction = "diminue"
            elif " freine " in f" {low} ":
                direction = "diminue"
            if not direction:
                continue
            syms = self._extract_equation_symbols(line)
            if len(syms) < 2:
                continue
            src, dst = syms[0], syms[-1]
            key = (src, dst)
            pairs.setdefault(key, set()).add(direction)
        contradictions: List[str] = []
        for (src, dst), dirs in pairs.items():
            if len(dirs) > 1:
                contradictions.append(f"{src}->{dst}")
        return contradictions

    def _infer_repair_issue(self, issues: List[str], approved: bool, repair_required: bool, fallback_used: bool) -> str:
        ordered = [str(x).strip() for x in (issues or []) if str(x).strip()]
        if ordered:
            return ordered[0]
        if fallback_used:
            return "fallback provisoire"
        if repair_required:
            return "équation à réparer"
        if approved:
            return "équation consolidée"
        return "équation partielle"

    def _infer_repair_fix(self, final_eq: str, original_eq: str, links: List[str], missing_link_symbols: List[str], contradictions: List[str]) -> str:
        fixes: List[str] = []
        if final_eq and original_eq and re.sub(r"\s+", "", final_eq) != re.sub(r"\s+", "", original_eq):
            fixes.append("réécriture de l'équation")
        if len(list(links or [])) >= 2:
            fixes.append("ajout ou consolidation des liens causaux")
        if missing_link_symbols:
            fixes.append("couverture des symboles dans les liens")
        if contradictions:
            fixes.append("suppression des contradictions causales")
        if not fixes and final_eq:
            fixes.append("stabilisation structurelle")
        return ", ".join(dict.fromkeys(fixes))

    def _infer_repair_pattern(self, issue: str, fix_applied: str, original_eq: str, fixed_eq: str) -> str:
        low_issue = (issue or "").lower()
        low_fix = (fix_applied or "").lower()
        if "moins de 2 liens" in low_issue or "lien" in low_fix:
            return "équation sans liens suffisants -> ajouter des liens causaux directionnels"
        if "variable du cycle absente" in low_issue:
            return "variable du cycle absente -> réinjecter la variable du cycle dans l'équation"
        if "variables absentes des liens" in low_issue:
            return "variables principales absentes des liens -> couvrir tous les symboles clés dans les liens"
        if "contradiction" in low_issue or "contradiction" in low_fix:
            return "contradictions causales -> garder un seul sens causal par paire source/cible"
        if "variables non validées" in low_issue:
            return "symboles non validés -> remplacer par des variables validées ou définir clairement les symboles"
        if original_eq and fixed_eq and original_eq != fixed_eq:
            return "équation partielle -> réécriture minimale exploitable"
        return "réparation minimale de structure et de causalité"

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------
    def _update_repeat_counter(self, final_eq: str) -> int:
        current_sig = self._structure_signature(final_eq)
        last_sig = self._structure_signature(str(self.state.get("last_equation", "") or ""))
        if current_sig and current_sig == last_sig:
            new_count = int(self.state.get("equation_repeat_count", 0)) + 1
        else:
            new_count = 0
        self.state["equation_repeat_count"] = new_count
        self.state["last_equation"] = final_eq or ""
        return new_count

    def _finalize_turn(self, turn: int) -> None:
        locked_eq = self._get_locked_equation()
        original_locked = locked_eq
        must_mutate = self._must_mutate()

        replacement = self._replacement_requested()
        if replacement and replacement.lower() != "aucun":
            locked_eq = replacement
            self.state["locked_equation"] = replacement

        synth = self._last_by("Synthetiseur")
        archivist = self._last_by("Archiviste")
        final_validator = self._last_by("FinalValidator")
        hermes = self._last_by("Hermes") or ""
        chymicus = self._last_by("Chymicus") or ""

        candidate_text = synth or self._last_by("Basilide") or self._last_by("Aurelius") or ""
        banned_signatures = self._known_equation_signatures()

        if must_mutate:
            parent_norm = re.sub(r"\s+", "", (original_locked or locked_eq or "")).lower()
            mutation_candidates = [
                self._last_by("Synthetiseur") or "",
                self._last_by("Basilide") or "",
                self._last_by("Aurelius") or "",
            ]
            chosen_text = ""
            for txt in mutation_candidates:
                eq_try = self._extract_equation_line(txt)
                eq_norm = re.sub(r"\s+", "", eq_try or "").lower()
                if eq_try and eq_norm and eq_norm != parent_norm:
                    chosen_text = txt
                    break
            if chosen_text:
                candidate_text = chosen_text
                final_eq = self._extract_equation_line(chosen_text)
            else:
                final_eq = self._extract_equation_line(candidate_text) or locked_eq or "Ndot = J * A"
        else:
            final_eq = locked_eq or self._extract_equation_line(candidate_text) or "Ndot = J * A"

        final_eq = self._sanitize_equation(final_eq)

        if must_mutate:
            final_norm = re.sub(r"\s+", "", final_eq).lower()
            original_norm = re.sub(r"\s+", "", (original_locked or "")).lower()
            critique_pool = f"{hermes}\n{chymicus}".lower()
            if final_norm and original_norm and final_norm == original_norm:
                candidates = []
                if hasattr(self, "_build_deterministic_child_from_parent") and (original_locked or locked_eq):
                    try:
                        child = getattr(self, "_build_deterministic_child_from_parent")(original_locked or locked_eq)
                        if child:
                            candidates.append(child)
                    except Exception:
                        pass
                cycle = self._cycle_variable_symbol()
                core = self._core_equation_symbols()
                if cycle and "A" in core and ("gradient" in critique_pool or "diffusion" in critique_pool):
                    candidates.append(f"Ndot = ({cycle} * A) / L" if "L" in core else f"Ndot = {cycle} * A")
                if cycle and "R" in core:
                    candidates.append(f"Ndot = {cycle} / (1 + R)")
                if cycle and "A" in core and "R" in core:
                    candidates.append(f"Ndot = ({cycle} * A) / (1 + R)")
                final_eq = self._pick_distinct_equation(candidates, banned_signatures)
                final_eq = self._sanitize_equation(final_eq)

        defs: Dict[str, str] = self._extract_defs(candidate_text)
        if not defs:
            defs = {"Ndot": "débit total"}
            for sym in self._extract_equation_symbols(final_eq):
                if sym == "A":
                    defs[sym] = "surface d'échange active"
                elif sym == "R":
                    defs[sym] = "résistance globale de transfert"
                elif sym == "L":
                    defs[sym] = "distance ou longueur caractéristique"
                else:
                    defs[sym] = f"variable validée {sym}"

        object_calculated = self._extract_equation_object(candidate_text)
        law_type = self._extract_equation_type(candidate_text)
        architecture = self._extract_equation_architecture(candidate_text)
        mechanism = self._extract_named_field(candidate_text, "Mécanisme causal")
        if must_mutate and not mechanism:
            mechanism = "la mutation explicite le terme moteur ou la limitation du transport"

        experiment = self._extract_named_field(candidate_text, "Expérience")

        raw_links = self._extract_links(candidate_text)
        links = [l for l in raw_links if self._is_causal_link(l)]
        if must_mutate and not any(("limite" in x.lower() or "gradient" in x.lower() or "relie" in x.lower()) for x in links):
            if "R" in final_eq and "A" in final_eq:
                links.extend(["R limite Ndot", "A augmente Ndot"])
            elif "L" in final_eq:
                links.extend(["L freine Ndot", "la variable du cycle augmente Ndot"])

        cycle_symbol = self._cycle_variable_symbol() or "la variable du cycle"
        if len(links) < 2:
            if "R" in final_eq:
                links = [f"R diminue Ndot", f"{cycle_symbol} augmente Ndot"]
            elif "L" in final_eq:
                links = [f"L diminue Ndot", f"{cycle_symbol} augmente Ndot"]
            else:
                links = [f"{cycle_symbol} augmente Ndot", f"{cycle_symbol} contrôle le transfert net"]

        remark = self._extract_remark(candidate_text)
        if must_mutate and not remark:
            remark = "mutation forcée pour éviter la répétition de structure"

        decision_src = final_validator or archivist
        decision = self._parse_decision(decision_src)
        lowered = decision.lower()
        approved = (
            any(x in lowered for x in ["approuv", "accept", "valide"])
            and "part" not in lowered
            and "réparer" not in lowered
            and "reparer" not in lowered
        )

        fallback_used = self._equation_is_provisional_fallback(
            final_eq,
            self._last_by("Aurelius"),
            synth,
            archivist,
            final_validator,
            remark,
        )
        repair_required = bool(fallback_used)
        stable_parent = bool(approved and not fallback_used and not repair_required)
        memory_decision = "approved_stable" if stable_parent else ("temporary_fallback_only" if fallback_used else "needs_repair")

        if must_mutate:
            final_norm = re.sub(r"\s+", "", final_eq).lower()
            original_norm = re.sub(r"\s+", "", (original_locked or "")).lower()
            if final_norm == original_norm:
                approved = False
                decision = "à réparer"

        if not self._equation_uses_memory(final_eq):
            approved = False
            decision = "à réparer"
        if self._unknown_symbols(final_eq):
            approved = False
            decision = "à réparer"

        if self._structure_signature(final_eq) in banned_signatures:
            approved = False
            decision = "à réparer"
            final_eq = self._pick_distinct_equation(self._fallback_equation_candidates(), banned_signatures)

        if fallback_used:
            approved = False
            decision = "à réparer"
            repair_required = True
            stable_parent = False
            memory_decision = "temporary_fallback_only"
            if not remark:
                remark = "fallback conservé provisoirement, non promu comme parent stable"

        status = "approved" if approved else "partial"
        required_next: List[str] = []
        for src in [archivist, final_validator]:
            for line in (src or "").splitlines():
                low = line.lower().strip()
                if low.startswith("à reprendre") or low.startswith("a reprendre") or low.startswith("à vérifier") or low.startswith("a verifier"):
                    required_next.extend([x.strip(" -*•") for x in line.split(":", 1)[-1].split(",") if x.strip(" -*•")])

        if must_mutate:
            required_next.append("éviter la répétition de structure")
            if "J" in final_eq and ("D" not in final_eq and "k" not in final_eq):
                required_next.append("expliciter J")

        repeat_count_after = self._update_repeat_counter(final_eq)
        final_signature = self._structure_signature(final_eq)
        signatures = list(self.state.get("equation_signatures", []))
        if final_signature and final_signature not in signatures:
            signatures.append(final_signature)
        self.state["equation_signatures"] = signatures[-20:]

        missing_link_symbols = self._missing_link_symbols(final_eq, links)
        contradictions = self._detect_link_contradictions(links)

        eq_issues: List[str] = []
        if contradictions:
            eq_issues.append("contradictions causales: " + ", ".join(contradictions))
        if missing_link_symbols:
            eq_issues.append("variables absentes des liens: " + ", ".join(missing_link_symbols))
        if self._unknown_symbols(final_eq):
            eq_issues.append("variables non validées: " + ", ".join(self._unknown_symbols(final_eq)))
        if not self._equation_uses_cycle_variable(final_eq):
            eq_issues.append("variable du cycle absente")
        if fallback_used:
            eq_issues.append("fallback provisoire")

        repair_issue = self._infer_repair_issue(eq_issues, approved, repair_required, fallback_used)
        repair_fix = self._infer_repair_fix(final_eq, original_locked or locked_eq or "", links, missing_link_symbols, contradictions)
        repair_pattern = self._infer_repair_pattern(repair_issue, repair_fix, original_locked or locked_eq or "", final_eq)
        repair_log = {
            "turn": turn,
            "status": status,
            "original_equation": (original_locked or locked_eq or ""),
            "fixed_equation": final_eq,
            "main_issue": repair_issue,
            "fix_applied": repair_fix,
            "pattern": repair_pattern,
        } if ((repair_required or status != "approved") and final_eq) else ({
            "turn": turn,
            "status": status,
            "original_equation": (original_locked or locked_eq or final_eq),
            "fixed_equation": final_eq,
            "main_issue": repair_issue,
            "fix_applied": repair_fix or "consolidation finale",
            "pattern": repair_pattern,
        } if (approved and final_eq and (original_locked or locked_eq) and re.sub(r"\s+", "", original_locked or locked_eq) != re.sub(r"\s+", "", final_eq)) else {})

        equation = SharedEquation(
            equation=final_eq,
            definitions=defs,
            mechanism=mechanism,
            experiment=experiment,
            links=list(dict.fromkeys(links))[:12],
            remark=remark,
            approved=approved,
            source_turn=turn,
            source_agent="Synthetiseur" if synth else "Aurelius",
            validation_summary=(final_validator or "")[:2200],
            required_next=list(dict.fromkeys(required_next))[:10],
            status=status,
            fallback_used=fallback_used,
            stable_parent=stable_parent,
            repair_required=repair_required,
            memory_decision=memory_decision,
            object_calculated=object_calculated,
            law_type=law_type,
            architecture=architecture,
            parent_equation=(original_locked if must_mutate else ""),
            parent_variable=(self.state.get("parent_variable", "") if must_mutate else self._cycle_variable_symbol()),
            exploratory_parent=(not approved and not fallback_used and (repair_required or status == "partial")),
            repair_log=repair_log,
        )
        self.shared.add_equation(equation)

        score_global = self.shared.compute_equation_turn_score(equation)
        self.shared.record_turn_metric(
            kind="equation",
            turn=turn,
            element=equation.equation,
            signature=final_signature,
            score=score_global,
            approved=approved,
            note=(decision or "")[:240],
        )

        stag = self.shared.detect_stagnation("equation")
        if stag.get("stagnant"):
            reason_bits = []
            if stag.get("repeated_signature"):
                reason_bits.append("signature répétée")
            if stag.get("no_recent_approved"):
                reason_bits.append("aucune validation récente")
            self.shared.register_stagnation_event(
                kind="equation",
                turn=turn,
                reason=", ".join(reason_bits) or "stagnation",
            )

        self.shared.add_final_validation("equation", turn, (final_validator or "")[:1400], decision)
        fusion = self.shared.suggest_fusion() or ""
        self.shared.add_debate_summary(
            "equation",
            turn,
            f"{equation.equation} | approved={approved} | score_global={score_global} | repeat_count={repeat_count_after} | fusion={fusion[:120]} | liens={'; '.join(equation.links[:4])} | remarque={remark}",
        )
        self.state["locked_equation"] = ""
