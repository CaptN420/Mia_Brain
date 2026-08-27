from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from typing import Any, Dict, List, Optional


def normalize_target_equation(value) -> str:
    value = str(value or "").strip()
    m = re.match(r"^T\d+\s*\|\s*(.+)$", value)
    if m:
        return m.group(1).strip()
    if " | " in value:
        return value.split(" | ", 1)[1].strip()
    return value


@dataclass
class SharedVariable:
    name: str
    definition: str = ""
    unit: str = ""
    measure: str = ""
    role: str = ""
    links: List[str] = field(default_factory=list)
    remarks: List[str] = field(default_factory=list)
    approved: bool = False
    source_turn: int = 0
    source_agent: str = ""
    validation_summary: str = ""
    required_next: List[str] = field(default_factory=list)
    family: str = ""
    micro_equation: str = ""
    experiment: str = ""
    status: str = "candidate"


@dataclass
class SharedEquation:
    equation: str
    definitions: Dict[str, str] = field(default_factory=dict)
    mechanism: str = ""
    experiment: str = ""
    links: List[str] = field(default_factory=list)
    remark: str = ""
    approved: bool = False
    source_turn: int = 0
    source_agent: str = ""
    validation_summary: str = ""
    required_next: List[str] = field(default_factory=list)
    status: str = "candidate"
    fallback_used: bool = False
    stable_parent: bool = False
    repair_required: bool = False
    memory_decision: str = ""
    object_calculated: str = ""
    law_type: str = ""
    architecture: str = ""
    parent_equation: str = ""
    parent_variable: str = ""
    exploratory_parent: bool = False
    repair_log: Dict[str, str] = field(default_factory=dict)


class SharedResearchMemory:
    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)
        self.path = self.session_dir / "shared_research_memory.json"
        self.data = self._load()

    def _blank(self) -> Dict[str, Any]:
        return {
            "approved_variables": {},
            "candidate_variables": {},
            "rejected_fragments": [],
            "approved_links": [],
            "approved_equations": [],
            "partial_equations": [],
            "priority_variables": [],
            "recent_links": [],
            "recent_remarks": [],
            "final_validations": [],
            "debate_summaries": [],
            "equation_scores": {},
            "variable_scores": {},
            "equation_usage_count": {},
            "variable_usage_count": {},
            "equation_failures": {},
            "variable_failures": {},
            "turn_metrics": [],
            "mutation_history": [],
            "stagnation_events": [],
            "last_validated_variable": {},
            "pending_variables": {},
            "pending_equations": {},
            "repair_logs": [],
            "repair_patterns": [],
            "consolidation_logs": [],
        }

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._blank()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            blank = self._blank()
            if isinstance(data, dict):
                blank.update(data)
            return blank
        except Exception:
            return self._blank()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


    def _normalize_pending_key(self, symbol: str) -> str:
        return str(symbol or "").strip().lower()

    def _normalize_symbol_key(self, symbol: str) -> str:
        return str(symbol or "").strip().lower()

    def _find_existing_symbol_key(self, store_name: str, symbol: str) -> str:
        wanted = self._normalize_symbol_key(symbol)
        for key in (self.data.get(store_name, {}) or {}).keys():
            if self._normalize_symbol_key(key) == wanted:
                return str(key)
        return ""

    def _drop_symbol_from_store(self, store_name: str, symbol: str) -> None:
        found = self._find_existing_symbol_key(store_name, symbol)
        if found:
            self.data.get(store_name, {}).pop(found, None)


    def _merge_variable_payload(self, store_name: str, variable: SharedVariable) -> Dict[str, Any]:
        payload = asdict(variable)
        existing = self.data.get(store_name, {}).get(variable.name, {})
        payload["links"] = list(dict.fromkeys(existing.get("links", []) + payload.get("links", [])))[:16]
        payload["remarks"] = list(dict.fromkeys(existing.get("remarks", []) + payload.get("remarks", [])))[:8]
        payload["required_next"] = list(dict.fromkeys(existing.get("required_next", []) + payload.get("required_next", [])))[:12]
        if not payload.get("status"):
            payload["status"] = "approved" if variable.approved else "candidate"
        return payload

    def add_variable(self, variable: SharedVariable) -> None:
        store_name = "approved_variables" if variable.approved else "candidate_variables"
        other_store = "candidate_variables" if variable.approved else "approved_variables"
        payload = self._merge_variable_payload(store_name, variable)
        self.data[store_name][variable.name] = payload
        if variable.approved:
            self.data[other_store].pop(variable.name, None)

        for link in payload.get("links", []):
            if variable.approved and link not in self.data["approved_links"]:
                self.data["approved_links"].append(link)
            self.data["recent_links"].append(f"{variable.name}: {link}")
        for rem in payload.get("remarks", []):
            self.data["recent_remarks"].append(f"{variable.name}: {rem}")

        self.data["approved_links"] = self.data["approved_links"][-80:]
        self.data["recent_links"] = self.data["recent_links"][-80:]
        self.data["recent_remarks"] = self.data["recent_remarks"][-80:]
        if variable.name and variable.name not in self.data["priority_variables"]:
            self.data["priority_variables"].append(variable.name)
        self.data["priority_variables"] = self.data["priority_variables"][-20:]
        self.score_variable(variable.name, variable.approved)
        if variable.approved:
            self.data.setdefault("pending_variables", {})
            self.data["pending_variables"].pop(self._normalize_pending_key(variable.name), None)
            self.data["last_validated_variable"] = {
                "symbol": variable.name,
                "family": variable.family,
                "definition": variable.definition,
                "unit": variable.unit,
                "measure": variable.measure,
                "role": variable.role,
                "source_turn": variable.source_turn,
                "source_agent": variable.source_agent,
            }
        self.save()

    def add_pending_variable(self, norm_symbol: str, payload: Dict[str, Any]) -> None:
        key = self._normalize_pending_key(norm_symbol)
        if not key:
            return
        clean = {k: v for k, v in dict(payload or {}).items() if v not in (None, "", [], {})}
        self.data.setdefault("pending_variables", {})
        existing = dict(self.data["pending_variables"].get(key, {}) or {})
        existing.update(clean)
        self.data["pending_variables"][key] = existing
        self.save()

    def get_pending_variables(self) -> Dict[str, Dict[str, Any]]:
        raw = self.data.get("pending_variables", {}) or {}
        return {str(k): dict(v or {}) for k, v in raw.items()}

    def get_pending_variable(self, norm_symbol: str) -> Dict[str, Any]:
        key = self._normalize_pending_key(norm_symbol)
        if not key:
            return {}
        return dict((self.data.get("pending_variables", {}) or {}).get(key, {}) or {})

    def remove_pending_variable(self, norm_symbol: str) -> None:
        key = self._normalize_pending_key(norm_symbol)
        if not key:
            return
        self.data.setdefault("pending_variables", {})
        self.data["pending_variables"].pop(key, None)
        self.save()

    def clear_pending_variables(self) -> None:
        self.data["pending_variables"] = {}
        self.save()

    def _normalize_equation_key(self, equation: str) -> str:
        return normalize_target_equation(equation)

    def add_pending_equation(self, equation: str, payload: Dict[str, Any]) -> None:
        key = self._normalize_equation_key(equation)
        if not key:
            return
        self.data.setdefault("pending_equations", {})
        existing = dict((self.data.get("pending_equations", {}) or {}).get(key, {}) or {})
        existing.update({k: v for k, v in dict(payload or {}).items() if v is not None})
        existing["equation"] = key
        self.data["pending_equations"][key] = existing
        self.save()

    def get_pending_equations(self) -> Dict[str, Dict[str, Any]]:
        raw = self.data.get("pending_equations", {}) or {}
        return {str(k): dict(v or {}) for k, v in raw.items()}

    def remove_pending_equation(self, equation: str) -> None:
        key = self._normalize_equation_key(equation)
        if not key:
            return
        self.data.setdefault("pending_equations", {})
        self.data["pending_equations"].pop(key, None)
        self.save()


    def get_latest_stable_equation(self) -> Optional[Dict[str, Any]]:
        approved = list(self.data.get("approved_equations", []) or [])
        for row in reversed(approved):
            if bool(row.get("approved", False)) and bool(row.get("stable_parent", False)) and not bool(row.get("fallback_used", False)) and not bool(row.get("repair_required", False)):
                return dict(row)
        return None

    def get_latest_equation_entry(self) -> Optional[Dict[str, Any]]:
        approved = self.get_latest_equation()
        partial = self.get_latest_partial_equation()
        if approved and not partial:
            return dict(approved)
        if partial and not approved:
            return dict(partial)
        if not approved and not partial:
            return None

        def rank(row: Dict[str, Any]) -> tuple[int, int, int]:
            source_turn = int(row.get("source_turn", 0) or 0)
            approved_flag = 1 if bool(row.get("approved", False) or str(row.get("status", "")).lower() == "approved") else 0
            stable_flag = 1 if bool(row.get("stable_parent", False)) else 0
            return (source_turn, approved_flag, stable_flag)

        a = dict(approved or {})
        p = dict(partial or {})
        return a if rank(a) >= rank(p) else p

    def can_start_mutation(self) -> tuple[bool, str]:
        latest = self.get_latest_equation_entry() or {}
        if not latest:
            return False, "aucune équation parent"

        decision = ""
        for row in reversed(self.data.get("final_validations", []) or []):
            if str(row.get("kind", "")) == "equation":
                decision = str(row.get("decision", "") or row.get("summary", "")).lower()
                break

        approved = bool(latest.get("approved", False) or str(latest.get("status", "")).lower() == "approved")
        stable_parent = bool(latest.get("stable_parent", False))
        fallback_used = bool(latest.get("fallback_used", False))
        repair_required = bool(latest.get("repair_required", False))

        if fallback_used:
            return False, "équation parent fallback"
        if repair_required:
            return False, "équation parent à réparer"
        if any(tok in decision for tok in ["à réparer", "a reparer", "repair", "needs_repair"]):
            return False, "validation finale à réparer"
        if not approved:
            return False, "équation parent non approuvée"
        if not stable_parent:
            return False, "équation parent non stable"
        return True, "ok"


    def can_start_mutation_for_equation(self, equation: str) -> tuple[bool, str]:
        wanted = str(equation or '').strip()
        if not wanted:
            return False, "aucune équation parent"
        payload = self.get_equation_payload(wanted) or {}
        if not payload:
            return False, "équation cible introuvable"

        approved = bool(payload.get("approved", False) or str(payload.get("status", "")).lower() == "approved")
        stable_parent = bool(payload.get("stable_parent", False))
        fallback_used = bool(payload.get("fallback_used", False))
        repair_required = bool(payload.get("repair_required", False))

        if fallback_used:
            return False, "équation cible fallback"
        if repair_required:
            return False, "équation cible à réparer"
        if not approved:
            return False, "équation cible non approuvée"
        if not stable_parent:
            return False, "équation cible non stable"
        return True, "ok"

    def can_start_repair_for_equation(self, equation: str) -> tuple[bool, str]:
        wanted = normalize_target_equation(equation)
        if not wanted:
            return False, "aucune équation à réparer"
        payload = self.get_equation_payload(wanted) or {}
        if not payload:
            return False, "équation cible introuvable"
        if bool(payload.get("fallback_used", False)):
            return True, "fallback à fermer"
        if bool(payload.get("repair_required", False)):
            return True, "repair_required"
        if bool(payload.get("fallback_used", False)):
            return True, "fallback à fermer"
        if str(payload.get("status", "")).lower() == "partial":
            return True, "équation partielle"
        decision = str(payload.get("memory_decision", "") or payload.get("validation_summary", "")).lower()
        if any(tok in decision for tok in ["à réparer", "a reparer", "repair", "needs_repair"]):
            return True, "validation à réparer"
        info = self.detect_stagnation(kind="equation", window=4)
        if bool(info.get("stagnant", False)):
            return True, "stagnation globale"
        failures = int(self.data.get("equation_failures", {}).get(str(payload.get("equation", "")).strip(), 0) or 0)
        if failures >= 2:
            return True, "échecs répétés"
        if bool(payload.get("approved", False)) and bool(payload.get("stable_parent", False)):
            return True, "repair manuel sur équation stable"
        return False, "pas de réparation nécessaire"

    def get_all_equation_entries(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for bucket_name in ('approved_equations', 'partial_equations'):
            for row in self.data.get(bucket_name, []) or []:
                payload = dict(row)
                payload['_bucket'] = bucket_name
                rows.append(payload)
        return rows

    def can_start_repair(self) -> tuple[bool, str]:
        latest = self.get_latest_equation_entry() or {}
        if not latest:
            return False, "aucune équation à réparer"

        if bool(latest.get("fallback_used", False)):
            return True, "fallback à fermer"
        if bool(latest.get("repair_required", False)):
            return True, "repair_required"

        decision = ""
        for row in reversed(self.data.get("final_validations", []) or []):
            if str(row.get("kind", "")) == "equation":
                decision = str(row.get("decision", "") or row.get("summary", "")).lower()
                break

        if any(tok in decision for tok in ["à réparer", "a reparer", "repair", "needs_repair"]):
            return True, "validation finale à réparer"

        if str(latest.get("status", "")).lower() == "partial":
            return True, "équation partielle"

        info = self.detect_stagnation(kind="equation", window=4)
        if bool(info.get("stagnant", False)):
            return True, "stagnation globale"

        failures = int(self.data.get("equation_failures", {}).get(str(latest.get("equation", "")).strip(), 0) or 0)
        if failures >= 2:
            return True, "échecs répétés"

        return False, "pas de réparation nécessaire"

    def add_repair_log(self, repair_log: Dict[str, Any]) -> None:
        clean = {k: v for k, v in dict(repair_log or {}).items() if v not in (None, "", [], {})}
        if not clean:
            return
        self.data.setdefault("repair_logs", []).append(clean)
        self.data["repair_logs"] = self.data["repair_logs"][-120:]

        pattern = str(clean.get("pattern", "") or "").strip()
        if pattern:
            bucket = self.data.setdefault("repair_patterns", [])
            existing = next((row for row in bucket if str(row.get("pattern", "") or "").strip() == pattern), None)
            if existing is None:
                bucket.append({"pattern": pattern, "count": 1, "last_turn": int(clean.get("turn", 0) or 0)})
            else:
                existing["count"] = int(existing.get("count", 0) or 0) + 1
                existing["last_turn"] = int(clean.get("turn", 0) or 0)
            bucket.sort(key=lambda row: (int(row.get("count", 0) or 0), int(row.get("last_turn", 0) or 0)), reverse=True)
            self.data["repair_patterns"] = bucket[:40]

        note = str(clean.get("fix_applied", "") or clean.get("main_issue", "") or "").strip()
        if note:
            self.data.setdefault("recent_remarks", []).append(f"REPAIR: {note}")
            self.data["recent_remarks"] = self.data["recent_remarks"][-80:]
        self.save()

    def get_recent_repair_logs(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(self.data.get("repair_logs", []))[-limit:]

    def get_top_repair_patterns(self, limit: int = 5) -> List[Dict[str, Any]]:
        rows = list(self.data.get("repair_patterns", []))
        rows.sort(key=lambda row: (int(row.get("count", 0) or 0), int(row.get("last_turn", 0) or 0)), reverse=True)
        return rows[:limit]

    def build_repair_memory_block(self, limit: int = 4) -> str:
        logs = self.get_recent_repair_logs(limit=limit)
        patterns = self.get_top_repair_patterns(limit=limit)
        lines: List[str] = []
        if patterns:
            lines.append("PATTERNS DE RÉPARATION MÉMORISÉS :")
            for row in patterns:
                lines.append(f"- {row.get('pattern', '')} (x{int(row.get('count', 0) or 0)})")
        if logs:
            lines.append("RÉPARATIONS RÉCENTES :")
            for row in logs:
                original = str(row.get("original_equation", "") or "").strip()
                fixed = str(row.get("fixed_equation", "") or "").strip()
                issue = str(row.get("main_issue", "") or "").strip()
                fix = str(row.get("fix_applied", "") or "").strip()
                line = f"- défaut={issue or '-'} | correction={fix or '-'}"
                if original or fixed:
                    line += f" | {original or '?'} -> {fixed or '?'}"
                lines.append(line)
        return "\n".join(lines).strip()



    def get_variable_payload(self, symbol: str) -> Dict[str, Any]:
        wanted = str(symbol or "").strip()
        if not wanted:
            return {}
        wanted_key = self._normalize_symbol_key(wanted)
        for store_name, bucket_name in (("approved_variables", "approved"), ("candidate_variables", "candidate")):
            store = self.data.get(store_name, {}) or {}
            for key, payload in store.items():
                current = str(key or payload.get("name", "") or payload.get("symbol", "") or "").strip()
                if self._normalize_symbol_key(current) == wanted_key:
                    row = dict(payload or {})
                    row.setdefault("name", current or wanted)
                    row.setdefault("symbol", row.get("name", current or wanted))
                    row["_bucket"] = bucket_name
                    return row
        pending = self.get_pending_variable(wanted)
        if pending:
            row = dict(pending)
            row.setdefault("name", wanted)
            row.setdefault("symbol", row.get("name", wanted))
            row["_bucket"] = "pending"
            return row
        return {}

    def is_variable_valid(self, symbol: str) -> bool:
        row = self.get_variable_payload(symbol)
        if not row:
            return False
        bucket = str(row.get("_bucket", "") or "").strip().lower()
        if bucket == "approved":
            return True
        if bool(row.get("approved", False)):
            return True
        status = str(row.get("status", "") or "").strip().lower()
        return status in {"approved", "validated", "valid"}

    def was_recently_validated(self, equation: str, window: int = 5) -> bool:
        wanted = self._normalized_equation_key(equation)
        if not wanted:
            return False
        for row in reversed(list(self.data.get("repair_logs", []) or [])[-max(1, int(window)):]):
            verdict = str(row.get("verdict", "") or "").strip().lower()
            if verdict not in {"approved", "validated", "valid"}:
                continue
            original = self._normalized_equation_key(str(row.get("original_equation", "") or ""))
            fixed = self._normalized_equation_key(str(row.get("fixed_equation", "") or ""))
            if wanted in {original, fixed}:
                return True
        return False

    def add_consolidation_log(self, original_equation: str, consolidated_equation: str = "", reason: str = "") -> None:
        original = normalize_target_equation(original_equation)
        consolidated = normalize_target_equation(consolidated_equation)
        clean = {
            "original_equation": original,
            "consolidated_equation": consolidated,
            "reason": str(reason or "").strip(),
        }
        self.data.setdefault("consolidation_logs", []).append(clean)
        self.data["consolidation_logs"] = self.data["consolidation_logs"][-120:]
        note = consolidated or original
        if note:
            self.data.setdefault("recent_remarks", []).append(f"CONSOLIDATE: {note}")
            self.data["recent_remarks"] = self.data["recent_remarks"][-80:]
        self.save()

    def get_recent_consolidation_logs(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(self.data.get("consolidation_logs", []) or [])[-max(1, int(limit)):]

    def get_latest_consolidation_state(self, equation: str, window: int = 20) -> str:
        wanted = self._normalized_equation_key(equation)
        if not wanted:
            return ""
        sterile_reasons = {"no_structural_change", "sterile_placeholder", "blacklisted_no_structural_change"}
        recent = self.get_recent_consolidation_logs(limit=max(1, int(window)))
        for row in reversed(recent):
            original = self._normalized_equation_key(str(row.get("original_equation", "") or ""))
            consolidated = self._normalized_equation_key(str(row.get("consolidated_equation", "") or ""))
            reason = str(row.get("reason", "") or "").strip().lower()
            if wanted not in {original, consolidated}:
                continue
            if consolidated and reason not in sterile_reasons:
                return "success"
            if reason in sterile_reasons:
                return "sterile"
            if consolidated:
                return "success"
            return "seen"
        return ""

    def was_recently_consolidated(self, equation: str, window: int = 6) -> bool:
        return self.get_latest_consolidation_state(equation, window=window) == "success"

    def was_recently_sterile_consolidation(self, equation: str, window: int = 8) -> bool:
        return self.get_latest_consolidation_state(equation, window=window) == "sterile"

    def clear_sterile_consolidation_state(self, equation: str) -> None:
        wanted = self._normalized_equation_key(equation)
        if not wanted:
            return
        sterile_reasons = {"no_structural_change", "sterile_placeholder", "blacklisted_no_structural_change"}
        cleaned: List[Dict[str, Any]] = []
        changed = False
        for row in list(self.data.get("consolidation_logs", []) or []):
            original = self._normalized_equation_key(str(row.get("original_equation", "") or ""))
            consolidated = self._normalized_equation_key(str(row.get("consolidated_equation", "") or ""))
            reason = str(row.get("reason", "") or "").strip().lower()
            if wanted in {original, consolidated} and reason in sterile_reasons and not consolidated:
                changed = True
                continue
            cleaned.append(row)
        if changed:
            self.data["consolidation_logs"] = cleaned[-120:]
            self.save()

    def add_sterile_consolidation_log(self, equation: str, reason: str = "no_structural_change") -> None:
        self.add_consolidation_log(equation, "", reason=reason)


    def get_variable_repair_candidates(self) -> List[Dict[str, Any]]:
        pending = self.get_pending_variables()
        recent_logs = list(self.data.get("repair_logs", []) or [])[-12:]
        block_counts: Dict[str, int] = {}
        for log in recent_logs:
            if not bool(log.get("blocked_by_variables", False)):
                continue
            for symbol in list(log.get("next_variables", []) or []):
                clean = str(symbol or "").strip()
                if not clean:
                    continue
                key = self._normalize_pending_key(clean)
                block_counts[key] = int(block_counts.get(key, 0) or 0) + 1

        rows: List[Dict[str, Any]] = []
        for key, payload in pending.items():
            row = dict(payload or {})
            symbol = str(row.get("symbol", "") or row.get("name", "") or key).strip()
            if not symbol:
                symbol = str(key).strip()
            row["symbol"] = symbol
            row["_pending_key"] = str(key)
            score = 0
            score += 30
            score += 25 * int(block_counts.get(str(key), 0) or 0)
            score += 10 if str(row.get("family", "")).strip() else 0
            score += 8 if str(row.get("definition", "")).strip() else 0
            score += 6 if str(row.get("unit", "")).strip() else 0
            score += 6 if str(row.get("measure", "")).strip() else 0
            score += 6 if str(row.get("role", "")).strip() else 0
            row["_score"] = score
            rows.append(row)
        rows.sort(key=lambda r: (int(r.get("_score", 0) or 0), str(r.get("symbol", ""))), reverse=True)
        return rows

    def _recent_repair_logs_for_equation(self, equation: str, limit: int = 6) -> List[Dict[str, Any]]:
        wanted = self._normalized_equation_key(equation)
        rows: List[Dict[str, Any]] = []
        for row in reversed(self.data.get("repair_logs", []) or []):
            original = self._normalized_equation_key(str(row.get("original_equation", "") or ""))
            if original and original == wanted:
                rows.append(dict(row))
            if len(rows) >= max(1, int(limit)):
                break
        return rows

    def choose_next_repair_action(self) -> Dict[str, Any]:
        pending_vars = [row for row in self.get_variable_repair_candidates() if not self.is_variable_valid(str(row.get('symbol', '') or ''))]
        recent_logs = list(self.data.get("repair_logs", []) or [])[-10:]
        global_blocked = [dict(log) for log in recent_logs if bool(log.get("blocked_by_variables", False))]
        eq_candidates: List[tuple[int, Dict[str, Any], str]] = []
        for row in self.get_all_equation_entries():
            equation = str(row.get("equation", "") or "").strip()
            if not equation:
                continue
            ok, reason = self.can_start_repair_for_equation(equation)
            if not ok:
                continue

            score = 0
            score += 100 if bool(row.get("repair_required", False)) else 0
            score += 80 if str(row.get("status", "")).lower() == "partial" else 0
            score += 70 if bool(row.get("fallback_used", False)) else 0
            score += 30 if bool(row.get("stable_parent", False)) else 0
            score += max(0, 20 - int(self.data.get("equation_failures", {}).get(equation, 0) or 0) * 2)
            score += int(row.get("source_turn", 0) or 0)

            recent_logs = self._recent_repair_logs_for_equation(equation, limit=4)
            blocked_count = 0
            same_fix_count = 0
            for log in recent_logs:
                if bool(log.get("blocked_by_variables", False)):
                    blocked_count += 1
                fixed = str(log.get("fixed_equation", "") or "").strip()
                if fixed and self._normalized_equation_key(fixed) == self._normalized_equation_key("Ndot = k_eff"):
                    same_fix_count += 1

            if pending_vars and blocked_count:
                score -= 120
            if pending_vars and same_fix_count >= 1:
                score -= 80
            if blocked_count >= 2:
                score -= 60
            if same_fix_count >= 2:
                score -= 60
            if self.was_recently_validated(equation, window=4):
                score -= 140

            eq_candidates.append((score, dict(row), reason))

        if pending_vars:
            best_var = pending_vars[0]
            if not eq_candidates:
                return {
                    "kind": "variable",
                    "target": str(best_var.get("symbol", "")).strip(),
                    "reason": "pending_variable_repair",
                }
            if global_blocked:
                return {
                    "kind": "variable",
                    "target": str(best_var.get("symbol", "")).strip(),
                    "reason": "blocked_by_unvalidated_variables",
                }
            eq_candidates.sort(key=lambda item: item[0], reverse=True)
            best_eq_score, best_eq, best_eq_reason = eq_candidates[0]
            if best_eq_score < 60:
                return {
                    "kind": "variable",
                    "target": str(best_var.get("symbol", "")).strip(),
                    "reason": "blocked_by_unvalidated_variables",
                }
            recent_logs = self._recent_repair_logs_for_equation(str(best_eq.get("equation", "")).strip(), limit=3)
            if any(bool(log.get("blocked_by_variables", False)) for log in recent_logs):
                return {
                    "kind": "variable",
                    "target": str(best_var.get("symbol", "")).strip(),
                    "reason": "blocked_by_unvalidated_variables",
                }

        if not eq_candidates:
            return {"kind": "none", "target": "", "reason": "no_repairable_target"}

        eq_candidates.sort(key=lambda item: item[0], reverse=True)
        _score, row, reason = eq_candidates[0]
        return {
            "kind": "equation",
            "target": str(row.get("equation", "")).strip(),
            "reason": str(reason or "repair_required"),
        }

    def add_fragment(self, fragment: Dict[str, Any]) -> None:
        clean = {k: v for k, v in fragment.items() if v not in (None, "", [], {})}
        self.data["rejected_fragments"].append(clean)
        self.data["rejected_fragments"] = self.data["rejected_fragments"][-80:]
        if clean.get("remark"):
            self.data["recent_remarks"].append(f"FRAGMENT: {clean['remark']}")
            self.data["recent_remarks"] = self.data["recent_remarks"][-80:]
        self.save()

    def add_equation(self, equation: SharedEquation) -> None:
        payload = asdict(equation)
        payload["equation"] = normalize_target_equation(payload.get("equation", ""))
        payload["parent_equation"] = normalize_target_equation(payload.get("parent_equation", ""))

        if bool(payload.get("fallback_used", False)):
            payload["approved"] = False
            payload["status"] = "partial"
            payload["stable_parent"] = False
            payload["repair_required"] = True
            if not payload.get("memory_decision"):
                payload["memory_decision"] = "temporary_fallback_only"

        if payload.get("parent_equation") == payload.get("equation"):
            payload["parent_equation"] = ""

        target = "approved_equations" if bool(payload.get("approved", False)) else "partial_equations"
        other = "partial_equations" if target == "approved_equations" else "approved_equations"
        eq_key = str(payload.get("equation", "")).strip()
        eq_norm = self._normalized_equation_key(eq_key)
        if eq_key:
            self.data[target] = [row for row in self.data[target] if self._normalized_equation_key(str(row.get("equation", "")).strip()) != eq_norm]
            self.data[other] = [row for row in self.data[other] if self._normalized_equation_key(str(row.get("equation", "")).strip()) != eq_norm]
        self.data[target].append(payload)
        self.data[target] = self.data[target][-40:]
        for link in payload.get("links", []):
            self.data["recent_links"].append(f"EQ: {link}")
        if payload.get("remark"):
            self.data["recent_remarks"].append(f"EQ: {payload['remark']}")
        self.data["recent_links"] = self.data["recent_links"][-80:]
        self.data["recent_remarks"] = self.data["recent_remarks"][-80:]
        self.score_equation(equation.equation, equation.approved)
        eq_norm = normalize_target_equation(eq_key)
        if equation.approved and not bool(payload.get("fallback_used", False)) and not bool(payload.get("repair_required", False)):
            self.data.setdefault("pending_equations", {})
            self.data["pending_equations"].pop(eq_norm, None)
            parent = normalize_target_equation(str(payload.get("parent_equation", "") or ""))
            if parent:
                self.data["pending_equations"].pop(parent, None)
        else:
            self.add_pending_equation(eq_key, {"equation": eq_key, "reason": str(payload.get("memory_decision", "") or payload.get("status", "") or "pending_equation"), "source_turn": payload.get("source_turn", 0), "parent_equation": payload.get("parent_equation", "")})
        repair_log = payload.get("repair_log", {}) or {}
        if repair_log:
            self.add_repair_log(repair_log)
            return
        self.save()

    def add_final_validation(self, kind: str, turn: int, summary: str, decision: str) -> None:
        self.data["final_validations"].append({"kind": kind, "turn": turn, "summary": summary, "decision": decision})
        self.data["final_validations"] = self.data["final_validations"][-80:]
        self.save()

    def add_debate_summary(self, kind: str, turn: int, summary: str) -> None:
        self.data["debate_summaries"].append({"kind": kind, "turn": turn, "summary": summary})
        self.data["debate_summaries"] = self.data["debate_summaries"][-80:]
        self.save()

    def get_priority_variables(self) -> List[str]:
        return list(self.data.get("priority_variables", []))

    def get_approved_variables(self) -> Dict[str, Dict[str, Any]]:
        raw = dict(self.data.get("approved_variables", {}))
        merged: Dict[str, Dict[str, Any]] = {}
        seen: Dict[str, str] = {}
        for key, payload in raw.items():
            norm = self._normalize_symbol_key(key)
            if norm not in seen:
                seen[norm] = str(key)
                merged[str(key)] = dict(payload or {})
        return merged

    def get_last_validated_variable(self) -> Dict[str, Any]:
        return dict(self.data.get("last_validated_variable", {}) or {})


    def get_candidate_variables(self) -> Dict[str, Dict[str, Any]]:
        raw = dict(self.data.get("candidate_variables", {}))
        merged: Dict[str, Dict[str, Any]] = {}
        seen: Dict[str, str] = {}
        for key, payload in raw.items():
            norm = self._normalize_symbol_key(key)
            if norm not in seen:
                seen[norm] = str(key)
                merged[str(key)] = dict(payload or {})
        return merged

    def get_recent_links(self) -> List[str]:
        return list(self.data.get("recent_links", []))

    def get_recent_remarks(self) -> List[str]:
        return list(self.data.get("recent_remarks", []))

    def get_latest_equation(self) -> Optional[Dict[str, Any]]:
        equations = self.data.get("approved_equations", [])
        return equations[-1] if equations else None

    def get_latest_partial_equation(self) -> Optional[Dict[str, Any]]:
        equations = self.data.get("partial_equations", [])
        return equations[-1] if equations else None

    def get_recent_fragments(self) -> List[Dict[str, Any]]:
        return list(self.data.get("rejected_fragments", []))

    def score_equation(self, equation: str, approved: bool) -> None:
        eq = (equation or "").strip()
        if not eq:
            return
        self.data["equation_usage_count"][eq] = self.data["equation_usage_count"].get(eq, 0) + 1
        if approved:
            self.data["equation_scores"][eq] = self.data["equation_scores"].get(eq, 0) + 2
        else:
            self.data["equation_failures"][eq] = self.data["equation_failures"].get(eq, 0) + 1
            self.data["equation_scores"][eq] = self.data["equation_scores"].get(eq, 0) - 1

    def score_variable(self, name: str, approved: bool) -> None:
        name = (name or "").strip()
        if not name:
            return
        self.data["variable_usage_count"][name] = self.data["variable_usage_count"].get(name, 0) + 1
        if approved:
            self.data["variable_scores"][name] = self.data["variable_scores"].get(name, 0) + 2
        else:
            self.data["variable_failures"][name] = self.data["variable_failures"].get(name, 0) + 1
            self.data["variable_scores"][name] = self.data["variable_scores"].get(name, 0) - 1

    def compute_variable_turn_score(self, variable: Any) -> int:
        if isinstance(variable, dict):
            name = str(variable.get("name", "") or variable.get("symbol", "")).strip()
            approved = bool(variable.get("approved", False) or str(variable.get("status", "")).lower() == "approved")
            family = str(variable.get("family", "")).strip()
            role = str(variable.get("role", "") or variable.get("rôle", "")).strip()
            measure = str(variable.get("measure", "")).strip()
            unit = str(variable.get("unit", "")).strip()
            definition = str(variable.get("definition", "")).strip()
        else:
            name = str(getattr(variable, "name", "")).strip()
            approved = bool(getattr(variable, "approved", False) or getattr(variable, "status", "") == "approved")
            family = str(getattr(variable, "family", "")).strip()
            role = str(getattr(variable, "role", "")).strip()
            measure = str(getattr(variable, "measure", "")).strip()
            unit = str(getattr(variable, "unit", "")).strip()
            definition = str(getattr(variable, "definition", "")).strip()
        score = self.data.get("variable_scores", {}).get(name, 0)
        score += 3 if approved else 0
        score += 1 if family else 0
        score += 1 if definition else 0
        score += 1 if unit else 0
        score += 1 if measure else 0
        score += 1 if role else 0
        return score

    def compute_equation_turn_score(self, equation: Any) -> int:
        if isinstance(equation, dict):
            eq = str(equation.get("equation", "")).strip()
            approved = bool(equation.get("approved", False) or str(equation.get("status", "")).lower() == "approved")
            mechanism = str(equation.get("mechanism", "")).strip()
            experiment = str(equation.get("experiment", "")).strip()
            links = equation.get("links", []) or []
        else:
            eq = str(getattr(equation, "equation", "")).strip()
            approved = bool(getattr(equation, "approved", False) or getattr(equation, "status", "") == "approved")
            mechanism = str(getattr(equation, "mechanism", "")).strip()
            experiment = str(getattr(equation, "experiment", "")).strip()
            links = getattr(equation, "links", []) or []
        score = self.data.get("equation_scores", {}).get(eq, 0)
        score += 3 if approved else 0
        score += 1 if mechanism else 0
        score += 1 if experiment else 0
        score += min(2, len(list(links)))
        return score

    def add_turn_metric(self, kind: str, turn: int, score_global: int = 0, **extras: Any) -> None:
        metric = {"kind": str(kind), "turn": int(turn), "score_global": int(score_global), "score": int(score_global)}
        metric.update({k: v for k, v in extras.items() if v is not None})
        if "key" in metric and "signature" not in metric:
            metric["signature"] = metric["key"]
        self.data["turn_metrics"].append(metric)
        self.data["turn_metrics"] = self.data["turn_metrics"][-200:]
        self.save()

    def record_turn_metric(self, *args: Any, **kwargs: Any) -> None:
        """Compatibility alias for orchestrators expecting record_turn_metric."""
        if args and isinstance(args[0], dict):
            payload = dict(args[0])
            kind = str(payload.pop("kind", payload.pop("debate_kind", "")) or "unknown")
            turn = int(payload.pop("turn", 0) or 0)
            score_global = int(payload.pop("score_global", payload.pop("score", 0)) or 0)
            if "score" not in payload:
                payload["score"] = score_global
            self.add_turn_metric(kind=kind, turn=turn, score_global=score_global, **payload)
            return
        if len(args) >= 3:
            kind, turn, score_global, *rest = args
            extras = dict(rest[0]) if rest and isinstance(rest[0], dict) else {}
            extras.update(kwargs)
            if "score" not in extras:
                extras["score"] = int(score_global)
            self.add_turn_metric(str(kind), int(turn), int(score_global), **extras)
            return
        kind = str(kwargs.pop("kind", kwargs.pop("debate_kind", "unknown")))
        turn = int(kwargs.pop("turn", 0) or 0)
        score_global = int(kwargs.pop("score_global", kwargs.pop("score", 0)) or 0)
        if "score" not in kwargs:
            kwargs["score"] = score_global
        self.add_turn_metric(kind=kind, turn=turn, score_global=score_global, **kwargs)

    def add_turn_metrics(self, *args: Any, **kwargs: Any) -> None:
        self.record_turn_metric(*args, **kwargs)

    def get_turn_metrics(self, kind: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        metrics = list(self.data.get("turn_metrics", []))
        if kind:
            wanted = str(kind)
            metrics = [m for m in metrics if str(m.get("kind", "")) == wanted]
        return metrics[-max(1, int(limit)):]

    def get_stagnation(self, kind: Optional[str] = None, window: int = 3) -> Dict[str, Any]:
        metrics = self.get_turn_metrics(kind=kind, limit=max(2, window))
        if len(metrics) < 2:
            return {"stagnating": False, "window": len(metrics), "reason": "insufficient_history"}
        scores = [int(m.get("score_global", 0)) for m in metrics]
        unique_scores = len(set(scores))
        stagnating = unique_scores <= 1 or (max(scores) - min(scores) <= 1)
        return {
            "stagnating": stagnating,
            "window": len(metrics),
            "scores": scores,
            "reason": "flat_scores" if stagnating else "progress_detected",
        }

    def record_mutation(self, parent_equation: str, child_equation: str, decision: str, **extras: Any) -> None:
        entry = {
            "parent_equation": (parent_equation or "").strip(),
            "child_equation": (child_equation or "").strip(),
            "decision": (decision or "").strip(),
        }
        entry.update({k: v for k, v in extras.items() if v is not None})
        self.data["mutation_history"].append(entry)
        self.data["mutation_history"] = self.data["mutation_history"][-120:]
        self.save()

    def get_mutation_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self.data.get("mutation_history", []))[-limit:]

    def get_variable_score(self, name: str) -> int:
        return int(self.data.get("variable_scores", {}).get((name or "").strip(), 0))

    def get_equation_score(self, equation: str) -> int:
        return int(self.data.get("equation_scores", {}).get((equation or "").strip(), 0))

    def recent_metrics(self, kind: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        return self.get_turn_metrics(kind=kind, limit=limit)

    def detect_stagnation(self, kind: Optional[str] = None, window: int = 3) -> Dict[str, Any]:
        base = self.get_stagnation(kind=kind, window=window)
        metrics = self.get_turn_metrics(kind=kind, limit=max(2, window))
        repeated_signature = False
        no_recent_approved = False
        sigs = [str(m.get("signature", "")).strip() for m in metrics if str(m.get("signature", "")).strip()]
        if len(sigs) >= 2 and len(set(sigs)) == 1:
            repeated_signature = True
        approvals = [bool(m.get("approved", False)) for m in metrics]
        if metrics and not any(approvals):
            no_recent_approved = True
        return {
            "stagnant": bool(base.get("stagnating", False) or repeated_signature or no_recent_approved),
            "stagnating": bool(base.get("stagnating", False) or repeated_signature or no_recent_approved),
            "window": base.get("window", len(metrics)),
            "scores": base.get("scores", []),
            "reason": base.get("reason", ""),
            "repeated_signature": repeated_signature,
            "no_recent_approved": no_recent_approved,
        }

    def register_stagnation_event(self, kind: str, turn: int, reason: str = "") -> None:
        event = {"kind": str(kind), "turn": int(turn), "reason": str(reason or "")}
        self.data.setdefault("stagnation_events", []).append(event)
        self.data["stagnation_events"] = self.data["stagnation_events"][-120:]
        self.save()

    def add_equation_mutation(self, parent_equation: str, mutated_equation: str, decision: str, **extras: Any) -> None:
        self.record_mutation(parent_equation=parent_equation, child_equation=mutated_equation, decision=decision, mutated_equation=mutated_equation, **extras)

    def get_recent_equation_mutations(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = self.get_mutation_history(limit=limit)
        normalized: List[Dict[str, Any]] = []
        for item in items:
            row = dict(item)
            if "mutated_equation" not in row and "child_equation" in row:
                row["mutated_equation"] = row.get("child_equation", "")
            if "child_equation" not in row and "mutated_equation" in row:
                row["child_equation"] = row.get("mutated_equation", "")
            normalized.append(row)
        return normalized[-limit:]


    def _normalized_equation_key(self, equation: str) -> str:
        return re.sub(r"\s+", "", str(equation or "").strip().lower())

    def get_equation_payload(self, equation: str) -> Dict[str, Any]:
        wanted_raw = str(equation or '').strip()
        wanted = normalize_target_equation(wanted_raw)
        if not wanted:
            return {}
        wanted_norm = self._normalized_equation_key(wanted)
        wanted_raw_norm = self._normalized_equation_key(wanted_raw)
        for bucket_name in ('approved_equations', 'partial_equations'):
            for row in reversed(self.data.get(bucket_name, []) or []):
                current = str(row.get('equation', '')).strip()
                current_norm = self._normalized_equation_key(current)
                if current == wanted or current == wanted_raw or current_norm == wanted_norm or current_norm == wanted_raw_norm:
                    payload = dict(row)
                    payload['_bucket'] = bucket_name
                    return payload
        return {}

    def _lineage_nodes(self) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        for bucket_name in ('approved_equations', 'partial_equations'):
            for row in self.data.get(bucket_name, []) or []:
                eq = str(row.get('equation', '')).strip()
                if not eq:
                    continue
                current = nodes.get(eq, {'equation': eq})
                current.update(dict(row))
                current['equation'] = eq
                current['_bucket'] = bucket_name
                current.setdefault('parent_equation', str(row.get('parent_equation', '') or '').strip())
                current.setdefault('source_turn', int(row.get('source_turn', 0) or 0))
                nodes[eq] = current
        for row in self.data.get('mutation_history', []) or []:
            child = str(row.get('child_equation') or row.get('mutated_equation') or '').strip()
            parent = str(row.get('parent_equation', '') or '').strip()
            if not child:
                continue
            current = nodes.get(child, {'equation': child})
            current.setdefault('equation', child)
            if parent and not current.get('parent_equation'):
                current['parent_equation'] = parent
            if row.get('decision'):
                current.setdefault('memory_decision', str(row.get('decision', '')))
            if row.get('mechanism') and not current.get('mechanism'):
                current['mechanism'] = row.get('mechanism')
            nodes[child] = current
            if parent and parent not in nodes:
                nodes[parent] = {'equation': parent}
        return nodes

    def _lineage_metrics(self, equation: str) -> Dict[str, int]:
        eq = str(equation or '').strip()
        return {
            'score': int(self.data.get('equation_scores', {}).get(eq, 0)),
            'usage': int(self.data.get('equation_usage_count', {}).get(eq, 0)),
            'failures': int(self.data.get('equation_failures', {}).get(eq, 0)),
        }

    def get_equation_lineage(self, equation: str) -> List[Dict[str, Any]]:
        wanted = str(equation or '').strip()
        if not wanted:
            return []
        nodes = self._lineage_nodes()
        if wanted not in nodes:
            payload = self.get_equation_payload(wanted)
            return [payload] if payload else []
        chain: List[Dict[str, Any]] = []
        seen = set()
        current = wanted
        while current and current not in seen:
            seen.add(current)
            payload = dict(nodes.get(current, {}))
            payload.setdefault('equation', current)
            payload.update(self._lineage_metrics(current))
            chain.append(payload)
            current = str(payload.get('parent_equation', '') or '').strip()
        chain.reverse()
        return chain

    def get_best_lineages(self, limit: int = 5) -> List[Dict[str, Any]]:
        nodes = self._lineage_nodes()
        if not nodes:
            return []
        children = set()
        for payload in nodes.values():
            parent = str(payload.get('parent_equation', '') or '').strip()
            if parent:
                children.add(parent)
        leaves = [eq for eq in nodes.keys() if eq not in children]
        if not leaves:
            leaves = list(nodes.keys())

        scored: List[Dict[str, Any]] = []
        for leaf in leaves:
            chain = self.get_equation_lineage(leaf)
            if not chain:
                continue
            root = chain[0].get('equation', '')
            total_score = sum(int(row.get('score', 0) or 0) for row in chain)
            usage = sum(int(row.get('usage', 0) or 0) for row in chain)
            failures = sum(int(row.get('failures', 0) or 0) for row in chain)
            mechanisms = [str(row.get('mechanism', '') or '').strip() for row in chain if str(row.get('mechanism', '') or '').strip()]
            lineage = {
                'root_equation': root,
                'leaf_equation': leaf,
                'depth': len(chain),
                'total_score': total_score,
                'usage_total': usage,
                'failures_total': failures,
                'avg_score': round(total_score / max(1, len(chain)), 2),
                'approved_count': sum(1 for row in chain if bool(row.get('approved', False) or str(row.get('status', '')).lower() == 'approved')),
                'stable_count': sum(1 for row in chain if bool(row.get('stable_parent', False))),
                'mechanisms': mechanisms[-4:],
                'lineage': chain,
            }
            lineage['fitness'] = (
                lineage['total_score']
                + lineage['approved_count'] * 3
                + lineage['stable_count'] * 2
                + max(0, lineage['depth'] - 1) * 2
                + min(3, len(set(lineage['mechanisms'])))
                - lineage['failures_total']
            )
            scored.append(lineage)

        scored.sort(
            key=lambda row: (
                float(row.get('fitness', 0) or 0),
                float(row.get('avg_score', 0) or 0),
                int(row.get('depth', 0) or 0),
                int(row.get('usage_total', 0) or 0),
            ),
            reverse=True,
        )
        return scored[:max(1, int(limit))]

    def build_lineage_report(self, limit: int = 5) -> str:
        lineages = self.get_best_lineages(limit=limit)
        if not lineages:
            return 'Aucune lignée évolutive disponible.'
        lines: List[str] = ['=== TOP LIGNÉES ÉVOLUTIVES ===']
        for idx, row in enumerate(lineages, start=1):
            chain = row.get('lineage', []) or []
            chain_str = ' -> '.join(str(item.get('equation', '')).strip() for item in chain if str(item.get('equation', '')).strip())
            mech = ', '.join(dict.fromkeys(row.get('mechanisms', []) or [])) or '-'
            lines.append(
                f"[{idx}] fitness={row.get('fitness', 0)} | depth={row.get('depth', 0)} | avg={row.get('avg_score', 0)} | approved={row.get('approved_count', 0)} | stable={row.get('stable_count', 0)}"
            )
            lines.append(f"     root : {row.get('root_equation', '')}")
            lines.append(f"     leaf : {row.get('leaf_equation', '')}")
            lines.append(f"     mechanisms : {mech}")
            lines.append(f"     chaîne : {chain_str}")
        return "\n".join(lines)


    def get_observability_snapshot(self) -> Dict[str, Any]:
        latest_eq = self.get_latest_equation_entry() or {}
        latest_var = self.get_last_validated_variable() or {}
        best_lineages = self.get_best_lineages(limit=3)
        return {
            'latest_equation': latest_eq,
            'latest_variable': latest_var,
            'best_equations': self.get_best_equations(limit=3),
            'best_lineages': best_lineages,
            'repair_patterns': self.get_top_repair_patterns(limit=3),
            'mutation_ready': self.can_start_mutation(),
            'repair_ready': self.can_start_repair(),
        }

    def cleanup_memory(self) -> None:
        bad_eqs = {eq for eq, score in self.data.get("equation_scores", {}).items() if score < -3}
        if bad_eqs:
            self.data["approved_equations"] = [e for e in self.data["approved_equations"] if e.get("equation") not in bad_eqs]
            self.data["partial_equations"] = [e for e in self.data["partial_equations"] if e.get("equation") not in bad_eqs]

        bad_vars = {name for name, score in self.data.get("variable_scores", {}).items() if score < -3}
        if bad_vars:
            for name in bad_vars:
                self._drop_symbol_from_store("approved_variables", name)
                self._drop_symbol_from_store("candidate_variables", name)
        self.save()

    def get_best_equations(self, limit: int = 3) -> List[Dict[str, Any]]:
        eqs = self.data.get("approved_equations", []) or self.data.get("partial_equations", [])
        def score(item: Dict[str, Any]) -> int:
            return self.data["equation_scores"].get(item.get("equation", ""), 0)
        return sorted(eqs, key=score, reverse=True)[:limit]

    def suggest_fusion(self) -> Optional[str]:
        eqs = self.get_best_equations(2)
        if len(eqs) < 2:
            return None
        eq1 = eqs[0].get("equation", "")
        eq2 = eqs[1].get("equation", "")
        if not eq1 or not eq2 or eq1 == eq2:
            return None
        return f"Fusion candidate : combiner {eq1} et {eq2}"
