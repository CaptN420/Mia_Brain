from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from workspace_security import ensure_session_dir
from action_monitor import guard_path_operation, log_effect
from typing import Dict, List, Optional, Tuple


@dataclass
class DebateState:
    theme: str
    question: str
    history: List[Tuple[str, str]] = field(default_factory=list)
    tour: int = 1

    def add(self, speaker: str, text: str, max_history_messages: int = 16) -> None:
        self.history.append((speaker, text))
        if len(self.history) > max_history_messages:
            self.history = self.history[-max_history_messages:]


@dataclass
class VariableRecord:
    name: str
    kind: str = ""
    description: str = ""
    first_seen: int = 0
    last_seen: int = 0
    status: str = "new"
    usages: List[int] = field(default_factory=list)


@dataclass
class ValidationRecord:
    element_name: str
    element_type: str = ""
    turn: int = 0
    status: str = ""
    reason: str = ""
    correction: str = ""


@dataclass
class RoleRecord:
    variable_name: str
    role: str = ""
    turn: int = 0
    confidence: str = ""
    justification: str = ""


@dataclass
class SymbolicRecord:
    turn: int
    source_agent: str = ""
    source_equation: str = ""
    correspondences: List[str] = field(default_factory=list)
    mechanism: str = ""
    principle_active: str = ""
    principle_passive: str = ""
    operation: str = ""
    observable_sign: str = ""


class MemoryStore:
    def __init__(self, session_dir: Path):
        self.session_dir = ensure_session_dir(Path(session_dir))

        self.variables_file = self.session_dir / "variables_memory.json"
        self.validations_file = self.session_dir / "validations_memory.json"
        self.roles_file = self.session_dir / "roles_memory.json"
        self.symbolic_file = self.session_dir / "symbolic_memory.json"

        self.variables: Dict[str, VariableRecord] = self._load_variables()
        self.validations: List[ValidationRecord] = self._load_validations()
        self.roles: Dict[str, RoleRecord] = self._load_roles()
        self.symbolic: List[SymbolicRecord] = self._load_symbolic()

    def _load_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _load_variables(self) -> Dict[str, VariableRecord]:
        data = self._load_json(self.variables_file, {})
        if not isinstance(data, dict):
            return {}
        return {name: VariableRecord(**{k: v for k, v in payload.items() if k in VariableRecord.__annotations__}) for name, payload in data.items() if isinstance(payload, dict)}

    def _load_validations(self) -> List[ValidationRecord]:
        data = self._load_json(self.validations_file, [])
        if not isinstance(data, list):
            return []
        return [ValidationRecord(**{k: v for k, v in payload.items() if k in ValidationRecord.__annotations__}) for payload in data if isinstance(payload, dict)]

    def _load_roles(self) -> Dict[str, RoleRecord]:
        data = self._load_json(self.roles_file, {})
        if not isinstance(data, dict):
            return {}
        return {name: RoleRecord(**{k: v for k, v in payload.items() if k in RoleRecord.__annotations__}) for name, payload in data.items() if isinstance(payload, dict)}

    def _load_symbolic(self) -> List[SymbolicRecord]:
        data = self._load_json(self.symbolic_file, [])
        if not isinstance(data, list):
            return []
        return [SymbolicRecord(**{k: v for k, v in payload.items() if k in SymbolicRecord.__annotations__}) for payload in data if isinstance(payload, dict)]

    def save_all(self) -> None:
        guard_path_operation(self.variables_file, action='write', session_dir=str(self.session_dir))
        self.variables_file.write_text(
            json.dumps({name: asdict(record) for name, record in self.variables.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        guard_path_operation(self.validations_file, action='write', session_dir=str(self.session_dir))
        self.validations_file.write_text(
            json.dumps([asdict(record) for record in self.validations], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        guard_path_operation(self.roles_file, action='write', session_dir=str(self.session_dir))
        self.roles_file.write_text(
            json.dumps({name: asdict(record) for name, record in self.roles.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        guard_path_operation(self.symbolic_file, action='write', session_dir=str(self.session_dir))
        self.symbolic_file.write_text(
            json.dumps([asdict(record) for record in self.symbolic], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register_variable(self, name: str, turn: int, kind: str = "", description: str = "", status: str = "active") -> None:
        if not name:
            return
        record = self.variables.get(name)
        if record is None:
            self.variables[name] = VariableRecord(
                name=name,
                kind=kind,
                description=description,
                first_seen=turn,
                last_seen=turn,
                status=status,
                usages=[turn],
            )
            return

        record.last_seen = turn
        if turn not in record.usages:
            record.usages.append(turn)
        if kind:
            record.kind = kind
        if description:
            record.description = description
        if status:
            record.status = status

    def mark_variable_used(self, name: str, turn: int) -> None:
        self.register_variable(name=name, turn=turn, status="active")

    def add_validation(self, element_name: str, element_type: str, turn: int, status: str, reason: str = "", correction: str = "") -> None:
        if not element_name:
            return
        self.validations.append(
            ValidationRecord(
                element_name=element_name,
                element_type=element_type,
                turn=turn,
                status=status,
                reason=reason,
                correction=correction,
            )
        )

    def set_role(self, variable_name: str, role: str, turn: int, confidence: str = "moyen", justification: str = "") -> None:
        if not variable_name or not role:
            return
        self.roles[variable_name] = RoleRecord(
            variable_name=variable_name,
            role=role,
            turn=turn,
            confidence=confidence,
            justification=justification,
        )

    def add_symbolic_record(
        self,
        *,
        turn: int,
        source_agent: str,
        source_equation: str,
        correspondences: List[str],
        mechanism: str = "",
        principle_active: str = "",
        principle_passive: str = "",
        operation: str = "",
        observable_sign: str = "",
    ) -> None:
        self.symbolic.append(
            SymbolicRecord(
                turn=turn,
                source_agent=source_agent,
                source_equation=source_equation,
                correspondences=correspondences[:8],
                mechanism=mechanism,
                principle_active=principle_active,
                principle_passive=principle_passive,
                operation=operation,
                observable_sign=observable_sign,
            )
        )
        self.symbolic = self.symbolic[-30:]



    def clear_all(self) -> None:
        self.variables = {}
        self.validations = []
        self.roles = {}
        self.symbolic = []
        for path in [
            self.variables_file,
            self.validations_file,
            self.roles_file,
            self.symbolic_file,
        ]:
            try:
                if path.exists():
                    guard_path_operation(path, action='unlink', session_dir=str(self.session_dir))
                    path.unlink()
                    log_effect('file_deleted', target=str(path), risk='medium', session_dir=str(self.session_dir))
            except Exception:
                pass

    def get_active_variables(self, current_turn: int, max_gap: int = 5) -> List[Dict[str, object]]:
        results: List[Dict[str, object]] = []
        for record in self.variables.values():
            if record.status not in {"active", "stabilised", "stabilized", "validée", "valide"}:
                continue
            if current_turn and (current_turn - record.last_seen) > max_gap:
                continue
            results.append(asdict(record))
        results.sort(key=lambda item: (item.get("last_seen", 0), item.get("first_seen", 0)), reverse=True)
        return results


    def build_variable_bank(self) -> Dict[str, List[Dict[str, object]]]:
        useful: List[Dict[str, object]] = []
        unstable: List[Dict[str, object]] = []
        rejected: List[Dict[str, object]] = []
        promising: List[Dict[str, object]] = []
        experimental: List[Dict[str, object]] = []

        latest_status: Dict[str, str] = {}
        latest_reason: Dict[str, str] = {}
        for record in self.validations:
            latest_status[record.element_name] = record.status
            latest_reason[record.element_name] = record.reason

        for name, record in self.variables.items():
            payload = asdict(record)
            status = str(latest_status.get(name, record.status or "") or "").lower()
            payload["validation_status"] = status
            payload["validation_reason"] = str(latest_reason.get(name, "") or "")
            uses = len(record.usages or [])

            if status in {"rejected", "reject", "invalid"}:
                rejected.append(payload)
            elif status in {"unstable", "partial"}:
                unstable.append(payload)
            elif uses >= 2 and status in {"approved", "active", "validée", "valide", "stabilised", "stabilized"}:
                useful.append(payload)
            elif status in {"candidate", "new", ""}:
                promising.append(payload)
            else:
                experimental.append(payload)

        return {
            "useful": useful[:20],
            "unstable": unstable[:20],
            "rejected": rejected[:20],
            "promising": promising[:20],
            "experimental": experimental[:20],
        }

    def suggest_variables(self, limit: int = 3) -> List[Dict[str, object]]:
        bank = self.build_variable_bank()
        out: List[Dict[str, object]] = []
        for bucket in ["useful", "promising", "experimental"]:
            for row in bank.get(bucket, []):
                out.append({
                    "name": row.get("name", ""),
                    "kind": row.get("kind", ""),
                    "description": row.get("description", ""),
                    "reason": f"{bucket} variable",
                })
                if len(out) >= limit:
                    return out
        return out

    def get_recent_symbolic(self, limit: int = 3) -> List[Dict[str, object]]:
        return [asdict(record) for record in self.symbolic[-limit:]]

    def get_role(self, variable_name: str) -> Optional[Dict[str, object]]:
        record = self.roles.get(variable_name)
        return asdict(record) if record else None

    def get_latest_validation(self, element_name: str) -> Optional[Dict[str, object]]:
        matches = [record for record in self.validations if record.element_name == element_name]
        if not matches:
            return None
        latest = sorted(matches, key=lambda item: item.turn)[-1]
        return asdict(latest)
