from __future__ import annotations

import re
from typing import Dict, List, Set
import unicodedata

from base_debate_core import BaseDebateCore
from prompt_guard import prompt_injection_guardrails, wrap_untrusted_block, sanitize_untrusted_text
from prompts import get_agent_prompt
from shared_memory import SharedVariable


VALID_FAMILIES = {
    "flux",
    "transport",
    "temps caracteristique",
    "structure",
    "cinetique",
    "resistance",
    "concentration",
    "limitation",
}

SYMBOL_PROFILES = {
    "J": {"family": "flux", "unit_any": ["m⁻²", "m2", "m^2"], "unit_all": ["s"], "definition_any": ["flux", "transfert", "interface", "transport"]},
    "A": {"family": "structure", "unit_any": ["m²", "m2", "m^2"], "definition_any": ["surface", "interface", "aire", "contact"]},
    "L": {"family": "structure", "unit_any": ["m"], "definition_any": ["longueur", "epaisseur", "épaisseur", "distance"]},
    "τ": {"family": "temps caracteristique", "unit_any": ["s", "sec", "seconde"], "definition_any": ["temps", "relaxation", "reponse", "réponse"]},
    "tau": {"family": "temps caracteristique", "unit_any": ["s", "sec", "seconde"], "definition_any": ["temps", "relaxation", "reponse", "réponse"]},
    "D": {"family": "transport", "unit_any": ["m²/s", "m2/s", "m^2/s"], "definition_any": ["diffusion", "transport", "diffusiv"]},
    "D_eff": {"family": "transport", "unit_any": ["m²/s", "m2/s", "m^2/s"], "definition_any": ["diffusion", "transport", "diffusiv"]},
    "k": {"family": "cinetique", "unit_any": ["s⁻¹", "1/s"], "definition_any": ["cinet", "cinét", "vitesse", "reaction", "réaction"]},
    "k_eff": {"family": "cinetique", "unit_any": ["s⁻¹", "1/s"], "definition_any": ["cinet", "cinét", "vitesse", "reaction", "réaction"]},
    "R": {"family": "resistance", "unit_any": ["s/m", "s·m⁻¹"], "definition_any": ["resistance", "résistance", "limitation", "frein"]},
    "R_int": {"family": "resistance", "unit_any": ["s/m", "s·m⁻¹"], "definition_any": ["resistance", "résistance", "limitation", "frein"]},
}


class VariableDebateOrchestrator(BaseDebateCore):
    debate_kind = "variable"
    turn_prefix = "variable tour"

    sequence = [
        "Hermes",
        "HermesValidator",
        "Aurelius",
        "Basilide",
        "Chymicus",
        "Sentinelle",
        "VariableValidator",
        "Synthetiseur",
        "Archiviste",
        "Hermetica",
        "Reviseur",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        approved = self._get_approved_variables()
        self.stable_variables: Set[str] = set(approved.keys())
        self.seen_variables: Set[str] = set(approved.keys())
        self.variable_history: List[Dict[str, object]] = []
        self.family_counts: Dict[str, int] = self._build_family_counts(approved)
        self.signature_counts: Dict[str, int] = self._build_signature_counts(approved)

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------
    def _forbidden_variables_block(self) -> str:
        approved = self._get_approved_variables()
        if not approved:
            return (
                "VARIABLES DÉJÀ VALIDÉES : aucune\n"
                "VARIABLES INTERDITES POUR CE TOUR : aucune\n"
            )
        forbidden = ", ".join(sorted(approved.keys()))
        return (
            f"VARIABLES DÉJÀ VALIDÉES : {forbidden}\n"
            f"VARIABLES INTERDITES POUR CE TOUR : {forbidden}\n"
            "RÈGLE : il est interdit de reproposer une variable déjà validée.\n"
        )

    def _novelty_rule_block(self) -> str:
        return (
            "OBLIGATION DE NOUVEAUTÉ\n"
            "- Si une variable stable existe déjà, il est interdit de la reproposer.\n"
            "- Une répétition d'une variable stable = échec.\n"
            "- Il faut proposer une variable nouvelle, mesurable, définie, avec unité, mesure et rôle causal.\n"
            "- La sortie doit contenir explicitement une ligne : Variable : X\n"
            "- Ne pas répondre uniquement avec un diagnostic, une checklist ou un commentaire.\n"
            "- Il faut fournir une vraie proposition structurée.\n"
            "- Interdiction des symboles ambigus seuls : T, C, D, R. Utilise plutôt T_surf, C_x, D_eff, R_int, J, tau, A_eff, etc.\n"
        )


    def _suggested_variables_block(self) -> str:
        try:
            suggestions = list(self.shared.suggest_variables_for_equation("", limit=4) or [])
        except Exception:
            suggestions = []
        if not suggestions:
            return "SUGGESTIONS ARCHIVISTE++ : aucune suggestion forte pour ce tour."
        lines = ["SUGGESTIONS ARCHIVISTE++"]
        for row in suggestions:
            sym = str(row.get("symbol", "") or row.get("name", "") or "").strip()
            why = str(row.get("reason", "") or "").strip() or "variable utile"
            if sym:
                lines.append(f"- {sym} : {why}")
        lines.append("RÈGLE : tu peux utiliser une suggestion, mais tu dois quand même proposer une variable structurée et nouvelle.")
        return "\n".join(lines)

    def _required_output_block(self) -> str:
        return (
            "FORMAT MINIMAL OBLIGATOIRE\n"
            "Variable : X\n"
            "Famille : ...\n"
            "Définition : ...\n"
            "Unité : ...\n"
            "Mesure : ...\n"
            "Rôle causal : ...\n"
            "Liens :\n"
            "- ...\n"
            "- ...\n"
        )

    def build_agent_prompt(self, agent_name: str, agent_prompt: str = "", shared_memory: str = "") -> str:
        if not agent_prompt.strip():
            agent_prompt = get_agent_prompt(agent_name, "variable")
        parts = [
            agent_prompt.strip(),
            "",
            self._forbidden_variables_block().strip(),
            "",
            self._novelty_rule_block().strip(),
            "",
            self._required_output_block().strip(),
            '',
            self._suggested_variables_block().strip(),
            '',
            self._physics_core_block().strip(),
        ]
        if agent_name == "Hermes":
            parts.extend(
                [
                    "",
                    "RÈGLE HERMES-GUARD",
                    "- Ta variable sera vérifiée immédiatement contre la mémoire.",
                    "- Si elle existe déjà elle sera rejetée. Une famille déjà vue n'est rejetée que si rien de nouveau n'est ajouté.",
                    "- En cas de rejet, tu devras proposer une autre variable en suivant une piste d'exploration.",
                    "- Évite les variantes triviales de concentration : C, C_eff, C_local, C_active.",
                ]
            )
        if shared_memory and shared_memory.strip():
            parts.extend(["", "MÉMOIRE PARTAGÉE", shared_memory.strip()])
        return "\n".join(parts).strip() + "\n"

    def _physics_core_block(self) -> str:
        starter = dict(getattr(self.cfg, 'physics_core_starter_pack', {}) or {})
        if not starter:
            return ''
        lines = [
            'NOYAU PHYSIQUE VERROUILLÉ (NE PAS REDÉFINIR)',
            '- Les symboles suivants gardent leur sens canonique.',
            '- Ils peuvent être utilisés, précisés ou combinés, mais pas redéfinis.',
        ]
        for symbol, payload in starter.items():
            family = str(payload.get('family', '')).strip() or '-'
            definition = str(payload.get('definition', '')).strip() or '-'
            unit = str(payload.get('unit', '')).strip() or '-'
            lines.append(f"- {symbol} | famille={family} | unité={unit} | sens={definition}")
        return '\n'.join(lines)

    def _locked_variable_symbol(self) -> str:
        return self._normalize_symbol(str(self.state.get("locked_variable", "")))

    def _locked_variable_text(self) -> str:
        return str(self.state.get("locked_variable_text", "") or "")

    def _locked_variable_block(self, agent_name: str) -> str:
        locked = self._locked_variable_symbol()
        if not locked or agent_name in {"Hermes", "HermesValidator"}:
            return ""
        return (
            "VARIABLE VERROUILLÉE POUR CE TOUR\n"
            f"- variable cible : {locked}\n"
            "- interdiction de proposer une autre variable principale\n"
            "- tu dois enrichir, critiquer, mesurer, relier ou tester cette variable\n"
            "- toute autre variable principale sera rejetée\n"
        )

    def _compose_prompt(self, speaker: str) -> str:
        shared_memory = ""
        try:
            shared_memory = self._shared_block()
        except Exception:
            shared_memory = ""
        if speaker == "HermesValidator":
            return "HermesValidator est un garde déterministe, pas un agent modèle."
        prompt = "\n\n".join([prompt_injection_guardrails(), self.build_agent_prompt(speaker, get_agent_prompt(speaker, "variable"), shared_memory)])
        lock_block = self._locked_variable_block(speaker)
        if lock_block:
            prompt += "\n" + lock_block
        return prompt

    # ------------------------------------------------------------------
    # Memory / families
    # ------------------------------------------------------------------
    def _get_approved_variables(self) -> Dict[str, object]:
        try:
            return self.shared.get_approved_variables() or {}
        except Exception:
            return {}

    def _normalize_symbol(self, symbol: str) -> str:
        symbol = unicodedata.normalize("NFKC", (symbol or "").strip())
        symbol = re.sub(r"<.*?>", "", symbol)
        symbol = symbol.replace("**", "").replace("*", "")
        symbol = re.sub(r"\s+", "", symbol).strip()
        match = re.match(r"([A-Za-zΑ-Ωα-ωΔΦΨχρτ_][A-Za-z0-9Α-Ωα-ωΔΦΨχρτ_]*)", symbol)
        return match.group(1) if match else symbol

    def _normalize_family(self, family: str) -> str:
        family = (family or "").strip().lower()
        family = family.replace("é", "e").replace("è", "e").replace("ê", "e")
        family = family.replace("à", "a").replace("ù", "u")
        family = re.sub(r"\s+", " ", family)
        aliases = {
            "temps": "temps caracteristique",
            "surface": "structure",
            "geometrie": "structure",
            "géométrie": "structure",
            "diffusion": "transport",
            "coefficient de diffusion": "transport",
            "vitesse": "cinetique",
            "cinetique reactionnelle": "cinetique",
            "flux de matiere": "flux",
            "flux de matière": "flux",
        }
        return aliases.get(family, family)

    def _symbol_profile(self, symbol: str) -> Dict[str, object]:
        norm = self._normalize_symbol(symbol)
        return dict(SYMBOL_PROFILES.get(norm, {}) or SYMBOL_PROFILES.get(norm.lower(), {}) or {})

    def _symbol_semantic_incoherent(self, symbol: str, family: str, unit: str, definition: str) -> bool:
        profile = self._symbol_profile(symbol)
        if not profile:
            return False
        fam = self._normalize_family(family)
        if profile.get("family") and fam and fam != str(profile.get("family")):
            return True
        unit_low = str(unit or "").lower()
        required_any = [str(x).lower() for x in profile.get("unit_any", [])]
        if required_any and not any(tok in unit_low for tok in required_any):
            return True
        required_all = [str(x).lower() for x in profile.get("unit_all", [])]
        if required_all and not all(tok in unit_low for tok in required_all):
            return True
        definition_low = str(definition or "").lower()
        definition_any = [str(x).lower() for x in profile.get("definition_any", [])]
        if definition_any and not any(tok in definition_low for tok in definition_any):
            return True
        return False

    def _ambiguous_symbols(self) -> Set[str]:
        return {"T", "C", "D", "R"}

    def _is_ambiguous_symbol(self, symbol: str) -> bool:
        norm = self._normalize_symbol(symbol)
        return bool(norm) and norm.upper() in self._ambiguous_symbols()

    def _infer_family_from_symbol(self, symbol: str) -> str:
        s = self._normalize_symbol(symbol).lower()
        if not s:
            return ""
        if s.startswith("c"):
            return "concentration"
        if s.startswith("d"):
            return "transport"
        if s.startswith("j"):
            return "flux"
        if s.startswith("t") or s.startswith("tau"):
            return "temps caracteristique"
        if s.startswith("a") or s.startswith("l") or s.startswith("phi"):
            return "structure"
        if s.startswith("r"):
            return "resistance"
        if s.startswith("k"):
            return "cinetique"
        return ""


    def _infer_family_from_unit(self, unit: str, definition: str = "") -> str:
        u = (unit or "").strip().lower()
        d = (definition or "").strip().lower()
        if any(tok in u for tok in ["m²", "m2", "m^2"]):
            if any(tok in d for tok in ["surface", "interface", "contact", "aire", "zone"]):
                return "structure"
        if any(tok in u for tok in ["s", "sec", "seconde"]) and not any(tok in u for tok in ["mol", "kg", "m²", "m2", "m^2", "m/s", "s⁻¹", "1/s"]):
            return "temps caracteristique"
        if any(tok in u for tok in ["mol·m⁻²·s⁻¹", "mol/m²/s", "kg·m⁻²·s⁻¹"]):
            return "flux"
        if any(tok in u for tok in ["m²/s", "m2/s", "m^2/s"]):
            return "transport"
        if any(tok in u for tok in ["s⁻¹", "1/s"]):
            return "cinetique"
        if any(tok in u for tok in ["s·m⁻¹", "s/m"]):
            return "resistance"
        if any(tok in u for tok in ["mol/l", "g/l", "kg/m³", "kg/m3", "mol/m³", "mol/m3"]):
            return "concentration"
        return ""

    def _family_unit_incoherent(self, family: str, unit: str, definition: str = "") -> bool:
        fam = self._normalize_family(family)
        inferred = self._infer_family_from_unit(unit, definition)
        if not fam or not inferred:
            return False
        compatible = {
            'structure': {'structure'},
            'temps caracteristique': {'temps caracteristique'},
            'flux': {'flux'},
            'transport': {'transport'},
            'cinetique': {'cinetique'},
            'resistance': {'resistance'},
        }
        return inferred not in compatible.get(fam, {fam})

    def _definition_keywords(self, definition: str) -> str:
        text = (definition or "").lower()
        repl = {
            "é": "e", "è": "e", "ê": "e", "à": "a", "ù": "u", "ô": "o", "î": "i", "ï": "i"
        }
        for a, b in repl.items():
            text = text.replace(a, b)
        text = re.sub(r"[^a-z0-9 ]+", " ", text)
        stop = {
            "variable", "espece", "dans", "milieu", "mesuree", "mesure", "effective",
            "locale", "dune", "une", "des", "les", "pour", "avec", "par",
            "sur", "dun", "du", "la", "le", "de", "et"
        }
        tokens = [t for t in text.split() if len(t) >= 4 and t not in stop]
        return "-".join(tokens[:2])

    def _variable_signature(self, symbol: str = "", family: str = "", unit: str = "", definition: str = "", measure: str = "") -> str:
        family = self._normalize_family(family) or self._infer_family_from_symbol(symbol)
        unit = self._normalize_symbol(unit).lower()
        head = self._definition_keywords(definition)
        measure_head = self._definition_keywords(measure)
        return f"{family}|{unit}|{head}|{measure_head}"

    def _build_signature_counts(self, approved: Dict[str, object]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for sym, payload in approved.items():
            if isinstance(payload, dict):
                sig = self._variable_signature(
                    symbol=str(sym),
                    family=str(payload.get("family", "")),
                    unit=str(payload.get("unit", "")),
                    definition=str(payload.get("definition", "")),
                    measure=str(payload.get("measure", "")),
                )
            else:
                sig = self._variable_signature(symbol=str(sym))
            if sig.strip("|"):
                counts[sig] = counts.get(sig, 0) + 1
        return counts

    def _is_signature_seen(self, signature: str) -> bool:
        if not signature.strip("|"):
            return False
        limit = int(getattr(self.cfg, "variable_signature_soft_limit", 2))
        return self.signature_counts.get(signature, 0) >= limit

    def _build_family_counts(self, approved: Dict[str, object]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for sym, payload in approved.items():
            family = ""
            if isinstance(payload, dict):
                family = self._normalize_family(str(payload.get("family", "")))
            if not family:
                family = self._infer_family_from_symbol(str(sym))
            if family:
                counts[family] = counts.get(family, 0) + 1
        return counts

    def _approved_semantic_map(self) -> Dict[str, Dict[str, str]]:
        approved = self._get_approved_variables()
        out: Dict[str, Dict[str, str]] = {}
        for sym, payload in approved.items():
            if not isinstance(payload, dict):
                continue
            norm = self._normalize_symbol(sym)
            out[norm] = {
                "family": self._normalize_family(str(payload.get("family", ""))),
                "unit": self._normalize_symbol(str(payload.get("unit", ""))).lower(),
                "definition_head": self._definition_keywords(str(payload.get("definition", ""))),
            }
        return out

    def _pending_semantic_map(self) -> Dict[str, Dict[str, str]]:
        try:
            pending = self.shared.get_pending_variables() or {}
        except Exception:
            pending = {}
        out: Dict[str, Dict[str, str]] = {}
        for sym, payload in pending.items():
            if not isinstance(payload, dict):
                continue
            norm = self._normalize_symbol(sym)
            out[norm] = {
                "family": self._normalize_family(str(payload.get("family", ""))),
                "unit": self._normalize_symbol(str(payload.get("unit", ""))).lower(),
                "definition_head": self._definition_keywords(str(payload.get("definition", ""))),
            }
        return out

    def _reserve_pending_symbol(self, symbol: str, family: str, unit: str, definition: str) -> None:
        norm = self._normalize_symbol(symbol)
        if not norm:
            return
        approved_symbols = {self._normalize_symbol(k) for k in self._get_approved_variables().keys()}
        if norm in approved_symbols:
            return
        try:
            pending = self.shared.get_pending_variables() or {}
        except Exception:
            pending = {}
        if norm in {self._normalize_symbol(k) for k in pending.keys()}:
            return
        self.shared.add_pending_variable(norm, {
            "family": self._normalize_family(family) or self._infer_family_from_symbol(symbol),
            "unit": self._normalize_symbol(unit).lower(),
            "definition": definition,
        })

    def _pending_conflicts(self, symbol: str, family: str, unit: str, definition: str) -> bool:
        ref = self._pending_semantic_map().get(self._normalize_symbol(symbol))
        if not ref:
            return False
        cur_family = self._normalize_family(family) or self._infer_family_from_symbol(symbol)
        cur_unit = self._normalize_symbol(unit).lower()
        cur_def = self._definition_keywords(definition)
        if ref["family"] and cur_family and ref["family"] != cur_family:
            return True
        if ref["unit"] and cur_unit and ref["unit"] != cur_unit:
            return True
        if ref["definition_head"] and cur_def and ref["definition_head"] != cur_def:
            return True
        return False

    def _semantic_drift(self, symbol: str, family: str, unit: str, definition: str) -> bool:
        ref = self._approved_semantic_map().get(self._normalize_symbol(symbol))
        if not ref:
            return False
        cur_family = self._normalize_family(family)
        cur_unit = self._normalize_symbol(unit).lower()
        cur_def = self._definition_keywords(definition)
        if ref["family"] and cur_family and ref["family"] != cur_family:
            return True
        if ref["unit"] and cur_unit and ref["unit"] != cur_unit:
            return True
        if ref["definition_head"] and cur_def and ref["definition_head"] != cur_def:
            return True
        return False

    def _is_family_saturated(self, family: str) -> bool:
        family = self._normalize_family(family)
        limit = int(getattr(self.cfg, "variable_family_soft_limit", 2))
        return bool(family) and self.family_counts.get(family, 0) >= limit

    def _suggest_exploration_track(self) -> str:
        approved = self._get_approved_variables()
        approved_symbols = {self._normalize_symbol(k) for k in approved.keys()}
        used_families = set(self._build_family_counts(approved).keys())
        candidates: List[str] = []
        if "transport" not in used_families:
            candidates.extend(["D", "D_eff", "J"])
        if "temps caracteristique" not in used_families:
            candidates.extend(["τ", "t_char"])
        if "structure" not in used_families:
            candidates.extend(["A", "L", "φ"])
        if "cinetique" not in used_families:
            candidates.extend(["k", "k_eff"])
        if "resistance" not in used_families:
            candidates.extend(["R"])
        deduped: List[str] = []
        for c in candidates:
            if self._normalize_symbol(c) not in approved_symbols and c not in deduped:
                deduped.append(c)
        if not deduped:
            deduped = ["D_eff", "L", "A", "τ", "R", "φ", "k_eff"]
        return (
            "PISTE À EXPLORER : éviter la famille actuelle et proposer une variable "
            "de transport, structure, temps, flux, cinétique ou limitation. "
            f"Candidats suggérés : {', '.join(deduped[:6])}."
        )

    # ------------------------------------------------------------------
    # Evaluation and retry
    # ------------------------------------------------------------------
    def _evaluate_agent_output(self, speaker: str, text: str) -> Dict[str, object]:
        if speaker == "HermesValidator":
            return {"issues": [], "score": 0, "is_valid": True}
        result = self._evaluate_variable_candidate(text)
        if speaker == "Hermes":
            return result
        locked = self._locked_variable_symbol()
        symbol = self._normalize_symbol(str(result.get("symbol", "")))
        if locked and symbol and symbol != locked:
            issues = list(result.get("issues", []))
            if "variable verrouillée non respectée" not in issues:
                issues.append("variable verrouillée non respectée")
            result = {
                **result,
                "issues": issues,
                "score": min(int(result.get("score", 0)), 1),
                "is_valid": False,
            }
        return result

    def _evaluate_variable_candidate(self, text: str) -> Dict[str, object]:
        base_eval = self._evaluate_variable_structure(text)
        parsed = base_eval["parsed"]
        symbol = str(base_eval.get("symbol", "")).strip()
        issues = list(base_eval.get("issues", []))
        score = int(base_eval.get("score", 0))

        approved = self._get_approved_variables()
        approved_symbols = {self._normalize_symbol(k) for k in approved.keys()}
        family = self._normalize_family(str(parsed.get("family", "")))
        if not family or family in {"...", "t", symbol.lower()}:
            family = self._infer_family_from_symbol(symbol)
        if not family:
            family = self._infer_family_from_unit(str(parsed.get("unit", "")), str(parsed.get("definition", "")))
        if not family:
            family = "structure" if any(u in str(parsed.get("unit", "")).lower() for u in ["m²", "m2", "m^2"]) else "indeterminee"
        family = self._normalize_family(family)
        norm_symbol = self._normalize_symbol(symbol)
        signature = self._variable_signature(
            symbol=symbol,
            family=family,
            unit=str(parsed.get("unit", "")),
            definition=str(parsed.get("definition", "")),
            measure=str(parsed.get("measure", "")),
        )

        if norm_symbol and self._is_ambiguous_symbol(norm_symbol):
            if "symbole ambigu interdit" not in issues:
                issues.append("symbole ambigu interdit")
            score = 0

        if norm_symbol and norm_symbol in approved_symbols:
            if "variable déjà validée" not in issues:
                issues.append("variable déjà validée")
            score = 0

        if norm_symbol and norm_symbol not in approved_symbols and not self._is_ambiguous_symbol(norm_symbol):
            self._reserve_pending_symbol(norm_symbol, family, str(parsed.get("unit", "")), str(parsed.get("definition", "")))
            if self._pending_conflicts(norm_symbol, family, str(parsed.get("unit", "")), str(parsed.get("definition", ""))):
                if "variable en cours redéfinie" not in issues:
                    issues.append("variable en cours redéfinie")
                return {
                    "parsed": parsed,
                    "symbol": symbol,
                    "family": family,
                    "signature": signature,
                    "issues": issues,
                    "score": 0,
                    "is_valid": False,
                }

        if norm_symbol and self._semantic_drift(norm_symbol, family, str(parsed.get("unit", "")), str(parsed.get("definition", ""))):
            if "dérive sémantique" not in issues:
                issues.append("dérive sémantique")
            score = 0

        existing_payload = {}
        try:
            existing_payload = self.shared.get_variable_payload(norm_symbol) if norm_symbol else {}
        except Exception:
            existing_payload = {}
        if existing_payload:
            existing_family = self._normalize_family(str(existing_payload.get("family", "")))
            existing_unit = str(existing_payload.get("unit", "")).strip().lower()
            current_unit = str(parsed.get("unit", "")).strip().lower()
            if existing_family and family and existing_family != family:
                if "verrou sémantique violé" not in issues:
                    issues.append("verrou sémantique violé")
                score = 0
            if existing_unit and current_unit and existing_unit != current_unit:
                if "verrou d'unité violé" not in issues:
                    issues.append("verrou d'unité violé")
                score = 0

        if family not in VALID_FAMILIES:
            if "famille invalide" not in issues:
                issues.append("famille invalide")
            score = 0

        if self._symbol_semantic_incoherent(symbol, family, str(parsed.get("unit", "")), str(parsed.get("definition", ""))):
            if "symbole incohérent" not in issues:
                issues.append("symbole incohérent")
            score = 0

        if self._family_unit_incoherent(family, str(parsed.get("unit", "")), str(parsed.get("definition", ""))):
            if "famille/unité incohérentes" not in issues:
                issues.append("famille/unité incohérentes")
            score = 0

        if family and self._is_family_saturated(family) and norm_symbol not in approved_symbols:
            if "famille déjà dense" not in issues:
                issues.append("famille déjà dense")
            score = min(score, 2)

        if signature and self._is_signature_seen(signature) and norm_symbol not in approved_symbols:
            if "signature déjà dense" not in issues:
                issues.append("signature déjà dense")
            score = min(score, 1)

        is_valid = (
            bool(symbol)
            and bool(parsed.get("definition"))
            and bool(parsed.get("unit"))
            and bool(parsed.get("measure"))
            and bool(parsed.get("role"))
            and norm_symbol not in approved_symbols
            and not (family and self._is_family_saturated(family))
            and "dérive sémantique" not in issues
            and "variable en cours redéfinie" not in issues
            and "symbole ambigu interdit" not in issues
            and "famille/unité incohérentes" not in issues
            and "famille invalide" not in issues
            and "symbole incohérent" not in issues
            and "verrou sémantique violé" not in issues
            and "verrou d'unité violé" not in issues
        )
        return {
            "parsed": parsed,
            "symbol": symbol,
            "family": family,
            "signature": signature,
            "issues": issues,
            "score": score,
            "is_valid": is_valid,
        }

    def _should_retry(self, eval_result: Dict[str, object]) -> bool:
        critical = {
            "variable absente",
            "définition absente",
            "unité absente",
            "mesure absente",
            "rôle causal absent",
            "variable déjà validée",
            "famille déjà dense",
            "signature déjà dense",
            "variable verrouillée non respectée",
            "variable en cours redéfinie",
            "dérive sémantique",
            "symbole ambigu interdit",
            "famille/unité incohérentes",
            "famille invalide",
            "symbole incohérent",
            "verrou sémantique violé",
            "verrou d'unité violé",
        }
        return any(issue in critical for issue in eval_result.get("issues", []))

    def _targeted_retry_instruction(self, eval_result: Dict[str, object]) -> str:
        issues = eval_result.get("issues", [])
        instructions = [
            "Régénère en respectant strictement ce format :",
            "Variable : X",
            "Famille : ...",
            "Définition : ...",
            "Unité : ...",
            "Mesure : ...",
            "Rôle causal : ...",
            "Liens :",
            "- ...",
            "- ...",
        ]
        if "variable déjà validée" in issues:
            forbidden = ", ".join(sorted(self._get_approved_variables().keys()))
            if forbidden:
                instructions.append(f"INTERDICTION ABSOLUE : ne pas utiliser {forbidden}.")
            instructions.append(self._suggest_exploration_track())
        if "famille déjà dense" in issues:
            instructions.append("Ta proposition appartient à une famille déjà trop utilisée en mémoire.")
            instructions.append("INTERDICTION : ne repropose pas une variable de concentration ou une variante triviale.")
            instructions.append(self._suggest_exploration_track())
        if "variable absente" in issues:
            instructions.append("La première ligne utile doit être exactement : Variable : X")
        if "définition absente" in issues:
            instructions.append("Ajoute explicitement une ligne : Définition : ...")
        if "unité absente" in issues:
            instructions.append("Ajoute explicitement une ligne : Unité : ...")
        if "mesure absente" in issues:
            instructions.append("Ajoute explicitement une ligne : Mesure : ...")
        if "rôle causal absent" in issues:
            instructions.append("Ajoute explicitement une ligne : Rôle causal : ...")
        if "famille/unité incohérentes" in issues:
            instructions.append("La famille proposée ne correspond pas à l'unité ou à la définition mesurable.")
            instructions.append("Exemple : m² -> structure/surface, s -> temps caractéristique, mol·m⁻²·s⁻¹ -> flux, m²/s -> transport.")
        if "famille invalide" in issues:
            instructions.append("Utilise une famille autorisée : flux, transport, temps caractéristique, structure, cinétique, résistance, concentration ou limitation.")
        if "symbole incohérent" in issues:
            instructions.append("Conserve le sens physique canonique du symbole proposé et n'en change ni la nature ni l'unité.")
        if "verrou sémantique violé" in issues or "verrou d'unité violé" in issues:
            instructions.append("Respecte strictement la sémantique et l'unité déjà mémorisées pour ce symbole.")
        if "dérive sémantique" in issues:
            instructions.append("Le symbole existe déjà mais son sens a changé.")
            instructions.append("Interdiction : ne change ni sa famille, ni son unité, ni son sens physique.")
            instructions.append("Soit tu respectes exactement ce symbole, soit tu proposes un nouveau symbole.")
        if "variable en cours redéfinie" in issues:
            instructions.append("Le symbole est déjà réservé dans ce tour avec une autre unité, famille ou définition.")
            instructions.append("Interdiction absolue : ne redéfinis pas ce symbole. Choisis un autre symbole non ambigu.")
        if "symbole ambigu interdit" in issues:
            instructions.append("Le symbole proposé est trop ambigu pour les petits modèles.")
            instructions.append("Interdiction : n'utilise pas T, C, D ou R seuls. Utilise par exemple T_surf, C_x, D_eff, R_int, J, tau ou A_eff.")
        if "variable verrouillée non respectée" in issues:
            locked = self._locked_variable_symbol() or "X"
            instructions.append(f"La variable principale doit rester exactement : {locked}")
            instructions.append("N'introduis pas une nouvelle variable principale.")
        return "\n".join(instructions)

    # ------------------------------------------------------------------
    # HermesValidator deterministic gate
    # ------------------------------------------------------------------
    def _validate_hermes_variable(self, text: str) -> Dict[str, object]:
        eval_result = self._evaluate_variable_candidate(text)
        symbol = str(eval_result.get("symbol", "")).strip() or "-"
        family = str(eval_result.get("family", "")).strip() or "-"
        accepted = not self._should_retry(eval_result)
        if accepted:
            return {
                "accepted": True,
                "message": (
                    "Statut : acceptée\n"
                    f"Variable détectée : {symbol}\n"
                    f"Famille détectée : {family}\n"
                    "Raison : variable nouvelle et structure minimale acceptable"
                ),
                "retry_prompt": "",
                "eval": eval_result,
            }
        reasons = "; ".join(eval_result.get("issues", []))
        retry_prompt = self._compose_prompt("Hermes") + "\n\n" + self._targeted_retry_instruction(eval_result)
        return {
            "accepted": False,
            "message": (
                "Statut : rejetée\n"
                f"Variable détectée : {symbol}\n"
                f"Famille détectée : {family}\n"
                f"Raisons : {reasons}\n"
                f"{self._suggest_exploration_track()}"
            ),
            "retry_prompt": retry_prompt,
            "eval": eval_result,
        }

    def _build_deterministic_fallback_variable(self) -> str:
        templates = [
            (
                "J",
                "flux",
                "Flux local de matière à travers une interface ou un milieu.",
                "mol·m⁻²·s⁻¹",
                "Mesure par bilan matière sur cellule instrumentée ou par capteur de flux.",
                "J contrôle directement le débit local de transfert et relie gradient, surface et vitesse de transport.",
                [
                    "J augmente le transfert net de matière.",
                    "J diminue le temps nécessaire pour atteindre un état homogène.",
                ],
            ),
            (
                "τ",
                "temps caractéristique",
                "Temps caractéristique nécessaire pour qu'un système atteigne une réponse mesurable après une perturbation.",
                "s",
                "Mesure par suivi temporel du retour à l'équilibre ou du temps de relaxation.",
                "τ fixe la vitesse globale d'évolution du système : plus τ est grand, plus la réponse est lente.",
                [
                    "τ augmente la durée de réponse du système.",
                    "τ ralentit l'établissement du régime stable.",
                ],
            ),
            (
                "A",
                "structure",
                "Surface d'échange active effectivement disponible dans le milieu ou à l'interface.",
                "m²",
                "Mesure par imagerie, analyse géométrique ou estimation expérimentale de surface active.",
                "A contrôle la capacité d'échange : plus A est grande, plus le transfert local peut être élevé.",
                [
                    "A augmente la capacité de transfert local.",
                    "A influence l'intensité des échanges à l'interface.",
                ],
            ),
            (
                "k_eff",
                "cinetique",
                "Constante cinétique effective gouvernant la vitesse apparente d'un processus local mesurable.",
                "s⁻¹",
                "Mesure par ajustement cinétique sur série temporelle expérimentale.",
                "k_eff contrôle la rapidité apparente de transformation ou d'évolution du système.",
                [
                    "k_eff augmente la vitesse apparente du processus.",
                    "k_eff diminue le temps nécessaire pour observer une conversion donnée.",
                ],
            ),
            (
                "R",
                "resistance",
                "Résistance effective opposée au transfert dans le milieu ou à l'interface.",
                "s·m⁻¹",
                "Mesure par réponse forcée, bilan de gradient et estimation de l'opposition au flux.",
                "R limite le transfert : plus R est grande, plus la propagation et l'échange sont freinés.",
                [
                    "R diminue le flux net de transfert.",
                    "R ralentit la propagation d'une perturbation.",
                ],
            ),
        ]
        approved_symbols = {self._normalize_symbol(k) for k in self._get_approved_variables().keys()}
        for symbol, family, definition, unit, measure, role, links in templates:
            if self._normalize_symbol(symbol) in approved_symbols:
                continue
            if self._is_family_saturated(family):
                continue
            candidate = (
                f"Variable : {symbol}\n"
                f"Famille : {family}\n"
                f"Définition : {definition}\n"
                f"Unité : {unit}\n"
                f"Mesure : {measure}\n"
                f"Rôle causal : {role}\n"
                "Liens :\n"
                + "\n".join(f"- {line}" for line in links)
            )
            result = self._validate_hermes_variable(candidate)
            if result.get("accepted"):
                return candidate
        return (
            "Variable : J\n"
            "Famille : flux\n"
            "Définition : Flux local de matière dans un milieu.\n"
            "Unité : mol·m⁻²·s⁻¹\n"
            "Mesure : Bilan matière sur cellule de diffusion.\n"
            "Rôle causal : J contrôle directement le transfert net mesurable.\n"
            "Liens :\n"
            "- J augmente le transfert net.\n"
            "- J influence la vitesse d'homogénéisation."
        )

    def _run_agent_turn(self, speaker: str) -> None:
        # Deterministic gates: validators never call the LLM.
        if speaker == "VariableValidator":
            candidate_text = (
                self._locked_variable_text()
                or self._last_by("Hermes")
                or ""
            )
            result = self._validate_hermes_variable(candidate_text)
            status = "OK" if result.get("accepted") else "FLAG"
            print(f"VariableValidator: {result['message']}")
            print(f"[AGENT {status}] VariableValidator (deterministic gate, no LLM)")
            self.debate_state.add("VariableValidator", result["message"], max_history_messages=self.cfg.max_history_messages)
            self._append_log("VariableValidator", result["message"])
            return
        if speaker in {"Hermetica", "Reviseur"}:
            result = self._run_hermetic_gate(speaker)
            status = "OK" if result.get("accepted") else "FLAG"
            print(f"{speaker}: {result['message']}")
            print(f"[AGENT {status}] {speaker} (deterministic gate, no LLM)")
            self.debate_state.add(speaker, result["message"], max_history_messages=self.cfg.max_history_messages)
            self._append_log(speaker, result["message"])
            return
        if speaker != "HermesValidator":
            return super()._run_agent_turn(speaker)

        hermes_output = self._last_by("Hermes")
        result = self._validate_hermes_variable(hermes_output)
        print(f"HermesValidator: {result['message']}")
        self.debate_state.add("HermesValidator", result["message"], max_history_messages=self.cfg.max_history_messages)
        self._append_log("HermesValidator", result["message"])

        if result["accepted"]:
            self.state["locked_variable"] = self._normalize_symbol(str(result["eval"].get("symbol", "")))
            self.state["locked_variable_text"] = hermes_output
            return

        if not result.get("accepted"):
            # Deterministic-first: only retry with the LLM when explicitly enabled.
            if not getattr(self.cfg, "llm_enabled", False):
                print("HermesValidator: rejet, fallback déterministe activé (LLM désactivé)")
                fallback_text = self._build_deterministic_fallback_variable()
                fallback_result = self._validate_hermes_variable(fallback_text)
                print(f"Hermes: {fallback_text}")
                self.debate_state.add("Hermes", fallback_text, max_history_messages=self.cfg.max_history_messages)
                self._append_log("Hermes", fallback_text)
                self.debate_state.add("HermesValidator", fallback_result["message"], max_history_messages=self.cfg.max_history_messages)
                self._append_log("HermesValidator", fallback_result["message"])
                self.state["locked_variable"] = self._normalize_symbol(str(fallback_result["eval"].get("symbol", "")))
                self.state["locked_variable_text"] = fallback_text
                self.process_agent_output("Hermes", fallback_text)
                return
            model = self._model_for("Hermes")
            num_predict = min(self._num_predict_for("Hermes") + 35, 220)
            temperature = self._temperature_for("Hermes")
            print("[RETRY] Hermes relancé avec piste")
        retry_text = self.client.ask(
            model=model,
            messages=[{"role": "user", "content": result["retry_prompt"]}],
            temperature=temperature,
            max_tokens=num_predict,
        )
        retry_result = self._validate_hermes_variable(retry_text)
        retry_eval = retry_result.get("eval", {})
        status = "OK" if retry_result.get("accepted") else "FLAG"
        print(
            f"[AGENT {status}] Hermes score={retry_eval.get('score', 0)} "
            f"predict={num_predict} issues={'; '.join(retry_eval.get('issues', []))}"
        )

        if not retry_result.get("accepted"):
            print("HermesValidator: deuxième rejet, fallback déterministe activé")
            fallback_text = self._build_deterministic_fallback_variable()
            fallback_result = self._validate_hermes_variable(fallback_text)
            print(f"Hermes: {fallback_text}")
            self.debate_state.add("Hermes", fallback_text, max_history_messages=self.cfg.max_history_messages)
            self._append_log("Hermes", fallback_text)
            self.debate_state.add("HermesValidator", fallback_result["message"], max_history_messages=self.cfg.max_history_messages)
            self._append_log("HermesValidator", fallback_result["message"])
            self.state["locked_variable"] = self._normalize_symbol(str(fallback_result["eval"].get("symbol", "")))
            self.state["locked_variable_text"] = fallback_text
            self.process_agent_output("Hermes", fallback_text)
            return

        print(f"Hermes: {retry_text}")
        self.debate_state.add("Hermes", retry_text, max_history_messages=self.cfg.max_history_messages)
        self._append_log("Hermes", retry_text)
        self.state["locked_variable"] = self._normalize_symbol(str(retry_result["eval"].get("symbol", "")))
        self.state["locked_variable_text"] = retry_text
        self.process_agent_output("Hermes", retry_text)

    # ------------------------------------------------------------------
    # Storage/finalization
    # ------------------------------------------------------------------
    def process_agent_output(self, agent_name: str, text: str) -> Dict[str, object]:
        if agent_name == "HermesValidator":
            return {"issues": [], "score": 0, "is_valid": True}
        result = self._evaluate_agent_output(agent_name, text)
        locked = self._locked_variable_symbol()
        symbol = self._normalize_symbol(str(result.get("symbol", "")))
        if agent_name != "Hermes" and locked and symbol and symbol != locked:
            return result
        self._register_variable_if_stable(result, agent_name)
        return result

    def _register_variable_if_stable(self, eval_result: Dict[str, object], agent_name: str) -> None:
        symbol = str(eval_result.get("symbol", "")).strip()
        if not symbol:
            return
        self.seen_variables.add(symbol)
        record = {
            "agent": agent_name,
            "symbol": symbol,
            "family": str(eval_result.get("family", "")).strip(),
            "signature": str(eval_result.get("signature", "")).strip(),
            "score": eval_result.get("score", 0),
            "issues": list(eval_result.get("issues", [])),
            "parsed": dict(eval_result.get("parsed", {})),
            "is_valid": bool(eval_result.get("is_valid", False)),
        }
        self.variable_history.append(record)
        if eval_result.get("is_valid"):
            self.stable_variables.add(symbol)
            family = self._normalize_family(str(eval_result.get("family", "")))
            if family:
                self.family_counts[family] = self.family_counts.get(family, 0) + 1
            signature = str(eval_result.get("signature", "")).strip()
            if signature:
                self.signature_counts[signature] = self.signature_counts.get(signature, 0) + 1

    def get_stable_variables(self) -> List[str]:
        return sorted(self.stable_variables)

    def get_seen_variables(self) -> List[str]:
        return sorted(self.seen_variables)

    def _finalize_turn(self, turn: int) -> None:
        """Finalize the debate turn with deterministic tool-first validation and LLM last-resort fallback."""
        locked_symbol = self._locked_variable_symbol()
        candidate_text = self._locked_variable_text()
        if not candidate_text:
            candidate_text = (
                self._last_by("Hermes")
                or self._last_by("Synthetiseur")
                or self._last_by("Aurelius")
                or self._last_by("Basilide")
                or ""
            )
        parsed = self._parse_variable_block(candidate_text)
        symbol = self._normalize_symbol(str(parsed.get("symbol", "")).strip()) or locked_symbol
        if not symbol:
            return
        
        # --- DETERMINISTIC TOOL-FIRST VALIDATION GATES ---
        # Gate 1: Symbol presence and basic structure
        basic_checks: List[str] = []
        if not symbol:
            basic_checks.append("symbole absent")
        approved = bool(parsed.get("definition") and parsed.get("unit") and parsed.get("measure") and parsed.get("role"))
        if not basic_checks and not approved:
            basic_checks.append("champs obligatoires absents")
        
        # Gate 2: Family validation
        family = self._normalize_family(str(parsed.get("family", ""))) or self._infer_family_from_symbol(symbol)
        family_checks: List[str] = []
        if not family or family == "...":
            family_checks.append("famille absente ou générique")
        if family and family not in VALID_FAMILIES:
            family_checks.append("famille invalide")
        if family and self._is_family_saturated(family) and symbol not in {self._normalize_symbol(k) for k in self._get_approved_variables().keys()}:
            family_checks.append("famille déjà dense")
        
        # Gate 3: Semantic coherence checks
        semantic_checks: List[str] = []
        symbol_norm = self._normalize_symbol(symbol)
        # Ambiguous symbol check
        if symbol_norm and self._is_ambiguous_symbol(symbol_norm):
            semantic_checks.append("symbole ambigu interdit")
        # Semantic drift check
        if self._semantic_drift(symbol, family, str(parsed.get("unit", "")), str(parsed.get("definition", ""))):
            semantic_checks.append("dérive sémantique")
        # Family-unit coherence check
        if self._family_unit_incoherent(family, str(parsed.get("unit", "")), str(parsed.get("definition", ""))):
            semantic_checks.append("famille/unité incohérentes")
        # Verrou sémantique check
        try:
            existing_payload = self.shared.get_variable_payload(symbol_norm) if symbol_norm else {}
            if existing_payload:
                existing_family = self._normalize_family(str(existing_payload.get("family", "")))
                existing_unit = str(existing_payload.get("unit", "")).strip().lower()
                current_unit = str(parsed.get("unit", "")).strip().lower()
                if existing_family and family and existing_family != family:
                    semantic_checks.append("verrou sémantique violé")
                if existing_unit and current_unit and existing_unit != current_unit:
                    semantic_checks.append("verrou d'unité violé")
        except Exception:
            pass
        
        # Gate 4: Link causal coverage (variable-level: links must mention the locked symbol)
        link_checks: List[str] = []
        raw_links = self._extract_links(candidate_text) or []
        links = [l for l in raw_links if self._is_causal_link(l)]
        link_symbols = set(self._extract_link_symbols(links))
        if symbol_norm and link_symbols and symbol_norm not in link_symbols:
            link_checks.append(f"variable absente des liens: {symbol_norm}")
        # Check for minimum 2 causal links
        if len(links) < 2:
            link_checks.append("moins de 2 liens causaux valides")
        # Check for contradictions
        contradictions = self._detect_link_contradictions(links)
        if contradictions:
            link_checks.append(f"contradictions causales: {', '.join(contradictions)}")

        # Compile all issues
        all_issues: List[str] = []
        all_issues.extend(basic_checks)
        all_issues.extend(family_checks)
        all_issues.extend(semantic_checks)
        all_issues.extend(link_checks)
        
        # Determine approval status
        repair_required = bool(family_checks or semantic_checks or link_checks or basic_checks)
        fallback_used = any("dérive" in issue or "ambigu" in issue for issue in all_issues)
        if fallback_used:
            repair_required = True
        
        stable_parent = bool(approved and not repair_required and not fallback_used)
        memory_decision = "approved_stable" if stable_parent else ("temporary_fallback_only" if fallback_used else "needs_repair")
        status = "approved" if approved and not repair_required and not fallback_used else "partial"
        
        # --- DETERMINISTIC REPAIR ONLY (no LLM in the finalize path) ---
        # When deterministic validation fails, repair strictly with the
        # deterministic fallback generator. The LLM is never consulted here:
        # validators and finalization are pure deterministic gates.
        llm_fallback_triggered = False
        llm_fallback_text = ""
        if repair_required or fallback_used:
            if self.cfg.enable_role_guard_fallbacks:
                fallback_text = self._build_deterministic_fallback_variable()
                fallback_result = self._validate_hermes_variable(fallback_text)
                if fallback_result.get("accepted"):
                    candidate_text = fallback_text
                    parsed = self._parse_variable_block(candidate_text)
                    symbol = self._normalize_symbol(str(parsed.get("symbol", "")).strip()) or symbol
                    status = "partial"
                    print("[FALLBACK] variable déterministe de secours appliquée (finalize, no LLM)")
                else:
                    print("[FALLBACK] réparation déterministe refusée, tour classé needs_repair")
        
        # --- REGISTER VARIABLE IF STABLE ---
        candidate_text_final = llm_fallback_text if llm_fallback_triggered and llm_fallback_text else candidate_text
        parsed_final = self._parse_variable_block(candidate_text_final)
        symbol_final = self._normalize_symbol(str(parsed_final.get("symbol", "")).strip()) or symbol
        if not symbol_final:
            return
        
        validator_text = self._last_by("VariableValidator") or self._last_by("Sentinelle") or ""
        decision = self._parse_decision(validator_text)
        lowered = decision.lower()
        approved_symbols = {self._normalize_symbol(k) for k in self._get_approved_variables().keys()}
        family_final = self._normalize_family(str(parsed_final.get("family", ""))) or self._infer_family_from_symbol(symbol_final)
        semantic_blockers: List[str] = []
        if self._is_ambiguous_symbol(symbol_final):
            semantic_blockers.append("symbole ambigu interdit")
        if self._pending_conflicts(symbol_final, family_final, str(parsed_final.get("unit", "")), str(parsed_final.get("definition", ""))):
            semantic_blockers.append("variable en cours redéfinie")
        if self._semantic_drift(symbol_final, family_final, str(parsed_final.get("unit", "")), str(parsed_final.get("definition", ""))):
            semantic_blockers.append("dérive sémantique")
        approved = (
            "rejet" not in lowered
            and "dupli" not in lowered
            and self._normalize_symbol(symbol_final) not in approved_symbols
            and not self._is_family_saturated(family_final)
            and not semantic_blockers
            and bool(parsed_final.get("definition"))
            and bool(parsed_final.get("unit"))
            and bool(parsed_final.get("measure"))
            and bool(parsed_final.get("role"))
        )

        required_next: List[str] = []
        for item in [self._last_by("Archiviste"), validator_text]:
            for line in (item or "").splitlines():
                low = line.lower().strip()
                if low.startswith("à reprendre") or low.startswith("a reprendre") or low.startswith("à vérifier") or low.startswith("a verifier"):
                    required_next.extend([
                        x.strip(" -*•")
                        for x in line.split(":", 1)[-1].split(",")
                        if x.strip(" -*•")
                    ])

        shared_var = SharedVariable(
            name=symbol_final,
            definition=str(parsed_final.get("definition", "")),
            unit=str(parsed_final.get("unit", "")),
            measure=str(parsed_final.get("measure", "")),
            role=str(parsed_final.get("role", "")),
            links=list(parsed_final.get("links", []) or []),
            approved=approved,
            source_turn=turn,
            source_agent="Hermes" if locked_symbol else ("Synthetiseur" if self._last_by("Synthetiseur") else "Aurelius"),
            validation_summary=(validator_text or "")[:1800],
            required_next=list(dict.fromkeys(required_next))[:8],
            family=family_final,
            micro_equation=self._extract_named_field(candidate_text_final, "Micro-équation") or self._extract_named_field(candidate_text_final, "Micro-equation"),
            experiment=self._extract_named_field(candidate_text_final, "Expérience") or self._extract_named_field(candidate_text_final, "Experience"),
            status="approved" if approved else "candidate",
        )
        self.shared.add_variable(shared_var)
        self.memory_store.register_variable(
            name=symbol_final,
            turn=turn,
            kind=str(parsed_final.get("family", "")),
            description=str(parsed_final.get("definition", "")),
            status="stabilised" if approved else "active",
        )
        self.memory_store.add_validation(
            element_name=symbol_final,
            element_type="variable",
            turn=turn,
            status="approved" if approved else "partial",
            reason=(validator_text or "")[:500],
        )
        self.memory_store.add_validation(
            element_name="variable",
            element_type="final",
            turn=turn,
            status=decision or ("approuvée" if approved else "à reprendre"),
            reason=(validator_text or "")[:500],
        )
        fusion = self.shared.suggest_fusion() or ""
        self.shared.add_debate_summary(
            "variable",
            turn,
            f"{symbol_final} | approved={approved} | family={family_final or '-'} | score_global={self.shared.compute_variable_turn_score(shared_var)} | issues={'; '.join(dict.fromkeys(all_issues))} | llm_fallback={llm_fallback_triggered}",
        )
        self.state["last_variable"] = symbol_final
        self.state["last_variable_signature"] = self._variable_signature(
            symbol=symbol_final,
            family=family_final,
            unit=str(parsed_final.get("unit", "")),
            definition=str(parsed_final.get("definition", "")),
            measure=str(parsed_final.get("measure", "")),
        )
        self.state["locked_variable"] = ""
        self.memory_store.register_variable(
            name=symbol,
            turn=turn,
            kind=str(parsed.get("family", "")),
            description=str(parsed.get("definition", "")),
            status="stabilised" if approved else "active",
        )
        self.memory_store.add_validation(
            element_name=symbol,
            element_type="variable",
            turn=turn,
            status="approved" if approved else "partial",
            reason=(validator_text or "")[:500],
        )
        self.shared.add_final_validation("variable", turn, (validator_text or "")[:1200], decision or ("approuvée" if approved else "à reprendre"))
        score_global = self.shared.compute_variable_turn_score(shared_var)
        self.shared.record_turn_metric(
            kind="variable",
            turn=turn,
            element=symbol,
            signature=str(self._variable_signature(
                symbol=symbol,
                family=family,
                unit=str(parsed.get("unit", "")),
                definition=str(parsed.get("definition", "")),
                measure=str(parsed.get("measure", "")),
            )),
            score=score_global,
            approved=approved,
            note=(validator_text or "")[:240],
        )
        stag = self.shared.detect_stagnation("variable")
        if stag.get("stagnant"):
            reason_bits = []
            if stag.get("repeated_signature"):
                reason_bits.append("signature répétée")
            if stag.get("no_recent_approved"):
                reason_bits.append("aucune validation récente")
            self.shared.register_stagnation_event(kind="variable", turn=turn, reason=", ".join(reason_bits) or "stagnation")
        issue_bits = []
        if self.variable_history:
            issue_bits.extend(self.variable_history[-1].get('issues', []))
        issue_bits.extend(semantic_blockers)
        self.shared.add_debate_summary(
            "variable",
            turn,
            f"{symbol} | approved={approved} | family={family or '-'} | score_global={score_global} | issues={'; '.join(dict.fromkeys(issue_bits))}",
        )
        self.state["last_variable"] = symbol
        self.state["last_variable_signature"] = self._variable_signature(
            symbol=symbol,
            family=family,
            unit=str(parsed.get("unit", "")),
            definition=str(parsed.get("definition", "")),
            measure=str(parsed.get("measure", "")),
        )
        self.state["locked_variable"] = ""
        self.state["locked_variable_text"] = ""
