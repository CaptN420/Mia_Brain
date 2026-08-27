from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Dict, List, Optional

from memory_store import DebateState, MemoryStore
from openai_client import OpenAIClient
from shared_memory import SharedResearchMemory
from prompt_guard import prompt_injection_guardrails, sanitize_items, sanitize_untrusted_text, wrap_untrusted_block
from action_monitor import SecurityStop, inspect_agent_output, log_effect
from themes import random_theme


def _build_md_field_re(field_name: str) -> re.Pattern:
    return re.compile(
        rf"""
        ^\s*
        (?:[#>\-\*\s]*)?
        (?:\*\*)?
        {re.escape(field_name)}
        \s*:\s*
        (?:\*\*)?
        (.+?)
        \s*$
        """,
        re.IGNORECASE | re.MULTILINE | re.VERBOSE,
    )


VAR_SYMBOL_RE = re.compile(
    r"""
    ^\s*
    (?:[#>\-\*\s]*)?
    (?:\*\*)?
    Variable
    \s*:\s*
    (?:\*\*)?
    \**([A-Za-zÀ-ÿΔτ][A-Za-z0-9_À-ÿΔτ]*)\**
    \s*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

VAR_FAMILY_RE = _build_md_field_re("Famille")
VAR_DEF_RE = _build_md_field_re("Définition")
VAR_UNIT_RE = _build_md_field_re("Unité")
VAR_MEASURE_RE = _build_md_field_re("Mesure")
VAR_ROLE_RE = _build_md_field_re("Rôle causal")
VAR_ROLE_FALLBACK_RE = _build_md_field_re("Rôle")
VAR_CONTEXT_RE = _build_md_field_re("Contexte chimique")

EQ_RE = re.compile(
    r"""
    ^\s*
    (?:[#>\-\*\s]*)?
    (?:\*\*)?
    (?:Équation|Equation)
    \s*:\s*
    (?:\*\*)?
    (.+?)
    \s*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

OBJECT_RE = _build_md_field_re("Objet calculé")
ARCH_RE = _build_md_field_re("Architecture choisie")
ARCH_FALLBACK_RE = _build_md_field_re("Architecture")
TYPE_RE = _build_md_field_re("Type de loi")


class BaseDebateCore:
    debate_kind = "base"
    turn_prefix = "tour"
    sequence: List[str] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.session_dir = Path(cfg.session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.client = OpenAIClient(
            api=cfg.api_url,
            request_timeout=cfg.request_timeout,
            max_retries=cfg.max_api_retries,
            retry_backoff_seconds=cfg.retry_backoff_seconds,
            api_key=cfg.api_key,
        )
        self.shared = SharedResearchMemory(self.session_dir)
        self.memory_store = MemoryStore(self.session_dir)
        theme, question = random_theme()
        self.theme = theme
        self.question = question
        self.state = {
            "turn": 0,
            "locked_equation": "",
            "equation_repeat_count": 0,
            "last_equation": "",
        }
        self.debate_state = DebateState(theme=theme, question=question)
        self.log_path = self.session_dir / f"{self.debate_kind}_debate_log.txt"
        self.last_parsed_variable: Dict[str, object] = {}
        self.last_parsed_equation: Dict[str, object] = {}
        self.fallback_events: List[Dict[str, str]] = []
        # Captn bridge: thinkers + workers feed the debate (deterministic)
        self.captn_bridge = None
        try:
            from captn_bridge import CaptnBridge
            self.captn_bridge = CaptnBridge(cfg)
            print(f"[BRIDGE] Captn connecté: {self.captn_bridge.status()}")
        except Exception as exc:
            print(f"[BRIDGE] Captn indisponible: {type(exc).__name__}: {exc}")


    def _record_fallback_metadata(self, speaker: str, *, level: str, recovery_type: str, reason: str, promotable_as_parent: bool = False) -> None:
        event = {
            "speaker": str(speaker or "").strip(),
            "level": str(level or "").strip() or "fallback_temporary",
            "recovery_type": str(recovery_type or "").strip() or "fallback",
            "reason": str(reason or "").strip() or "unknown",
            "promotable_as_parent": "yes" if promotable_as_parent else "no",
            "turn": str(self.state.get("turn", 0)),
        }
        self.fallback_events.append(event)
        self.fallback_events = self.fallback_events[-80:]
        self.state["last_fallback_event"] = dict(event)

    # ----------------------- run loop -----------------------
    def run(self, turns: int = 1) -> None:
        turns = max(1, int(turns))
        print(f"{self.debate_kind.capitalize()} debate started")
        for _ in range(turns):
            self.state["turn"] = int(self.state.get("turn", 0)) + 1
            turn = int(self.state["turn"])
            print(f"--- {self.turn_prefix} {turn} ---")
            for speaker in self.sequence:
                self._run_agent_turn(speaker)
            self._call_finalize(turn)
            self._record_turn_metric(turn)
            self._flush_memory()

    # ------------------------------------------------------------------
    # Deterministic agent proposals (no LLM)
    # ------------------------------------------------------------------
    def _deterministic_proposal(self, speaker: str) -> str:
        """Generate a role-appropriate deterministic proposal for any agent.

        Uses the turn-indexed template variants so successive turns differ,
        then tags the output with the speaking role. Never calls the LLM.
        """
        turn = int(self.state.get("turn", 1)) or 1
        base = self._build_generic_fallback_text(speaker if speaker not in {"Hermes", "Aurelius"} else "")
        # Role-specific framing line, purely static
        framing = {
            "Hermes": "Exploration rapide : piste nouvelle et mesurable.",
            "Aurelius": "Structuration principale : formalisation testable.",
            "Basilide": "Enrichissement : mécanisme physico-chimique et protocole.",
            "Chymicus": "Critique réparatrice : défaut principal et correction minimale.",
            "Sentinelle": "Contrôle structurel : unités, symboles, mesurabilité.",
            "Synthetiseur": "Synthèse finale : meilleure version compacte et exploitable.",
            "Archiviste": "Décision mémoire : stable / à reprendre / rejet.",
            "Hermetica": "Cohérence hermétique déterministe.",
            "Reviseur": "Révision de complétude déterministe.",
        }.get(speaker, f"Proposition déterministe ({speaker}).")
        parts = [f"Agent : {speaker}", f"Tour : {turn}", f"Cadre : {framing}"]
        # Inject captn thinkers/workers knowledge (deterministic pipeline)
        if self.captn_bridge is not None:
            try:
                kb = self.captn_bridge.knowledge_block(topic=self.theme)
                if kb:
                    parts.append(kb)
            except Exception:
                pass
        parts.append(base)
        return "\n".join(parts) + "\n"

    def _run_agent_turn(self, speaker: str) -> None:
        # FULL DETERMINISTIC MODE (default): agents never call the LLM.
        if not getattr(self.cfg, "llm_enabled", False):
            text = self._deterministic_proposal(speaker)
            print(f"[DETERMINISTIC] {speaker} proposition générée sans LLM")
            text = self._finalize_agent_text(speaker, text, reason="proposition déterministe")
            inspect_agent_output(text, speaker=speaker, session_dir=str(self.session_dir))
            eval_result = self._evaluate_agent_output(speaker, text)
            status = "OK" if not eval_result.get("issues") else "FLAG"
            print(
                f"[AGENT {status}] {speaker} score={eval_result.get('score', 0)} "
                f"(deterministic, no LLM) issues={'; '.join(eval_result.get('issues', []))}"
            )
            text_for_memory = sanitize_untrusted_text(text, max_chars=3000)
            print(f"{speaker}: {text}")
            self.debate_state.add(speaker, text_for_memory, max_history_messages=self.cfg.max_history_messages)
            log_effect('agent_turn_completed', target=speaker, risk='low', session_dir=str(self.session_dir), details={'chars': len(text_for_memory)})
            self._append_log(speaker, text_for_memory)
            if hasattr(self, "process_agent_output"):
                try:
                    getattr(self, "process_agent_output")(speaker, text)
                except Exception as exc:
                    err = f"[PROCESS OUTPUT ERROR] {speaker}: {type(exc).__name__}: {exc}"
                    print(err)
                    try:
                        self._append_log("SYSTEM", err)
                    except Exception:
                        pass
                    raise
            return

        model = self._model_for(speaker)
        num_predict = self._num_predict_for(speaker)
        temperature = self._temperature_for(speaker)
        print(f"[DEBUG] {speaker} utilise modèle: {model}")
        prompt = self._compose_prompt(speaker)
        text = self.client.ask(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=num_predict,
        )
        if not text or self._is_effectively_empty(text):
            print(f"[CRITICAL] {speaker} returned empty or useless response. Checking connection...")
            try:
                text = self.client.ask(
                    model=model,
                    messages=[{"role": "user", "content": prompt + "\n\n(Please provide a detailed and technical response.)"}],
                    temperature=temperature,
                    max_tokens=num_predict,
                )
                if not text or self._is_effectively_empty(text):
                    print(f"[FATAL] {speaker} is completely unresponsive. Check if LM Studio API is running on {self.cfg.api_url}")
                    raise RuntimeError(f"LM Studio API at {self.cfg.api_url} is not returning valid text for model {model}")
            except Exception as e:
                print(f"[FATAL ERROR] Failed to reach LM Studio API during {speaker} turn: {e}")
                raise RuntimeError(f"Connection to LM Studio API failed: {e}")
        
        text = self._finalize_agent_text(speaker, text, reason="réponse initiale")
        inspect_agent_output(text, speaker=speaker, session_dir=str(self.session_dir))
        eval_result = self._evaluate_agent_output(speaker, text)
        if self._should_retry(eval_result):
            print(f"[WARN] {speaker} tentative 1 rejetée, régénération ciblée.")
            retry_prompt = prompt + "\n\n" + self._targeted_retry_instruction(eval_result)
            retry_text = self.client.ask(
                model=model,
                messages=[{"role": "user", "content": retry_prompt}],
                temperature=temperature,
                max_tokens=min(num_predict + 35, 220),
            )
            retry_text = self._finalize_agent_text(speaker, retry_text, reason="réponse de régénération")
            retry_eval = self._evaluate_agent_output(speaker, retry_text)
            if retry_eval.get("score", -1) >= eval_result.get("score", -1):
                text = retry_text
                eval_result = retry_eval
        status = "OK" if not eval_result.get("issues") else "FLAG"
        print(
            f"[AGENT {status}] {speaker} score={eval_result.get('score', 0)} "
            f"predict={num_predict} issues={'; '.join(eval_result.get('issues', []))}"
        )
        text_for_memory = sanitize_untrusted_text(text, max_chars=3000)
        print(f"{speaker}: {text}")
        self.debate_state.add(speaker, text_for_memory, max_history_messages=self.cfg.max_history_messages)
        log_effect('agent_turn_completed', target=speaker, risk='low', session_dir=str(self.session_dir), details={'chars': len(text_for_memory)})
        self._append_log(speaker, text_for_memory)
        if hasattr(self, "process_agent_output"):
            try:
                getattr(self, "process_agent_output")(speaker, text)
            except Exception as exc:
                err = f"[PROCESS OUTPUT ERROR] {speaker}: {type(exc).__name__}: {exc}"
                print(err)
                try:
                    self._append_log("SYSTEM", err)
                except Exception:
                    pass
                raise

    def _call_finalize(self, turn: int) -> None:
        finalize = getattr(self, "_finalize_turn", None)
        if not finalize:
            return
        try:
            sig = inspect.signature(finalize)
            if len(sig.parameters) == 1:
                finalize(turn)
            else:
                finalize()
        except TypeError:
            try:
                finalize(turn)
            except TypeError:
                finalize()

    def _flush_memory(self) -> None:
        try:
            self.memory_store.save_all()
        except Exception:
            pass

    # ----------------------- config routing -----------------------
    def _model_for(self, speaker: str) -> str:
        if hasattr(self.cfg, "model_for_agent"):
            try:
                return str(self.cfg.model_for_agent(speaker))
            except Exception:
                pass
        key = speaker.lower().replace("é", "e").replace(" ", "").replace("_", "")
        mapping = {
            "aurelius": self.cfg.model_aurelius,
            "basilide": self.cfg.model_basilide,
            "chymicus": self.cfg.model_chymicus,
            "archiviste": self.cfg.model_archiviste,
            "sentinelle": self.cfg.model_sentinelle,
            "hermes": self.cfg.model_hermes,
            "hermesvalidator": getattr(self.cfg, "model_hermes_validator", self.cfg.model_variable_validator),
            "synthetiseur": self.cfg.model_synthetiseur,
            "reviseur": self.cfg.model_reviseur,
            "hermetica": self.cfg.model_hermetica,
            "variablevalidator": self.cfg.model_variable_validator,
            "equationvalidator": self.cfg.model_equation_validator,
            "finalvalidator": self.cfg.model_final_validator,
            "aureliusvalidation": self.cfg.model_variable_validator,
            "basilidevalidation": self.cfg.model_variable_validator,
        }
        return mapping.get(key, self.cfg.model_aurelius)

    def _num_predict_for(self, speaker: str) -> int:
        if speaker == "Hermes":
            return self.cfg.num_predict_hermes
        if speaker in {"HermesValidator", "VariableValidator", "EquationValidator", "FinalValidator", "AureliusValidation", "BasilideValidation", "Hermetica", "Reviseur"}:
            return self.cfg.num_predict_validation
        if speaker in {"Sentinelle"}:
            return self.cfg.num_predict_sentinelle
        if speaker in {"Archiviste"}:
            return self.cfg.num_predict_archive
        if speaker in {"Synthetiseur"}:
            return self.cfg.num_predict_synth
        if speaker in {"Chymicus", "Reviseur"}:
            return self.cfg.num_predict_support
        return self.cfg.num_predict_debate

    def _temperature_for(self, speaker: str) -> float:
        if speaker == "Hermes":
            return self.cfg.temperature_hermes
        if speaker in {"HermesValidator", "Chymicus", "Sentinelle", "Archiviste", "VariableValidator", "EquationValidator", "FinalValidator", "AureliusValidation", "BasilideValidation", "Hermetica", "Reviseur"}:
            return self.cfg.temperature_support
        return self.cfg.temperature_debate

    # ----------------------- prompts/history -----------------------
    def _compose_prompt(self, speaker: str) -> str:
        theme = sanitize_untrusted_text(self.theme, max_chars=600)
        question = sanitize_untrusted_text(self.question, max_chars=900)
        return "\n\n".join([
            prompt_injection_guardrails(),
            wrap_untrusted_block("THEME", theme),
            wrap_untrusted_block("QUESTION", question),
            f"Agent : {speaker}",
        ])

    def _turn_metric_key(self) -> str:
        if self.debate_kind == "variable":
            symbol = str(self.last_parsed_variable.get("symbol", "")).strip()
            if symbol:
                return symbol
            for speaker in ["Synthetiseur", "Aurelius", "Basilide", "Hermes"]:
                text = self._last_by(speaker)
                if text:
                    symbol = self._extract_variable_symbol(text)
                    if symbol:
                        return symbol
            return ""
        eq = str(self.last_parsed_equation.get("equation", "")).strip()
        if eq:
            return eq
        for speaker in ["Synthetiseur", "Aurelius", "Basilide"]:
            text = self._last_by(speaker)
            if text:
                eq = self._extract_equation_line(text)
                if eq:
                    return eq
        return ""

    def _score_global_estimate(self) -> float:
        recent = self.debate_state.history[-max(1, len(self.sequence)):]
        uniq = len({speaker for speaker, _ in recent})
        filled = sum(1 for _, text in recent if (text or "").strip())
        return round(float(uniq + filled) / 2.0, 2)

    def _record_turn_metric(self, turn: int) -> None:
        try:
            self.shared.record_turn_metric(
                kind=self.debate_kind,
                turn=turn,
                score_global=self._score_global_estimate(),
                key=self._turn_metric_key(),
            )
        except Exception:
            pass

    def _last_n_keys(self, n: int = 6) -> List[str]:
        try:
            metrics = self.shared.get_turn_metrics(kind=self.debate_kind, limit=max(1, int(n))) or []
        except Exception:
            metrics = []
        out: List[str] = []
        for item in metrics:
            key = str(item.get("key", "")).strip()
            if key:
                out.append(key)
        return out[-max(1, int(n)):]

    def _diversity_pressure_level(self) -> int:
        keys = self._last_n_keys(6)
        if not keys:
            return 0
        last = keys[-1]
        repeat_count = sum(1 for key in keys if key == last)
        if repeat_count >= 4:
            return 3
        if repeat_count >= 3:
            return 2
        if repeat_count >= 2:
            return 1
        return 0

    def _repeat_pressure_block(self) -> str:
        keys = self._last_n_keys(6)
        if not keys:
            return ""
        last = keys[-1]
        repeat_count = sum(1 for key in keys if key == last)
        if repeat_count < 2:
            return ""
        level = self._diversity_pressure_level()
        family_hint = "changer de famille structurelle" if self.debate_kind in {"equation", "mutation"} else "changer de famille de variable"
        return (
            "PRESSION ANTI-RÉPÉTITION : la même structure revient trop souvent.\n"
            f"- signature récente répétée : {last}\n"
            f"- répétitions observées : {repeat_count}\n"
            f"- niveau de pression diversité : {level}\n"
            f"- obligation : {family_hint}, pas seulement ajouter un terme cosmétique.\n"
            "- interdit : recopier la même architecture avec une variable renommée."
        )

    def _stagnation_block(self) -> str:
        try:
            info = self.shared.detect_stagnation(kind=self.debate_kind, window=4)
            stagnant = bool(info.get("stagnant") or info.get("stagnating"))
        except Exception:
            stagnant = False
        if not stagnant:
            return ""
        return (
            "STAGNATION DÉTECTÉE : les derniers tours ressemblent trop entre eux.\n"
            "Obligation : mutation visible, nouvelle architecture ou nouveau terme causal.\n"
            "Interdiction : simple reformulation cosmétique.\n"
            "Réparation attendue : changer de famille, de mécanisme ou de variable porteuse."
        )

    def _score_guidance_block(self) -> str:
        return (
            "GUIDE DE SCORE GLOBAL : améliorer nouveauté, cohérence, testabilité et lien mémoire.\n"
            "Une sortie trop proche des derniers tours sera pénalisée."
        )

    def _fusion_guidance_block(self) -> str:
        try:
            suggestion = self.shared.suggest_fusion()
        except Exception:
            suggestion = None
        if not suggestion:
            return ""
        return f"PISTE DE FUSION : {suggestion}"

    def _last_by(self, speaker: str) -> str:
        for spk, text in reversed(self.debate_state.history):
            if spk == speaker:
                return text
        return ""

    def _history_block(self, speakers: Optional[List[str]] = None, limit: int = 8) -> str:
        items = self.debate_state.history[-limit:]
        if speakers:
            items = [(s, t) for s, t in items if s in speakers]
        lines = [f"{sanitize_untrusted_text(s, max_chars=40)}: {sanitize_untrusted_text(t, max_chars=500)}" for s, t in items[-limit:]]
        return wrap_untrusted_block("HISTORIQUE", "\n".join(lines)) if lines else ""

    def _shared_block(self) -> str:
        approved_vars = self.shared.get_approved_variables()
        latest_eq = self.shared.get_latest_equation() or self.shared.get_latest_partial_equation() or {}
        parts: List[str] = []
        if approved_vars:
            vars_clean = sanitize_items(sorted(approved_vars.keys())[:12], max_chars_each=60)
            parts.append("Variables approuvées : " + ", ".join(vars_clean))
        if latest_eq.get("equation"):
            parts.append("Dernière équation : " + sanitize_untrusted_text(latest_eq["equation"], max_chars=240))
        recent_links = sanitize_items(self.shared.get_recent_links()[:6], max_chars_each=160)
        if recent_links:
            parts.append("Liens récents : " + " | ".join(recent_links))
        return wrap_untrusted_block("MEMOIRE_PARTAGEE", "\n".join(parts)) if parts else ""

    def _memory_block(self) -> str:
        try:
            known = sanitize_items(list(self.memory_store.variables.keys())[:10], max_chars_each=60)
        except Exception:
            known = []
        return wrap_untrusted_block("MEMOIRE_LOCALE", "Variables mémoire : " + ", ".join(known)) if known else ""

    # ----------------------- normalize/fallback helpers -----------------------
    def _safe_strip(self, text) -> str:
        if text is None:
            return ""
        return str(text).strip()

    def _normalize_agent_output(self, text) -> str:
        s = self._safe_strip(text)
        if not s:
            return ""
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        s = re.sub(r"^```(?:text|md|markdown|json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    def _is_effectively_empty(self, text) -> bool:
        s = self._normalize_agent_output(text)
        if not s:
            return True
        lowered = s.lower()
        weak_values = {
            "none",
            "null",
            "vide",
            "aucun",
            "aucune",
            "n/a",
            "-",
            ".",
            "diagnostic :",
            "## diagnostic :",
        }
        return lowered in weak_values

    def _speaker_allows_equation_fallback(self, speaker: str) -> bool:
        if self.debate_kind == "equation":
            if not getattr(self.cfg, "allow_generic_equation_fallback", False):
                return speaker in {"Aurelius"}
        if self.debate_kind == "mutation":
            if not getattr(self.cfg, "allow_generic_mutation_fallback", False):
                return speaker in {"Aurelius"}
        return True

    def _build_role_guard_text(self, speaker: str, reason: str = "") -> str:
        because = (reason or "sortie vide ou inutilisable").strip()
        if self.debate_kind == "equation":
            if speaker in {"Basilide", "Chymicus"}:
                return (
                    f"Diagnostic : {because}\n"
                    "Remplacement équation : aucun\n"
                    "Verdict : à réparer"
                )
            if speaker == "Hermes":
                return (
                    f"Diagnostic : {because}\n"
                    "Variable ou terme ajouté : aucun\n"
                    "Gain : aucune proposition exploitable\n"
                    "Testabilité : à reprendre"
                )
            if speaker == "Sentinelle":
                return (
                    "Équation : -\n"
                    "Check-list :\n"
                    "- unités : à vérifier\n"
                    "- mesurabilité : à vérifier\n"
                    "- cohérence : sortie vide\n"
                    "- objet calculé : à préciser\n"
                    "Verdict structurel : à réparer"
                )
            if speaker == "Synthetiseur":
                return (
                    "Statut : à réparer\n"
                    "Élément repris : aucun\n"
                    f"Défaut ancien : {because}\n"
                    "Correction : synthèse absente, conserver l'équation verrouillée sans en inventer une autre\n"
                    "Gain : aucun\n"
                    "Objet calculé : -\n"
                    "Type de loi : -\n"
                    "Architecture choisie : -\n"
                    "Justification : la synthèse n'a pas produit de sortie exploitable\n"
                    "Équation : -\n"
                    "Définitions :\n"
                    "- à reprendre\n"
                    "Mécanisme causal : à reprendre\n"
                    "Liens :\n"
                    "- à reprendre\n"
                    "- à reprendre\n"
                    "Expérience : à reprendre\n"
                    "Remarque : garde anti-fallback"
                )
            if speaker == "Archiviste":
                return (
                    "Équation : -\n"
                    "Statut : à réparer\n"
                    "Décision mémoire : aucune promotion mémoire, secours refusé\n"
                    "Pourquoi : sortie vide ou non exploitable\n"
                    "À reprendre : synthèse complète, équation explicite\n"
                    "À vérifier plus tard : stabilité parent"
                )
            if speaker == "FinalValidator":
                return (
                    "Validation : insuffisante\n"
                    "Point fort : aucun\n"
                    "Point faible : sortie vide ou incomplète\n"
                    "Test conseillé : régénérer avant validation\n"
                    "Décision : à réparer"
                )
        if self.debate_kind == "mutation":
            if speaker in {"Basilide", "Chymicus"}:
                return (
                    f"Diagnostic : {because}\n"
                    "Remplacement équation : aucun\n"
                    "Verdict : à réparer"
                )
            if speaker == "Synthetiseur":
                return (
                    "Statut : à réparer\n"
                    "Élément repris : parent verrouillée\n"
                    f"Défaut ancien : {because}\n"
                    "Correction : aucune mutation sûre disponible\n"
                    "Gain : aucun\n"
                    "Objet calculé : -\n"
                    "Type de loi : mutation bloquée\n"
                    "Architecture choisie : parent inchangée\n"
                    "Justification : garde anti-fallback pour éviter une fausse mutation\n"
                    "Équation : -\n"
                    "Définitions :\n"
                    "- à reprendre\n"
                    "Mécanisme causal : à reprendre\n"
                    "Liens :\n"
                    "- à reprendre\n"
                    "- à reprendre\n"
                    "Expérience : à reprendre\n"
                    "Remarque : garde anti-fallback"
                )
        return ""

    def _build_generic_fallback_text(self, speaker: str) -> str:
        turn = int(self.state.get("turn", 1))
        var_variants = [
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
        ]
        eq_variants = [
            "Ndot = k * A * ΔC",
            "Ndot = J * A",
            "Ndot = (D_eff * A * ΔC) / L",
            "Ndot = (k * A * ΔC) / (1 + alpha * ΔC)",
        ]

        if self.debate_kind == "variable":
            symbol, family, definition, unit, measure, role, links = var_variants[(turn - 1) % len(var_variants)]
            return (
                f"Variable : {symbol}\n"
                f"Famille : {family}\n"
                f"Définition : {definition}\n"
                f"Unité : {unit}\n"
                f"Mesure : {measure}\n"
                f"Rôle causal : {role}\n"
                "Liens :\n"
                f"- {links[0]}\n"
                f"- {links[1]}"
            )

        if self.debate_kind == "equation":
            eq = eq_variants[(turn - 1) % len(eq_variants)]
            return (
                "Statut : nouvelle\n"
                "Objet calculé : débit net de transfert\n"
                "Type de loi : transport\n"
                "Architecture choisie : relation explicite entre gradient, surface et limitation\n"
                f"Équation : {eq}\n"
                "Définitions :\n"
                "- Ndot : débit net de transfert\n"
                "- A : surface d'échange active\n"
                "Mécanisme causal : le débit augmente avec la force motrice et la surface active.\n"
                "Liens :\n"
                "- A augmente Ndot\n"
                "- ΔC augmente Ndot"
            )

        if self.debate_kind == "mutation":
            return (
                "Statut : nouvelle\n"
                "Élément repris : équation parent\n"
                "Défaut ancien : parent trop stable ou trop répétitif\n"
                "Correction : ajout d'un terme de perte global au parent\n"
                "Gain : mutation structurelle exploitable pour la sélection\n"
                "Objet calculé : débit net de transfert muté\n"
                "Type de loi : mutation structurée\n"
                "Architecture choisie : parent + limitation visible\n"
                "Équation : Ndot = J * A - pertes\n"
                "Définitions :\n"
                "- Ndot : débit net de transfert\n"
                "- A : surface d'échange active\n"
                "Mécanisme causal : la mutation ajoute une limitation observable.\n"
                "Liens :\n"
                "- A augmente Ndot\n"
                "- pertes diminue Ndot"
            )

        return f"[FALLBACK:{speaker}] réponse minimale"

    def _repair_variable_block_consistency(self, text: str) -> str:
        s = self._normalize_agent_output(text)
        if not s or self.debate_kind != "variable":
            return s

        symbol = self._extract_variable_symbol(s)
        definition = self._extract_variable_definition(s).lower()
        unit = self._extract_variable_unit(s)

        if symbol == "J" and ("diffus" in definition or unit == "m²/s"):
            s = re.sub(r"^\s*(?:[#>\-\*\s]*)?(?:\*\*)?Définition\s*:\s*.+$", "Définition : Flux local de matière à travers une interface ou un milieu.", s, flags=re.IGNORECASE | re.MULTILINE)
            s = re.sub(r"^\s*(?:[#>\-\*\s]*)?(?:\*\*)?Unité\s*:\s*.+$", "Unité : mol·m⁻²·s⁻¹", s, flags=re.IGNORECASE | re.MULTILINE)
        elif symbol in {"D", "D_eff"} and ("flux local" in definition or "mol·m⁻²·s⁻¹" in unit):
            s = re.sub(r"^\s*(?:[#>\-\*\s]*)?(?:\*\*)?Définition\s*:\s*.+$", "Définition : diffusivité effective d'une espèce dans un milieu", s, flags=re.IGNORECASE | re.MULTILINE)
            s = re.sub(r"^\s*(?:[#>\-\*\s]*)?(?:\*\*)?Unité\s*:\s*.+$", "Unité : m²/s", s, flags=re.IGNORECASE | re.MULTILINE)
        return s.strip()

    def _finalize_agent_text(self, speaker: str, text, reason: str = "") -> str:
        cleaned = self._normalize_agent_output(text)
        cleaned = self._repair_variable_block_consistency(cleaned)

        if not self._is_effectively_empty(cleaned):
            return cleaned

        fallback_reason = reason or 'sortie vide ou inutilisable'
        if getattr(self.cfg, "enable_role_guard_fallbacks", True):
            guard = self._build_role_guard_text(speaker, fallback_reason)
            if guard:
                print(f"[ANTI-FALLBACK] {speaker} -> garde rôle activée ({fallback_reason})")
                self._record_fallback_metadata(speaker, level="fallback_temporary", recovery_type="role_guard", reason=fallback_reason, promotable_as_parent=False)
                return self._normalize_agent_output(guard)

        if not self._speaker_allows_equation_fallback(speaker):
            guard = self._build_role_guard_text(speaker, fallback_reason)
            if guard:
                print(f"[ANTI-FALLBACK] {speaker} -> fallback générique bloqué ({fallback_reason})")
                self._record_fallback_metadata(speaker, level="fallback_temporary", recovery_type="role_guard_block", reason=fallback_reason, promotable_as_parent=False)
                return self._normalize_agent_output(guard)

        print(f"[FALLBACK] {speaker} activé -> {fallback_reason}")
        self._record_fallback_metadata(speaker, level="fallback_deterministic", recovery_type="generic_fallback", reason=fallback_reason, promotable_as_parent=False)
        return self._normalize_agent_output(self._build_generic_fallback_text(speaker))

    # ----------------------- extraction helpers -----------------------
    def _clean_inline(self, value: str) -> str:
        if not value:
            return ""
        value = value.strip()
        value = re.sub(r"^\*\*(.*?)\*\*$", r"\1", value)
        value = value.strip(" -*#>\t")
        return value.strip()

    def _extract_first(self, pattern: re.Pattern, text: str) -> str:
        if not text:
            return ""
        m = pattern.search(text)
        if not m:
            return ""
        return self._clean_inline(m.group(1))

    def _extract_named_field(self, text: str, field_name: str) -> str:
        pat = _build_md_field_re(field_name)
        return self._extract_first(pat, text)

    def _extract_defs(self, text: str) -> Dict[str, str]:
        defs: Dict[str, str] = {}
        in_defs = False
        for raw in (text or "").splitlines():
            line = raw.strip()
            if re.match(r"^(?:[#>\-\*\s]*)?(?:\*\*)?(Définitions|Definitions)(?:\*\*)?\s*:\s*$", line, re.I):
                in_defs = True
                continue
            if not in_defs:
                continue
            if not line:
                continue
            if re.match(r"^(?:[#>\-\*\s]*)?(?:\*\*)?(Mécanisme causal|Mecanisme causal|Expérience|Experience|Liens|Remarque|Remarques)(?:\*\*)?\s*:", line, re.I):
                break
            clean = re.sub(r"^[\-\*•]\s*", "", line)
            m = re.match(r"^([^:=]+)\s*[:=]\s*(.+)$", clean)
            if m:
                defs[self._clean_inline(m.group(1))] = self._clean_inline(m.group(2))
        return defs

    def _extract_links(self, text: str) -> List[str]:
        links: List[str] = []
        in_links = False
        for raw in (text or "").splitlines():
            line = raw.strip()
            if re.match(r"^(?:[#>\-\*\s]*)?(?:\*\*)?Liens(?:\*\*)?\s*:\s*$", line, re.I):
                in_links = True
                continue
            if not in_links:
                continue
            if not line:
                continue
            if re.match(r"^(?:[#>\-\*\s]*)?(?:\*\*)?(Remarque|Remarques|Expérience|Experience|Statut|Verdict|Décision|Decision)(?:\*\*)?\s*:", line, re.I):
                break
            line = re.sub(r"^[\-\*•]\s*", "", line).strip()
            if line:
                links.append(self._clean_inline(line))
        return links

    def _extract_remark(self, text: str) -> str:
        for field in ["Remarque", "Remarques", "Pourquoi"]:
            value = self._extract_named_field(text, field)
            if value:
                return value
        return ""

    # ------------------------------------------------------------------
    # Hermetica / Reviseur deterministic gates (no LLM)
    # ------------------------------------------------------------------
    def _run_hermetic_gate(self, speaker: str) -> Dict[str, object]:
        """Deterministic hermetic coherence gate.

        Hermetica : vérifie la cohérence symbolique globale (symbole/famille/
        unité/verrous) du candidat courant.
        Reviseur  : vérifie la complétude minimale et la reproductibilité
        (champs obligatoires présents, verdict explicite).
        Aucun des deux n'appelle jamais le LLM.
        """
        if self.debate_kind == "variable":
            return self._hermetic_gate_variable(speaker)
        return self._hermetic_gate_equation(speaker)

    def _hermetic_gate_variable(self, speaker: str) -> Dict[str, object]:
        candidate = (
            self._last_by("VariableValidator")
            or self._locked_variable_text()
            or self._last_by("Hermes")
            or self._last_by("Synthetiseur")
            or ""
        )
        parsed = self._parse_variable_block(candidate)
        symbol = str(parsed.get("symbol", "")).strip()
        issues: List[str] = []
        if not symbol:
            issues.append("variable absente")
        if not parsed.get("definition"):
            issues.append("définition absente")
        if not parsed.get("unit"):
            issues.append("unité absente")
        if not parsed.get("measure"):
            issues.append("mesure absente")
        if not parsed.get("role"):
            issues.append("rôle causal absent")
        verdict = "conforme" if not issues else "à réparer"
        header = "Cohérence hermétique vérifiée" if speaker == "Hermetica" else "Révision de complétude vérifiée"
        message = (
            f"{header} (déterministe, sans LLM)\n"
            f"Variable : {symbol or '-'}\n"
            f"Unité : {parsed.get('unit') or '-'}\n"
            f"Défauts : {'; '.join(issues) if issues else 'aucun'}\n"
            f"Verdict : {verdict}"
        )
        return {"accepted": not issues, "message": message, "issues": issues}

    def _hermetic_gate_equation(self, speaker: str) -> Dict[str, object]:
        candidate = (
            self._last_by("Synthetiseur")
            or self._last_by("Basilide")
            or self._last_by("Aurelius")
            or ""
        )
        eq_line = self._extract_equation_line(candidate) if hasattr(self, "_extract_equation_line") else ""
        issues: List[str] = []
        if not eq_line or "=" not in eq_line:
            issues.append("équation absente")
        else:
            eval_result = self._evaluate_equation_structure(candidate)
            issues = list(eval_result.get("issues", []))
        verdict = "conforme" if not issues else "à réparer"
        header = "Cohérence hermétique vérifiée" if speaker == "Hermetica" else "Révision de complétude vérifiée"
        message = (
            f"{header} (déterministe, sans LLM)\n"
            f"Équation : {eq_line or '-'}\n"
            f"Défauts : {'; '.join(issues) if issues else 'aucun'}\n"
            f"Verdict : {verdict}"
        )
        return {"accepted": not issues, "message": message, "issues": issues}

    # ------------------------------------------------------------------
    # Causal link helpers (shared by variable and equation orchestrators)
    # ------------------------------------------------------------------
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

    def _extract_link_symbols(self, links: List[str]) -> List[str]:
        out: List[str] = []
        extractor = getattr(self, "_extract_equation_symbols", None)
        for line in links or []:
            syms = extractor(line) if callable(extractor) else re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line or "")
            for sym in syms:
                if sym not in out:
                    out.append(sym)
        return out

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
            sym_extractor = getattr(self, "_extract_equation_symbols", None)
            syms = sym_extractor(line) if callable(sym_extractor) else []
            if len(syms) < 2:
                continue
            src, dst = syms[0], syms[-1]
            pairs.setdefault((src, dst), set()).add(direction)
        contradictions: List[str] = []
        for (src, dst), dirs in pairs.items():
            if "augmente" in dirs and "diminue" in dirs:
                contradictions.append(f"{src} augmente et diminue {dst}")
        return contradictions

    def _parse_decision(self, text: str) -> str:
        for field in ["Décision finale", "Décision", "Verdict", "Statut", "Décision mémoire"]:
            value = self._extract_named_field(text, field)
            if value:
                return value
        return (text or "").splitlines()[-1].strip() if (text or "").strip() else ""

    # ----------------------- variable extraction -----------------------
    def _extract_variable_symbol(self, text: str) -> str:
        return self._extract_first(VAR_SYMBOL_RE, text)

    def _extract_variable_line(self, text: str) -> str:
        return self._extract_variable_symbol(text)

    def _extract_variable_definition(self, text: str) -> str:
        return self._extract_first(VAR_DEF_RE, text)

    def _extract_variable_unit(self, text: str) -> str:
        return self._extract_first(VAR_UNIT_RE, text)

    def _extract_variable_measure(self, text: str) -> str:
        return self._extract_first(VAR_MEASURE_RE, text)

    def _extract_variable_role(self, text: str) -> str:
        role = self._extract_first(VAR_ROLE_RE, text)
        return role or self._extract_first(VAR_ROLE_FALLBACK_RE, text)

    def _extract_variable_family(self, text: str) -> str:
        return self._extract_first(VAR_FAMILY_RE, text)

    def _extract_variable_context(self, text: str) -> str:
        return self._extract_first(VAR_CONTEXT_RE, text)

    def _extract_variable_links(self, text: str) -> List[str]:
        return self._extract_links(text)

    def _parse_variable_block(self, text: str) -> Dict[str, object]:
        parsed = {
            "symbol": self._extract_variable_symbol(text),
            "family": self._extract_variable_family(text),
            "definition": self._extract_variable_definition(text),
            "unit": self._extract_variable_unit(text),
            "measure": self._extract_variable_measure(text),
            "role": self._extract_variable_role(text),
            "context": self._extract_variable_context(text),
            "links": self._extract_variable_links(text),
        }
        self.last_parsed_variable = parsed
        return parsed

    # ----------------------- equation extraction -----------------------
    def _extract_equation_line(self, text: str) -> str:
        return self._extract_first(EQ_RE, text)

    def _extract_equation(self, text: str) -> str:
        return self._extract_equation_line(text)

    def _extract_equation_object(self, text: str) -> str:
        return self._extract_first(OBJECT_RE, text)

    def _extract_equation_architecture(self, text: str) -> str:
        return self._extract_first(ARCH_RE, text) or self._extract_first(ARCH_FALLBACK_RE, text)

    def _extract_equation_type(self, text: str) -> str:
        return self._extract_first(TYPE_RE, text)

    def _extract_equation_links(self, text: str) -> List[str]:
        return self._extract_links(text)

    def _parse_equation_block(self, text: str) -> Dict[str, object]:
        parsed = {
            "equation": self._extract_equation_line(text),
            "object": self._extract_equation_object(text),
            "architecture": self._extract_equation_architecture(text),
            "law_type": self._extract_equation_type(text),
            "links": self._extract_equation_links(text),
        }
        self.last_parsed_equation = parsed
        return parsed

    # ----------------------- evaluation -----------------------
    def _evaluate_variable_structure(self, text: str) -> Dict[str, object]:
        parsed = self._parse_variable_block(text)
        issues: List[str] = []
        score = 0
        symbol = str(parsed.get("symbol", "")).strip()
        if not symbol:
            issues.append("variable absente")
        else:
            score += 2
        if parsed.get("definition"):
            score += 1
        else:
            issues.append("définition absente")
        if parsed.get("unit"):
            score += 1
        else:
            issues.append("unité absente")
        if parsed.get("measure"):
            score += 1
        else:
            issues.append("mesure absente")
        if parsed.get("role"):
            score += 1
        else:
            issues.append("rôle causal absent")
        links = parsed.get("links") or []
        if links:
            score += min(2, len(links))
        return {
            "parsed": parsed,
            "symbol": symbol,
            "issues": issues,
            "score": score,
            "is_valid": bool(symbol and parsed.get("definition") and parsed.get("unit") and parsed.get("measure")),
        }

    def _evaluate_equation_structure(self, text: str) -> Dict[str, object]:
        parsed = self._parse_equation_block(text)
        issues: List[str] = []
        score = 0
        eq = str(parsed.get("equation", "")).strip()
        if not eq:
            issues.append("équation absente")
        else:
            score += 2
        if parsed.get("object"):
            score += 1
        else:
            issues.append("objet calculé absent")
        if parsed.get("architecture"):
            score += 1
        else:
            issues.append("architecture absente")
        links = parsed.get("links") or []
        if len(links) >= 2:
            score += min(2, len(links))
        else:
            issues.append("moins de 2 liens")
        return {"parsed": parsed, "equation": eq, "issues": issues, "score": score, "is_valid": bool(eq)}

    def _evaluate_agent_output(self, speaker: str, text: str) -> Dict[str, object]:
        if self.debate_kind == "variable":
            return self._evaluate_variable_structure(text)
        return self._evaluate_equation_structure(text)

    def _should_retry(self, eval_result: Dict[str, object]) -> bool:
        if self.debate_kind == "variable":
            critical = {"variable absente", "définition absente", "unité absente", "mesure absente", "variable déjà validée"}
        else:
            critical = {"équation absente", "objet calculé absent", "architecture absente"}
        return any(issue in critical for issue in eval_result.get("issues", []))

    def _targeted_retry_instruction(self, eval_result: Dict[str, object]) -> str:
        if self.debate_kind == "variable":
            return (
                "Régénère en respectant strictement ce format :\n"
                "Variable : X\nDéfinition : ...\nUnité : ...\nMesure : ...\nRôle causal : ...\nLiens :\n- ...\n- ..."
            )
        return (
            "Régénère en respectant strictement ce format :\n"
            "Objet calculé : ...\nType de loi : ...\nArchitecture choisie : ...\nÉquation : ...\nLiens :\n- ...\n- ..."
        )

    # ----------------------- utils -----------------------
    def _append_log(self, speaker: str, text: str) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(f"--- {self.turn_prefix} {self.state.get('turn', 0)} ---\n")
                f.write(f"[DEBUG] {speaker} utilise modèle: {self._model_for(speaker)}\n")
                f.write(f"{speaker}: {text}\n")
        except Exception:
            pass
