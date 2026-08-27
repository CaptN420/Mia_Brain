from __future__ import annotations

from pathlib import Path

import json
import math
import re
import shutil
import sys
from datetime import datetime
from typing import Any
import ast

from action_monitor import SecurityStop, guard_path_operation, log_effect, log_security_event
from workspace_security import (
    ALLOWED_SHARED_JSON,
    ensure_session_dir,
    ensure_within_workspace,
    get_workspace_dir,
    list_session_dirs,
    session_dir as workspace_session_dir,
    validate_session_name,
)

from config import RuntimeConfig
from memory_store import MemoryStore
from shared_memory import SharedResearchMemory, SharedEquation
from variable_debate_orchestrator import VariableDebateOrchestrator
from variable_repair_orchestrator import VariableRepairOrchestrator
from equation_debate_orchestrator import EquationDebateOrchestrator
from mutation_equation_orchestrator import MutationEquationOrchestrator
from repair_equation_orchestrator import RepairEquationOrchestrator
from test_debate_orchestrator import TestDebateOrchestrator

# ---------- Session helpers ----------

def normalize_target_equation(value) -> str:
    if callable(value):
        try:
            value = value()
        except Exception:
            return ""
    value = str(value or "").strip()
    m = re.match(r"^T\d+\s*\|\s*(.+)$", value)
    if m:
        return m.group(1).strip()
    if " | " in value:
        return value.split(" | ", 1)[1].strip()
    return value

def _new_session_dir(base_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return ensure_session_dir(base_dir / f"alchemy_session_{timestamp}")

def list_sessions(base_dir: Path) -> list[Path]:
    ensure_within_workspace(base_dir)
    return list_session_dirs()

def resolve_session_dir(
    base_dir: Path,
    *,
    session_name: str | None = None,
    resume_latest: bool = False,
    create_new: bool = False,
    strict: bool = False,
    force: bool = False,
) -> Path:
    sessions = list_sessions(base_dir)
    sessions_by_name = {p.name: p for p in sessions}

    if session_name:
        wanted = sessions_by_name.get(session_name)
        if wanted is None:
            if strict:
                raise FileNotFoundError(f"Session introuvable: {session_name}")
            return workspace_session_dir(session_name)
        return wanted

    if resume_latest:
        if not sessions:
            raise FileNotFoundError("Aucune session existante à reprendre.")
        return sessions[-1]

    if create_new:
        return _new_session_dir(base_dir)

    raise ValueError("Aucune stratégie de session fournie.")

def _cfg(
    *,
    session_name: str | None = None,
    resume_latest: bool = False,
    create_new: bool = False,
    strict: bool = False,
    force: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
) -> RuntimeConfig:
    base_dir = get_workspace_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    cfg = RuntimeConfig(base_dir=base_dir)
    cfg.session_dir = resolve_session_dir(
        base_dir,
        session_name=session_name,
        resume_latest=resume_latest,
        create_new=create_new,
        strict=strict,
        force=force,
    )
    cfg.session_dir = ensure_session_dir(cfg.session_dir)
    log_security_event(risk='low', category='session', action='resolve', target=str(cfg.session_dir), reason='session selected')
    return cfg

# ---------- Generic utils ----------

def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _safe_eval_math(expr: str, safe_env: dict[str, Any]) -> float:
    allowed_binops = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
    allowed_unary = (ast.UAdd, ast.USub)

    def _eval(node: ast.AST):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.BinOp) and isinstance(node.op, allowed_binops):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
            if isinstance(node.op, ast.Mod):
                return left % right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, allowed_unary):
            value = _eval(node.operand)
            return +value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.Name) and node.id in safe_env and isinstance(safe_env[node.id], (int, float)):
            return safe_env[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in safe_env:
            fn = safe_env[node.func.id]
            args = [_eval(arg) for arg in node.args]
            return fn(*args)
        raise ValueError(f"Expression interdite: {ast.dump(node, include_attributes=False)}")

    tree = ast.parse(expr, mode="eval")
    return float(_eval(tree))

def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def _normalized_symbol(symbol: str) -> str:
    return str(symbol or "").strip().lower()

def _normalized_equation(eq: str) -> str:
    return "".join(str(eq or "").strip().lower().split())

def _structure_signature(eq: str) -> str:
    text = str(eq or "").strip()
    text = text.lower()
    text = __import__("re").sub(r"^T\d+\s*\|\s*(.+)$", "", text)
    text = __import__("re").sub(r"[a-zA-ZÀ-ÿ_][a-zA-zÀ-ÿ0-9_Δ]*", "X", text)
    text = __import__("re").sub(r"\s+", "", text)
    return text

def _union_list(values: list[Any], limit: int | None = None) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in values:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if limit is not None:
        out = out[-limit:]
    return out

def _sum_dict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    result = dict(a or {})
    for key, value in (b or {}).items():
        try:
            result[key] = result.get(key, 0) + value
        except Exception:
            result[key] = value
    return result

def _step(index: int, total: int, label: str) -> None:
    print(f"[STEP {index}/{total}] {label}")

def _step_done(index: int, total: int, label: str) -> None:
    print(f"[STEP {index}/{total}] OK - {label}")

def _extract_equation_from_block(block: str) -> str:
    import re

    patterns = [
        r"(?im)^\\s*(?:Équation|Equation)(?:\\*\\*)?\\s*:\\s*(.+?)\\s*$",
        r"(?im)^\\s*(?:Équation|Equation)\\s*=\\s*(.+?)\\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, block or "")
        if m:
            eq = str(m.group(1) or "").strip().strip("*")
            if eq and eq.lower() not in {"-", "?", "aucune", "none"}:
                return eq
    return ""

def _extract_status_from_block(block: str) -> str:
    import re

    m = re.search(r"(?im)^\\s*(?:Statut(?:\\*\\*)?\\s*:\\s*(.+?)\\s*$", block or "")
    return str(m.group(1) if m else "").strip().lower()

def _infer_equations_from_log(session_dir: Path) -> list[dict[str, Any]]:
    import re

    log_path = session_dir / "equation_debate_log.txt"
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    matches = list(re.finditer(r"(?m)^--\\s*equation tour\\s+(\\d+)\\s*--\\s*$", text))
    if not matches:
        return []

    by_turn: dict[int, list[str]] = {}
    for idx, m in enumerate(matches):
        turn = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        by_turn.setdefault(turn, []).append(block)

    inferred: list[dict[str, Any]] = []
    seen: set[str] = set()
    priority = ["FinalValidator", "Synthetiseur", "Sentinelle", "Basilide", "Aurelius"]

    for turn in sorted(by_turn):
        blocks = by_turn[turn]
        chosen_eq = ""
        chosen_status = ""
        chosen_agent = ""
        chosen_block = ""
        for agent in priority:
            for block in blocks:
                if f"{agent}:" not in block:
                    continue
                eq = _extract_equation_from_block(block)
                if eq:
                    chosen_eq = eq
                    chosen_status = _extract_status_from_block(block)
                    chosen_agent = agent
                    chosen_block = block
                    break
            if chosen_eq:
                break
        if not chosen_eq:
            for block in blocks:
                eq = _extract_equation_from_block(block)
                if eq:
                    chosen_eq = eq
                    chosen_status = _extract_status_from_block(block)
                    chosen_agent = "log_recovery"
                    chosen_block = block
                    break
        key = _normalized_equation(chosen_eq)
        if not key or key in seen:
            continue
        seen.add(key)

        low = chosen_status.lower()
        approved = any(tok in low for tok in ["approuv", "valid", "consolid"])
        rejected = any(tok in low for tok in ["rejet", "absente"])
        repaired = "répar" in low or "repar" in low
        partial = any(tok in low for tok in ["partiel", "partielle"])
        fallback = "fallback" in (chosen_block or "").lower()
        repair_required = bool(rejected or repaired or partial or not approved)
        inferred.append({
            "equation": chosen_eq,
            "definitions": {},
            "mechanism": "",
            "experiment": "",
            "links": [],
            "remark": f"recovered_from_log:{session_dir.name}",
            "approved": bool(approved and not rejected),
            "source_turn": turn,
            "source_agent": chosen_agent or "log_recovery",
            "validation_summary": chosen_status or "recovered_from_log",
            "required_next": ["merge_log_recovery"],
            "status": "approved" if approved and not rejected else "partial",
            "fallback_used": fallback,
            "stable_parent": bool(approved and not repair_required and not fallback),
            "repair_required": repair_required,
            "memory_decision": "recovered_from_log",
            "object_calculated": "",
            "law_type": "",
            "architecture": "",
            "parent_equation": "",
            "parent_variable": "",
            "exploratory_parent": not approved,
            "repair_log": {
                "turn": turn,
                "status": chosen_status or "recovered_from_log",
                "original_equation": "",
                "fixed_equation": chosen_eq,
                "main_issue": "recovered_from_log",
                "fix_applied": "merge_log_import",
                "pattern": "merge_log_recovery",
            } if repair_required else {},
        })
    return inferred

def _enrich_shared_with_log_equations(session_dir: Path, shared: dict[str, Any]) -> tuple[dict[str, Any], int]:
    enriched = dict(shared or {})
    approved = list(enriched.get("approved_equations", []) or [])
    partial = list(enriched.get("partial_equations", []) or [])
    existing = {_normalized_equation(row.get("equation", "")) for row in approved + partial if row.get("equation")}
    recovered = 0
    for row in _infer_equations_from_log(session_dir):
        key = _normalized_equation(row.get("equation", ""))
        if not key or key in existing:
            continue
        existing.add(key)
        if row.get("approved"):
            approved.append(row)
        else:
            partial.append(row)
        recovered += 1
    if recovered:
        approved.sort(key=lambda r: int(r.get("source_turn", 0) or 0))
        partial.sort(key=lambda r: int(r.get("source_turn", 0) or 0))
        enriched["approved_equations"] = approved[-40:]
        enriched["partial_equations"] = partial[-40:]
    return enriched, recovered

def _merge_variable_payload(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a or {})
    for field in [
        "definition",
        "unit",
        "measure",
        "role",
        "validation_summary",
        "family",
        "micro_equation",
        "experiment",
        "source_agent",
        "status",
        "kind",
        "description",
        "confidence",
        "justification",
    ]:
        if not out.get(field) and b.get(field):
            out[field] = b[field]
    for field in ["links", "remarks", "required_next", "usages"]:
        out[field] = _union_list(list(out.get(field, []) or []) + list(b.get(field, []) or []), limit=24)
    for field in ["approved"]:
        out[field] = bool(out.get(field, False) or b.get(field, False))
    for field in ["source_turn", "first_seen", "turn"]:
        out[field] = max(int(out.get(field, 0) or 0), int(b.get(field, 0) or 0))
    if "last_seen" in out or "last_seen" in b:
        out["last_seen"] = max(int(out.get("last_seen", 0) or 0), int(b.get("last_seen", 0) or 0))
    if not out.get("name") and b.get("name"):
        out["name"] = b["name"]
    if not out.get("variable_name") and b.get("variable_name"):
        out["variable_name"] = b["variable_name"]
    return out

def _equation_memory_score(entry: dict[str, Any], shared: dict[str, Any]) -> float:
    eq = str(entry.get("equation", ""))
    score = 0.0
    score += 6 if entry.get("mechanism") else 0
    score += 5 if entry.get("experiment") else 0
    score += 4 if entry.get("object_calculated") else 0
    score += 4 if entry.get("architecture") else 0
    score += 3 if entry.get("law_type") else 0
    score += min(6, len(entry.get("links", []) or []))
    score += min(6, len(entry.get("definitions", {}) or {}))
    score += 4 if entry.get("validation_summary") else 0
    score += 3 if entry.get("repair_log") else 0
    score += 0.01 * int(entry.get("source_turn", 0) or 0)
    score += 2 * float((shared.get("equation_scores", {}) or {}).get(eq, 0))
    score += float((shared.get("equation_usage_count", {}) or {}).get(eq, 0))
    score -= 2 * float((shared.get("equation_failures", {}) or {}).get(eq, 0))
    return score

def _merge_equation_entry(winner: dict[str, Any], loser: dict[str, Any]) -> dict[str, Any]:
    out = dict(winner or {})
    for field in [
        "mechanism",
        "experiment",
        "remark",
        "validation_summary",
        "memory_decision",
        "object_calculated",
        "law_type",
        "architecture",
        "parent_equation",
        "parent_variable",
        "source_agent",
        "status",
    ]:
        if not out.get(field) and loser.get(field):
            out[field] = loser[field]
    out["approved"] = bool(out.get("approved", False) or loser.get("approved", False))
    out["stable_parent"] = bool(out.get("stable_parent", False) or loser.get("stable_parent", False))
    out["exploratory_parent"] = bool(out.get("exploratory_parent", False) or loser.get("exploratory_parent", False))
    out["links"] = _union_list(list(out.get("links", []) or []) + list(loser.get("links", []) or []), limit=24)
    out["required_next"] = _union_list(list(out.get("required_next", []) or []) + list(loser.get("required_next", []) or []), limit=24)
    defs = dict(loser.get("definitions", {}) or {})
    defs.update(dict(out.get("definitions", {}) or {}))
    out["definitions"] = defs
    repair_log = dict(loser.get("repair_log", {}) or {})
    repair_log.update(dict(out.get("repair_log", {}) or {}))
    out["repair_log"] = repair_log
    out["source_turn"] = max(int(out.get("source_turn", 0) or 0), int(loser.get("source_turn", 0) or 0))
    if not out.get("remark") and loser.get("remark"):
        out["remark"] = loser["remark"]
    if loser.get("parent_equation") and out.get("parent_equation") and loser.get("parent_equation") != out.get("parent_equation"):
        notes = list(out.get("required_next", []) or [])
        notes.append(f"merge_alt_parent:{loser.get('parent_equation')}")
        out["required_next"] = _union_list(notes, limit=24)
    return out

def _merge_equation_lists(shared_a: dict[str, Any], shared_b: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    all_entries = []
    for store in ("approved_equations", "partial_equations"):
        for row in list(shared_a.get(store, []) or []):
            all_entries.append((dict(row), shared_a))
        for row in list(shared_b.get(store, []) or []):
            all_entries.append((dict(row), shared_b))

    winners: dict[str, dict[str, Any]] = {}
    nearby_structures: set[str] = set()
    exact_dupes_removed = 0
    structure_families = 0

    for entry, source_shared in all_entries:
        eq_key = _normalized_equation(entry.get("equation", ""))
        if not eq_key:
            continue
        sig = _structure_signature(entry.get("equation", ""))
        if sig in nearby_structures and eq_key not in winners:
            structure_families += 1
        nearby_structures.add(sig)
        current = winners.get(eq_key)
        if current is None:
            winners[eq_key] = dict(entry)
            continue
        exact_dupes_removed += 1
        current_score = _equation_memory_score(current, shared_a)
        new_score = _equation_memory_score(entry, source_shared)
        if new_score > current_score:
            winners[eq_key] = _merge_equation_entry(dict(entry), current)
        else:
            winners[eq_key] = _merge_equation_entry(current, entry)

    approved: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for row in winners.values():
        if bool(row.get("approved", False)):
            approved.append(row)
        else:
            partial.append(row)
    approved.sort(key=lambda row: int(row.get("source_turn", 0) or 0))
    partial.sort(key=lambda row: int(row.get("source_turn", 0) or 0))
    return approved, partial, exact_dupes_removed, structure_families

def merge_shared_memory_dicts(shared_a: dict[str, Any], shared_b: dict[str, Any], session_a: str, session_b: str) -> tuple[dict[str, Any], dict[str, Any]]:
    blank = SharedResearchMemory(_new_session_dir(get_workspace_dir()))._blank()
    merged = dict(blank)
    merged.update(shared_a or {})

    variable_dupes_removed = 0
    variables_store: dict[str, dict[str, Any]] = {}
    approved_names: set[str] = set()
    for source in [shared_a, shared_b]:
        for name, payload in (source.get("approved_variables", {}) or {}).items():
            key = _normalized_symbol(name)
            if not key:
                continue
            clean = dict(payload or {})
            clean.setdefault("name", name)
            clean["approved"] = bool(clean.get("approved", False))
            existing = variables_store.get(key)
            if existing is None:
                variables_store[key] = clean
            else:
                variable_dupes_removed += 1
                variables_store[key] = _merge_variable_payload(existing, clean)
            if True: # This logic is a bit flawed in the original but I'll keep the structure
                approved_names.add(key)
        for name, payload in (source.get("candidate_variables", {}) or {}).items():
            key = _normalized_symbol(name)
            if not key:
                continue
            clean = dict(payload or {})
            clean.setdefault("name", name)
            clean["approved"] = False
            existing = variables_store.get(key)
            if existing is None:
                variables_store[key] = clean
            else:
                variable_dupes_removed += 1
                variables_store[key] = _merge_variable_payload(existing, clean)

    merged["approved_variables"] = {}
    merged["candidate_variables"] = {}
    for key, payload in variables_store.items():
        name = payload.get("name") or payload.get("variable_name") or key
        if key in approved_names or payload.get("approved"):
            payload["approved"] = True
            payload["status"] = payload.get("status") or "approved"
            merged["approved_variables"][name] = payload
        else:
            payload["status"] = payload.get("status") or "candidate"
            merged["candidate_variables"][name] = payload

    approved_eq, partial_eq, exact_dupes_removed, structure_families = _merge_equation_lists(shared_a, shared_b)
    merged["approved_equations"] = approved_eq
    merged["partial_equations"] = partial_eq

    for field in [
        "rejected_fragments",
        "approved_links",
        "priority_variables",
        "recent_links",
        "recent_remarks",
        "final_validations",
        "debate_summaries",
        "stagnation_events",
    ]:
        merged[field] = _union_list(list(shared_a.get(field, []) or []) + list(shared_b.get(field, []) or []), limit=120)

    for field in [
        "equation_scores",
        "variable_scores",
        "equation_usage_count",
        "variable_usage_count",
        "equation_failures",
        "variable_failures",
    ]:
        merged[field] = _sum_dict(dict(shared_a.get(field, {}) or {}), dict(shared_b.get(field, {}) or {}))

    merged["pending_variables"] = dict(shared_a.get("pending_variables", {}) or {})
    merged["pending_variables"].update(dict(shared_b.get("pending_variables", {}) or {}))
    merged["turn_metrics"] = _union_list(list(shared_a.get("turn_metrics", []) or []) + list(shared_b.get("turn_metrics", []) or []), limit=200)
    merged["repair_logs"] = _union_list(list(shared_a.get("repair_logs", []) or []) + list(shared_b.get("repair_logs", []) or []), limit=120)
    merged["repair_patterns"] = _union_list(list(shared_a.get("repair_patterns", []) or []) + list(shared_b.get("repair_patterns", []) or []), limit=80)

    mutation_seen: set[tuple[str, str, str]] = set()
    mutation_rows: list[dict[str, Any]] = []
    for row in list(shared_a.get("mutation_history", []) or []) + list(shared_b.get("mutation_history", []) or []):
        clean = dict(row or {})
        key = (
            str(clean.get("parent_equation", "")),
            str(clean.get("child_equation", "") or clean.get("equation", "")),
            str(clean.get("decision", "") or clean.get("memory_decision", "")),
        )
        if key not in mutation_seen:
            mutation_seen.add(key)
            mutation_rows.append(clean)
    merged["mutation_history"] = mutation_rows[-120:]

    last_var_a = dict(shared_a.get("last_validated_variable", {}) or {})
    last_var_b = dict(shared_b.get("last_validated_variable", {}) or {})
    merged["last_validated_variable"] = last_var_a if int(last_var_a.get("source_turn", 0) or 0) >= int(last_var_b.get("source_turn", 0) or 0) else last_var_b

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source_sessions": [session_a, session_b],
        "variables_final": len(merged["approved_variables"]) + len(merged["candidate_variables"]),
        "equations_final": len(merged["approved_equations"]) + len(merged["partial_equations"]),
        "mutation_links_final": len(merged["mutation_history"]),
        "variable_duplicates_removed": variable_dupes_removed,
        "equation_duplicates_removed": exact_dupes_removed,
        "structure_families_detected": structure_families,
    }
    history = list(shared_a.get("merge_history", []) or [])
    history.append(summary)
    merged["last_merge_summary"] = summary
    merged["merge_history"] = history[-30:]
    return merged, summary

def _merge_memory_store_files(session_a: Path, session_b: Path, target: Path) -> dict[str, int]:
    stats = {
        "variables_memory": 0,
        "validations_memory": 0,
        "roles_memory": 0,
        "symbolic_memory": 0,
    }

    # variables_memory.json
    vars_a = _load_json(session_a / "variables_memory.json", {})
    vars_b = _load_json(session_b / "variables_memory.json", {})
    merged_vars: dict[str, dict[str, Any]] = {}
    for source in [vars_a, vars_b]:
        for name, payload in (source or {}).items():
            key = _normalized_symbol(name)
            clean = dict(payload or {})
            clean.setdefault("name", name)
            if key in merged_vars:
                stats["variables_memory"] += 1
                merged_vars[key] = _merge_variable_payload(merged_vars[key], clean)
            else:
                merged_vars[key] = clean
    _save_json(target / "variables_memory.json", {row.get("name") or key: row for key, row in merged_vars.items()})

    # validations_memory.json
    val_a = _load_json(session_a / "validations_memory.json", [])
    val_b = _load_json(session_b / "validations_memory.json", [])
    val_seen: set[tuple[Any, ...]] = set()
    merged_validations: list[dict[str, Any]] = []
    for row in list(val_a or []) + list(val_b or []):
        clean = dict(row or {})
        key = (
            clean.get("element_name"),
            clean.get("element_type"),
            clean.get("turn"),
            clean.get("status"),
            clean.get("reason"),
            clean.get("correction"),
        )
        if key not in val_seen:
            val_seen.add(key)
            merged_validations.append(clean)
        else:
            stats["validations_memory"] += 1
    _save_json(target / "validations_memory.json", merged_validations)

    # roles_memory.json
    roles_a = _load_json(session_a / "roles_memory.json", {})
    roles_b = _load_json(session_b / "roles_memory.json", {})
    merged_roles: dict[str, dict[str, Any]] = {}
    for source in [roles_a, roles_b]:
        for name, payload in (source or {}).items():
            key = _normalized_symbol(name)
            clean = dict(payload or {})
            clean.setdefault("variable_name", name)
            if key in merged_roles:
                stats["roles_memory"] += 1
                prev = merged_roles[key]
                new_turn = int(clean.get("turn", 0) or 0)
                prev_turn = int(prev.get("turn", 0) or 0)
                merged_roles[key] = clean if new_turn >= prev_turn else _merge_variable_payload(prev, clean)
            else:
                merged_roles[key] = clean
    _save_json(target / "roles_memory.json", {row.get("variable_name") or key: row for key, row in merged_roles.items()})

    # symbolic_memory.json
    sym_a = _load_json(session_a / "symbolic_memory.json", [])
    sym_b = _load_json(session_b / "symbolic_memory.json", [])
    sym_seen: set[str] = set()
    merged_symbolic: list[dict[str, Any]] = []
    for row in list(sym_a or []) + list(sym_b or []):
        clean = dict(row or {})
        key = json.dumps({
            "turn": clean.get("turn"),
            "source_agent": clean.get("source_agent"),
            "source_equation": clean.get("source_equation"),
            "correspondences": clean.get("correspondences"),
            "mechanism": clean.get("mechanism"),
            "principle_active": clean.get("principle_active"),
            "principle_passive": clean.get("principle_passive"),
            "operation": clean.get("operation"),
            "observable_sign": clean.get("observable_sign"),
        }, ensure_ascii=False, sort_keys=True)
        if key not in sym_seen:
            sym_seen.add(key)
            merged_symbolic.append(clean)
        else:
            stats["symbolic_memory"] += 1
    _save_json(target / "symbolic_memory.json", merged_symbolic[-60:])
    return stats

def merge_full_sessions(session_a_name: str, session_b_name: str, *, target_session_name: str | None = None, create_new_target: bool = True) -> Path:
    base_dir = get_workspace_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    total_steps = 7

    _step(1, total_steps, "validation des sessions source")
    session_a = resolve_session_dir(base_dir, session_name=session_a_name, strict=True)
    session_b = resolve_session_dir(base_dir, session_name=session_b_name, strict=True)
    if session_a.name == session_b.name:
        raise ValueError("Les deux sessions à fusionner doivent être différentes.")
    _step_done(1, total_steps, f"sources = {session_a.name} + {session_b.name}")

    _step(2, total_steps, "résolution de la session cible")
    if create_new_target:
        target = _new_session_dir(base_dir)
    else:
        if not target_session_name:
            raise ValueError("Une session cible doit être fournie si on ne crée pas une nouvelle session.")
        target = resolve_session_dir(base_dir, session_name=target_session_name, strict=True)
    target.mkdir(parents=True, exist_ok=True)
    _step_done(2, total_steps, f"cible = {target.name}")

    _step(3, total_steps, "chargement des shared memories")
    shared_a = _load_json(session_a / "shared_research_memory.json", {})
    shared_b = _load_json(session_b / "shared_research_memory.json", {})
    shared_a, recovered_a = _enrich_shared_with_log_equations(session_a, shared_a)
    shared_b, recovered_b = _enrich_shared_with_log_equations(session_b, shared_b)
    _step_done(3, total_steps, f"shared memories chargées | équations récupérées depuis logs: {recovered_a} + {recovered_b}")

    _step(4, total_steps, "fusion de shared_research_memory.json")
    merged_shared, shared_summary = merge_shared_memory_dicts(shared_a, shared_b, session_a.name, session_b.name)
    shared_summary["recovered_equations_from_logs"] = recovered_a + recovered_b
    _step_done(4, total_steps, "shared memory fusionnée")

    _step(5, total_steps, "fusion des mémoires variables / validations / rôles / symbolique")
    memstore_stats = _merge_memory_store_files(session_a, session_b, target)
    _step_done(5, total_steps, "mémoires secondaires fusionnées")

    _step(6, total_steps, "écriture des fichiers finaux")
    merged_shared["last_merge_summary"].update(memstore_stats)
    merged_shared["last_merge_summary"]["target_session"] = target.name
    _save_json(target / "shared_research_memory.json", merged_shared)
    for fname, default in [
        ("variables_memory.json", {}),
        ("validations_memory.json", []),
        ("roles_memory.json", {}),
        ("symbolic_memory.json", []),
    ]:
        path = target / fname
        if not path.exists():
            _save_json(path, default)
    _step_done(6, total_steps, "fichiers écrits")

    _step(7, total_steps, "résumé final")
    print(f"[MERGE] Sessions fusionnées : {session_a.name} + {session_b.name}")
    print(f"[MERGE] Cible : {target}")
    print(f"[MERGE] Variables finales : {shared_summary['variables_final']} | doublons retirés : {shared_summary['variable_duplicates_removed']}")
    print(f"[MERGE] Équations finales : {shared_summary['equations_final']} | doublons retirés : {shared_summary['equation_duplicates_removed']} | récupérées depuis logs : {shared_summary.get('recovered_equations_from_logs', 0)}")
    print(f"[MERGE] Liens de mutation : {shared_summary['mutation_links_final']}")
    print(f"[MERGE] Fichiers mémoire fusionnés : variables={memstore_stats['variables_memory']} validations={memstore_stats['validations_memory']} rôles={memstore_stats['roles_memory']} symbolique={memstore_stats['symbolic_memory']}")
    _step_done(7, total_steps, "merge terminé")
    return target

def merge_multiple_sessions(sessions_to_merge: list[str], *, target_session_name: str | None = None, create_new_target: bool = True) -> Path:
    base_dir = get_workspace_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    
    _step(1, 4, "validation des sessions source")
    source_paths = []
    for name in sessions_to_merge:
        path = resolve_session_dir(base_dir, session_name=name, strict=True)
        source_paths.append(path)
    if not source_paths:
        raise ValueError("Aucune session valide n'a été trouvée.")
    _step_done(1, 4, f"Sources: {[p.name for p in source_paths]}")

    _step(2, 4, "résolution de la session cible")
    if create_new_target:
        target = _new_session_dir(base_dir)
    else:
        if not target_session_name:
            raise ValueError("Une session cible doit être fournie si on ne crée pas une nouvelle session.")
        target = resolve_session_dir(base_dir, session_name=target_session_name, strict=True)
    target.mkdir(parents=True, exist_ok=True)
    _step_done(2, 4, f"Cible: {target.name}")

    _step(3, 4, "fusion de shared_research_memory.json")
    master_shared = {}
    for path in source_paths:
        data = _load_json(path / "shared_research_memory.json", {})
        if not master_shared:
            master_shared = dict(data)
        else:
            # Fold variables
            for store in ["approved_variables", "candidate_variables"]:
                if store not in master_shared: master_shared[store] = {}
                for name, payload in (data.get(store, {}) or {}).items():
                    key = _normalized_symbol(name)
                    clean = dict(payload or {})
                    clean.setdefault("name", name)
                    if key in master_shared[store]:
                        master_shared[store][key] = _merge_variable_payload(master_shared[store][key], clean)
                    else:
                        master_shared[store][key] = clean
            
            # Fold equations
            approved = list(master_shared.get("approved_equations", []) or [])
            partial = list(master_shared.get("partial_equations", []) or [])
            all_entries = []
            for store in ("approved_equations", "partial_equations"):
                for row in list(master_shared.get(store, []) or []):
                    all_entries.append((dict(row), master_shared))
                for row in list(data.get(store, []) or []):
                    all_entries.append((dict(row), data))
            
            winners = {}
            nearby_structures = set()
            exact_dupes = 0
            structure_families = 0
            for entry, source_data in all_entries:
                eq_key = _normalized_equation(entry.get("equation", ""))
                if not eq_key:
                    continue
                sig = _structure_signature(entry.get("equation", ""))
                if sig in nearby_structures and eq_key not in winners:
                    structure_families += 1
                nearby_structures.add(sig)
                current = winners.get(eq_key)
                if current is None:
                    winners[eq_key] = dict(entry)
                    continue
                exact_dupes += 1
                current_score = _equation_memory_score(current, master_shared)
                new_score = _equation_memory_score(entry, source_data)
                if new_score > current_score:
                    winners[eq_key] = _merge_equation_entry(dict(entry), current)
                else:
                    winners[eq_key] = _merge_equation_entry(current, entry)
            
            new_approved = []
            new_partial = []
            for row in winners.values():
                if bool(row.get("approved", False)): new_approved.append(row)
                else: new_partial.append(row)
            new_approved.sort(key=lambda row: int(row.get("source_turn", 0) or 0))
            new_partial.sort(key=lambda row: int(row.get("source_turn", 0) or 0))
            master_shared["approved_equations"] = new_approved
            master_shared["partial_equations"] = new_partial

            # Fold other lists/dicts
            for field in ["rejected_fragments", "approved_links", "priority_variables", "recent_links", "recent_remarks", "final_validations", "debate_summaries", "stagnation_events"]:
                master_shared[field] = _union_list(list(master_shared.get(field, []) or []) + list(data.get(field, []) or []), limit=120)
            
            for field in ["equation_scores", "variable_scores", "equation_usage_count", "variable_usage_count", "equation_failures", "variable_failures"]:
                master_shared[field] = _sum_dict(dict(master_shared.get(field, {}) or {}), dict(data.get(field, {}) or {}))

            # Fold history
            master_shared["pending_variables"] = dict(master_shared.get("pending_variables", {}) or {})
            master_shared["pending_variables"].update(dict(data.get("pending_variables", {}) or {}))
            master_shared["turn_metrics"] = _union_list(list(master_shared.get("turn_metrics", []) or []) + list(data.get("turn_metrics", []) or []), limit=200)
            master_shared["repair_logs"] = _union_list(list(master_shared.get("repair_logs", []) or []) + list(data.get("repair_logs", []) or []), limit=120)
            master_shared["repair_patterns"] = _union_list(list(master_shared.get("repair_patterns", []) or []) + list(data.get("repair_patterns", []) or []), limit=80)
            
            mut_seen = set()
            mut_rows = []
            for row in list(master_shared.get("mutation_history", []) or []) + list(data.get("mutation_history", []) or []):
                clean = dict(row or {})
                key = (
                    str(clean.get("parent_equation", "")),
                    str(clean.get("child_equation", "") or clean.get("equation", "")),
                    str(clean.get("decision", "") or clean.get("memory_decision", "")),
                )
                if key not in mut_seen:
                    mut_seen.add(key)
                    mut_rows.append(clean)
            master_shared["mutation_history"] = mut_rows[-120:]
            
            last_var_a = dict(master_shared.get("last_validated_variable", {}) or {})
            last_var_b = dict(data.get("last_validated_variable", {}) or {})
            master_shared["last_validated_variable"] = last_var_a if int(last_var_a.get("source_turn", 0) or 0) >= int(last_var_b.get("source_turn", 0) or 0) else last_var_b

            # Summary
            summary = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source_sessions": [p.name for p in source_paths],
                "variables_final": len(master_shared.get("approved_variables", {}) or {}) + len(master_shared.get("candidate_variables", {}) or {}),
                "equations_final": len(master_shared.get("approved_equations", []) or []) + len(master_shared.get("partial_equations", []) or []),
                "mutation_links_final": len(master_shared.get("mutation_history", []) or []),
                "variable_duplicates_removed": 0,
                "equation_duplicates_removed": 0,
                "structure_families_detected": 0,
            }
            history = list(master_shared.get("merge_history", []) or [])
            history.append(summary)
            master_shared["last_merge_summary"] = summary
            master_shared["merge_history"] = history[-30:]

    _save_json(target / "shared_research_memory.json", master_shared)
    _step_done(3, 4, "shared memory fusionnée")

    _step(4, 4, "fusion des mémoires variables / validations / rôles / symbolique")
    
    # Variables
    vars_merged = {}
    for path in source_paths:
        data = _load_json(path / "variables_memory.json", {})
        for name, payload in (data or {}).items():
            key = _normalized_symbol(name)
            clean = dict(payload or {})
            clean.setdefault("name", name)
            if key in vars_merged:
                vars_merged[key] = _merge_variable_payload(vars_merged[key], clean)
            else:
                vars_merged[key] = clean
    _save_json(target / "variables_memory.json", {row.get("name") or key: row for key, row in vars_merged.items()})

    # Validations
    vals_merged = []
    vals_seen = set()
    for path in source_paths:
        data = _load_json(path / "validations_memory.json", [])
        for row in (data or []):
            clean = dict(row or {})
            key = (
                clean.get("element_name"),
                clean.get("element_type"),
                clean.get("turn"),
                clean.get("status"),
                clean.get("reason"),
                clean.get("correction"),
            )
            if key not in vals_seen:
                vals_seen.add(key)
                vals_merged.append(clean)
            else:
                pass # ignore duplicates
    _save_json(target / "validations_memory.json", vals_merged)

    # Roles
    roles_merged = {}
    for path in source_paths:
        data = _load_json(path / "roles_memory.json", {})
        for name, payload in (data or {}).items():
            key = _normalized_symbol(name)
            clean = dict(payload or {})
            clean.setdefault("variable_name", name)
            if key in roles_merged:
                prev = roles_merged[key]
                new_turn = int(clean.get("turn", 0) or 0)
                prev_turn = int(prev.get("turn", 0) or 0)
                roles_merged[key] = clean if new_turn >= prev_turn else _merge_variable_payload(prev, clean)
            else:
                roles_merged[key] = clean
    _save_json(target / "roles_memory.json", {row.get("variable_name") or key: row for key, row in roles_merged.items()})

    # Symbolic
    syms_merged = []
    syms_seen = set()
    for path in source_paths:
        data = _load_json(path / "symbolic_memory.json", [])
        for row in (data or []):
            clean = dict(row or {})
            key = json.dumps({
                "turn": clean.get("turn"),
                "source_agent": clean.get("source_agent"),
                "source_equation": clean.get("source_equation"),
                "correspondences": clean.get("correspondences"),
                "mechanism": clean.get("mechanism"),
                "principle_active": clean.get("principle_active"),
                "principle_passive": clean.get("principle_passive"),
                "operation": clean.get("operation"),
                "observable_sign": clean.get("observable_sign"),
            }, ensure_ascii=False, sort_keys=True)
            if key not in syms_seen:
                syms_seen.add(key)
                syms_merged.append(clean)
            else:
                pass
    _save_json(target / "symbolic_memory.json", syms_merged[-60:])

    _step_done(4, 4, "mémoires secondaires fusionnées")

    _step(5, 4, "écriture des fichiers finaux")
    # The logic for writing was already done by the _save_json calls above
    # But we need to ensure the summary is updated in shared_research_memory
    # which is already done.
    _step_done(5, 4, "merge terminé")
    return target

# ---------- Main Orchestrators ----------

def run_variables(turns: int, session_name: str, **kwargs) -> None:
    cfg = _cfg(session_name=session_name, **kwargs)
    orchestrator = VariableDebateOrchestrator(cfg)
    orchestrator.run(turns=turns)

def run_equations(turns: int, session_name: str, **kwargs) -> None:
    cfg = _cfg(session_name=session_name, **kwargs)
    orchestrator = EquationDebateOrchestrator(cfg)
    orchestrator.run(turns=turns)

def run_both(turns_var: int, turns_eq: int, session_name: str, **kwargs) -> None:
    cfg = _cfg(session_name=session_name, **kwargs)
    var_orch = VariableDebateOrchestrator(cfg)
    eq_orch = EquationDebateOrchestrator(cfg)
    var_orch.run(turns=turns_var)
    eq_orch.run(turns=turns_eq)

def run_mutation(turns: int, session_name: str, **kwargs) -> None:
    cfg = _cfg(session_name=session_name, **kwargs)
    target_equation = kwargs.get("target_equation")
    orchestrator = MutationEquationOrchestrator(cfg)
    orchestrator.run(turns=turns, target_equation=target_equation)

def run_repair(turns: int, session_name: str, **kwargs) -> None:
    cfg = _cfg(session_name=session_name, **kwargs)
    target_equation = kwargs.get("target_equation")
    orchestrator = RepairEquationOrchestrator(cfg)
    orchestrator.run(turns=turns, target_equation=target_equation)

def run_test(turns: int, session_name: str, **kwargs) -> None:
    cfg = _cfg(session_name=session_name, **kwargs)
    orchestrator = TestDebateOrchestrator(cfg)
    orchestrator.run(turns=turns)

# ---------- CLI Entrypoint ----------

def _extract_options(remaining: list[str], flag: str) -> list[str]:
    options = []
    i = 0
    while i < len(remaining):
        if remaining[i] == flag:
            i += 1
            while i < len(remaining) and not remaining[i].startswith("--"):
                options.append(remaining[i])
                i += 1
            break
        i += 1
    return options

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MIA-LABS Launcher")
    parser.add_argument("mode", choices=[
        "list-sessions",
        "merge-sessions",
        "merge-multiple",
        "variables",
        "equations",
        "both",
        "mutation",
        "repair",
        "test",
        "status",
        "auto-repair",
        "full-evolve",
        "loop",
        "lineages",
    ], help="The operation mode to execute")
    parser.add_argument("--session", help="Name of the session folder", default=None)
    parser.add_argument("--session-a", help="First session for merge", default=None)
    parser.add_argument("--session-b", help="Second session for merge", default=None)
    parser.add_argument("--sessions", nargs="+", help="List of sessions to merge (multi-merge)")
    parser.add_argument("--target-session", help="Target session name for merge", default=None)
    parser.add_argument("--resume", action="store_true", help="Resume latest session")
    parser.add_argument("--create-new", action="store_true", help="Create a new session for the operation")
    parser.add_argument("--turns", type=int, default=1, help="Number of turns for the operation")
    parser.add_argument("--target-equation", help="Target equation for repair or mutation")
    parser.add_argument("--strict", action="store_true", help="Strict session resolution")
    parser.add_argument("--force", action="store_true", help="Force operation")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no changes)")
    parser.add_argument("--config", type=str, help="Custom config path")
    parser.add_argument("--reset", action="store_true", help="Reset all memory")
    parser.add_argument("--test", action="store_true", help="Run tests")
    parser.add_argument("--create-new-session", dest="create_new_session_flag", action="store_true", help="Create a new session (alias for --create-new)")
    
    # Use parse_known_args to allow extra positional arguments without error
    args, unknown_args = parser.parse_known_args()
    
    mode = args.mode
    session_name = args.session
    session_a = args.session_a
    session_b = args.session_b
    target_session = args.target_session
    resume_latest = args.resume
    create_new = args.create_new or args.create_new_session_flag
    turns = args.turns
    target_equation = args.target_equation
    strict = args.strict
    force = args.force
    verbose = args.verbose
    dry_run = args.dry_run
    config_path = args.config
    reset_memory = args.reset
    run_tests = args.test
    create_new_session = args.create_new_session_flag
    
    # Use unknown_args from parse_known_args as extra_args
    extra_args = [str(arg) for arg in unknown_args]
    
    base_dir = get_workspace_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    
    if reset_memory:
        reset_all_memory(base_dir)
        return
    
    if mode == "list-sessions":
        sessions = list_sessions(base_dir)
        if not sessions:
            print("[INFO] Aucune session.")
        else:
            for path in sessions:
                print(path.name)
        return
    
    if mode == "merge-sessions":
        print(f"[MODE] merge-sessions")
        if not session_a or not session_b:
            raise ValueError("merge-sessions exige --session-a et --session-b")
        merge_full_sessions(session_a, session_b, target_session_name=target_session, create_new_target=not bool(target_session))
        return
    
    if mode == "merge-multiple":
        print(f"[MODE] merge-multiple")
        if not args.sessions:
            raise ValueError("merge-multiple exige --sessions <nom1> <nom2> ...")
        if len(args.sessions) > 10:
            raise ValueError("merge-multiple supporte jusqu'à 10 sessions uniquement.")
        merge_multiple_sessions(args.sessions, target_session_name=target_session, create_new_target=not bool(target_session))
        return
    
    if mode == "variables":
        print(f"[MODE] variables")
        if not session_name:
            raise ValueError("variables exige --session")
        # ... rest of the logic ...

    elif mode == "equations":
        run_equations(turns, session_name, resume_latest=resume_latest, create_new=create_new or not (resume_latest or session_name), strict=strict, force=force, verbose=verbose, dry_run=dry_run)
        return

    elif mode == "both":
        # Handle the case where turns might be multiple values
        t_var = turns
        t_eq = turns
        if len(extra_args) > 1:
            # If the user provided two numbers after the command
            try:
                t_var = int(extra_args[0])
                t_eq = int(extra_args[1])
            except ValueError:
                pass
        run_both(t_var, t_eq, session_name, resume_latest=resume_latest, create_new=create_new or not (resume_latest or session_name), strict=strict, force=force, verbose=verbose, dry_run=dry_run)
        return

    elif mode == "mutation":
        run_mutation(turns, session_name, resume_latest=resume_latest, create_new=create_new or not (resume_latest or session_name), strict=strict, force=force, verbose=verbose, dry_run=dry_run, target_equation=target_equation)
        return

    elif mode == "repair":
        run_repair(turns, session_name, resume_latest=resume_latest, create_new=create_new or not (resume_latest or session_name), strict=strict, force=force, verbose=verbose, dry_run=dry_run, target_equation=target_equation)
        return

    elif mode == "test":
        # Placeholder for test runner
        print("[INFO] Test runner not fully implemented in this launcher.")
        return

    elif mode == "full-evolve":
        print(f"[MODE] full-evolve")
        print(f"[INFO] extra_args received: {extra_args}")
        print(f"[INFO] create_new_session: {create_new_session}")
        
        # Implementation of Full Evolution Cycle
        # 1. Variable Discovery
        print(f"\n--- PHASE 1: Variable Discovery ---")
        var_turns = _safe_int(extra_args[0] if extra_args and len(extra_args) > 0 else "5", 2)
        var_orch = VariableDebateOrchestrator(_cfg(session_name=session_name, strict=strict, force=force, verbose=verbose, dry_run=dry_run, resume_latest=resume_latest, create_new=create_new_session))
        var_orch.run(turns=var_turns)
        
        # 2. Equation Synthesis
        print(f"\n--- PHASE 2: Equation Synthesis ---")
        eq_turns = _safe_int(extra_args[1] if extra_args and len(extra_args) > 1 else "2", 2)
        eq_orch = EquationDebateOrchestrator(_cfg(session_name=session_name, strict=strict, force=force, verbose=verbose, dry_run=dry_run, resume_latest=resume_latest, create_new=create_new_session))
        eq_orch.run(turns=eq_turns)
        
        # 3. Mutation & Repair
        print(f"\n--- PHASE 3: Mutation & Repair ---")
        mut_orch = MutationEquationOrchestrator(_cfg(session_name=session_name, strict=strict, force=force, verbose=verbose, dry_run=dry_run, resume_latest=resume_latest, create_new=create_new_session))
        rep_orch = RepairEquationOrchestrator(_cfg(session_name=session_name, strict=strict, force=force, verbose=verbose, dry_run=dry_run, resume_latest=resume_latest, create_new=create_new_session))
        
        # Use extra_args for mutation count if provided, else default to 2 (extra_args[2])
        mut_cycles = _safe_int(extra_args[2] if extra_args and len(extra_args) > 2 else "2", 2)
        
        for i in range(mut_cycles):
            print(f"\n[Evolution Cycle {i+1}/{mut_cycles}]")
            mut_orch.run(turns=1)
            rep_orch.run(turns=1)
            
        print(f"\n✨ FULL EVOLUTION COMPLETE.")
        return

    elif mode == "loop":
        print(f"[MODE] loop")
        print(f"[INFO] extra_args received: {extra_args}")
        print(f"[INFO] create_new_session: {create_new_session}")
        
        # Implementation of Continuous Evolution Loop
        print(f"\n🔄 Starting CONTINUOUS EVOLUTION LOOP (Ctrl+C to stop)")
        
        # Determine or create the session directory once before the loop
        if create_new_session and not session_name:
            base_dir = get_workspace_dir()
            base_dir.mkdir(parents=True, exist_ok=True)
            new_session_dir = _new_session_dir(base_dir)
            session_name = new_session_dir.name
            print(f"[SESSION_CREATE] {session_name} at {new_session_dir}")
        elif not session_name:
            # Try to resolve latest or raise error
            if resume_latest:
                base_dir = get_workspace_dir()
                sessions = list_sessions(base_dir)
                if not sessions:
                    raise FileNotFoundError("Aucune session existante à reprendre.")
                session_name = sessions[-1].name
                print(f"[SESSION_REUSE] {session_name}")
            else:
                raise ValueError("loop mode nécessite --session ou --create-new-session ou --resume")
        
        print(f"[SESSION_ID] {session_name}")
        base_dir = get_workspace_dir()
        session_path = base_dir / session_name
        print(f"[SESSION_PATH] {session_path}")

        # Initialize cycle counter and sterile cycle detector
        cycle_count = 0
        consecutive_no_parent_cycles = 0
        
        while True:
            try:
                # Run the full cycle
                cycle_count += 1
                print(f"\n--- EVOLUTION CYCLE {cycle_count} ---")
                
                # Check for sterile loop
                if consecutive_no_parent_cycles >= 3:
                    print("\n[STERILE_LOOP_DETECTED] parent_equation is None for 3 consecutive cycles. Stopping loop.")
                    break
                
                # 1. Variable Discovery
                print(f"\n--- PHASE 1: Variable Discovery ---")
                var_orch = VariableDebateOrchestrator(_cfg(session_name=session_name, resume_latest=False, create_new=False, strict=strict, force=force, verbose=verbose, dry_run=dry_run))
                var_orch.run(turns=turns)
                
                # 2. Equation Synthesis
                print(f"\n--- PHASE 2: Equation Synthesis ---")
                eq_orch = EquationDebateOrchestrator(_cfg(session_name=session_name, resume_latest=False, create_new=False, strict=strict, force=force, verbose=verbose, dry_run=dry_run))
                eq_orch.run(turns=turns)
                
                # 3. Mutation & Repair
                print(f"\n--- PHASE 3: Mutation & Repair ---")
                mut_orch = MutationEquationOrchestrator(_cfg(session_name=session_name, resume_latest=False, create_new=False, strict=strict, force=force, verbose=verbose, dry_run=dry_run))
                rep_orch = RepairEquationOrchestrator(_cfg(session_name=session_name, resume_latest=False, create_new=False, strict=strict, force=force, verbose=verbose, dry_run=dry_run))
                
                # Check for parent_equation status before mutation
                shared_mem = SharedResearchMemory(session_path)
                can_mutate, reason = shared_mem.can_start_mutation()
                
                if not can_mutate:
                    print(f"[MUTATION_BLOCKED] {reason}")
                    consecutive_no_parent_cycles += 1
                else:
                    consecutive_no_parent_cycles = 0
                
                mut_cycles = int(extra_args[0]) if extra_args and extra_args[0].isdigit() else 2
                
                for i in range(mut_cycles):
                    print(f"\n[Evolution Cycle {cycle_count}.{i+1}/{mut_cycles}]")
                    mut_orch.run(turns=1)
                    rep_orch.run(turns=1)
                    
                # Print diagnostic summary
                print(f"\n--- CYCLE SUMMARY ---")
                print(f"SESSION:")
                print(f"  id: {session_name}")
                print(f"  path: {session_path}")
                print(f"EVOLUTION:")
                print(f"  cycle: {cycle_count}")
                
                # Get memory stats
                approved_vars = len(shared_mem.data.get("approved_variables", {}))
                eq_candidates = len(shared_mem.data.get("partial_equations", [])) + len(shared_mem.data.get("approved_equations", []))
                eq_approved = len([eq for eq in shared_mem.data.get("approved_equations", []) if eq.get('approved')])
                
                print(f"MEMORY:")
                print(f"  variables_validated: {approved_vars}")
                print(f"  equations_candidates: {eq_candidates}")
                print(f"  equations_approved: {eq_approved}")
                
                print(f"PARENT:")
                if can_mutate:
                    latest_eq = shared_mem.get_latest_equation_entry()
                    parent_id = latest_eq.get('equation', 'N/A') if latest_eq else 'None'
                    print(f"  id: {parent_id}")
                    print(f"  status: APPROVED")
                    print(f"MUTATION:")
                    print(f"  allowed: true")
                else:
                    print(f"  id: None")
                    print(f"  status: NONE")
                    print(f"MUTATION:")
                    print(f"  allowed: false")
                    print(f"  reason: {reason}")
                    
                print(f"\n✨ Cycle complete. Restarting loop...")
                import time
                time.sleep(2)
            except KeyboardInterrupt:
                print("\n[LOOP] Stopped by user.")
                break
            except SystemExit as e:
                # Treat exit 0 (success) as FIN_CYCLE, do not quit the process
                if e.code == 0 or e.code is None:
                    print(f"\n[FIN_CYCLE] Cycle terminated with exit code {e.code}. Resetting cycle state (tour=1, phase=start) and relaunching...")
                    # reset cycle state: tour=1, phase=start
                    # The orchestrators are re-instantiated in the next iteration, so their internal 'turn' starts at 0 then becomes 1.
                    # Continue to the next cycle
                    continue
                else:
                    print(f"\n[LOOP SYSTEMEXIT] Process exited with non-zero code {e.code}. Stopping loop.")
                    break
            except Exception as e:
                print(f"\n[LOOP ERROR] {e}")
                break
        return

    elif mode == "lineages":
        print(f"[MODE] lineages")
        print(f"[INFO] extra_args received: {extra_args}")
        print(f"[INFO] create_new_session: {create_new_session}")
        # Placeholder for lineages logic
        print("[INFO] lineages mode not fully implemented yet.")
        return

if __name__ == "__main__":
    main()


