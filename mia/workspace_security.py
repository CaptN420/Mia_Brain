from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = BASE_DIR / "session"

WORKSPACE_DIR = Path(
    os.environ.get("MAI_WORKSPACE", str(DEFAULT_WORKSPACE))
).expanduser().resolve()

SESSION_PREFIX = "alchemy_session_"
SESSION_RE = re.compile(r"^alchemy_session_[A-Za-z0-9_\-]+$")

ALLOWED_SHARED_JSON = {
    "shared_research_memory.json",
    "variables_memory.json",
    "validations_memory.json",
    "roles_memory.json",
    "symbolic_memory.json",
    "equation_debate_log.txt",
    "variable_debate_log.txt",
    "equation_test_report.txt",
}


def get_workspace_dir() -> Path:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_DIR


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def ensure_within_workspace(path: Path) -> Path:
    workspace = get_workspace_dir().resolve()
    resolved = _resolved(Path(path))

    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError(f"Path hors workspace refusé: {resolved}")

    for candidate in [resolved] + list(resolved.parents):
        try:
            if candidate.exists() and candidate.is_symlink():
                raise ValueError(f"Chemin symlink refusé: {candidate}")
        except OSError:
            continue
    return resolved


def validate_session_name(name: str) -> str:
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("Nom de session vide.")
    if "/" in clean or "\\" in clean or ".." in clean:
        raise ValueError(f"Nom de session invalide: {clean}")
    if not SESSION_RE.fullmatch(clean):
        raise ValueError(f"Nom de session invalide: {clean}")
    return clean


def session_dir(name: str) -> Path:
    return ensure_within_workspace(get_workspace_dir() / validate_session_name(name))


def ensure_session_dir(path: Path) -> Path:
    resolved = ensure_within_workspace(path)
    validate_session_name(resolved.name)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def list_session_dirs() -> list[Path]:
    workspace = get_workspace_dir()
    out: list[Path] = []
    for p in workspace.glob(f"{SESSION_PREFIX}*"):
        try:
            if p.is_dir() and SESSION_RE.fullmatch(p.name):
                out.append(ensure_within_workspace(p))
        except Exception:
            continue
    return sorted(out)
