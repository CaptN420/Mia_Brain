from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from workspace_security import get_workspace_dir

WORKSPACE = get_workspace_dir()
SECURITY_DIR = WORKSPACE / "security"
SECURITY_DIR.mkdir(parents=True, exist_ok=True)
JSONL_PATH = SECURITY_DIR / "security_events.jsonl"
TEXT_LOG_PATH = SECURITY_DIR / "security_alerts.log"

HIGH_RISK = "high"
MEDIUM_RISK = "medium"
LOW_RISK = "low"

DANGEROUS_TOKENS = {
    'sudo', 'apt', 'apt-get', 'pip', 'curl', 'wget', 'chmod', 'chown', 'mount', 'umount',
    'dd', 'mkfs', 'bash', 'sh', 'zsh', 'fish', 'powershell', 'cmd.exe', 'reg', 'schtasks',
    'rm', 'del', 'format', 'reboot', 'shutdown', 'systemctl', 'service', 'docker', 'podman'
}
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\b(curl|wget)\b.*\|", re.I),
    re.compile(r"&&|;|\|\s*(bash|sh|zsh)", re.I),
    re.compile(r">\s*/", re.I),
    re.compile(r"/(etc|root|boot|sys|proc)\b", re.I),
]
AGENT_HIGH_RISK_PATTERNS = [
    re.compile(r"\b(rm\s+-rf|sudo|apt-get|chmod\s+777|curl\s+.*\|\s*(bash|sh)|wget\s+.*\|\s*(bash|sh))\b", re.I),
    re.compile(r"\b(delete|remove|overwrite|replace|reset|install|run|execute|launch)\b.*\b(/etc|/root|/boot|/sys|/proc)\b", re.I),
]
AGENT_MEDIUM_RISK_PATTERNS = [
    re.compile(r"\b(run|execute|launch|call|open|delete|remove|overwrite|replace|reset|install)\b", re.I),
    re.compile(r"```(?:bash|sh|zsh|powershell|cmd)\b", re.I),
]


class SecurityStop(RuntimeError):
    pass


def _now() -> str:
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def _append_text(line: str) -> None:
    with TEXT_LOG_PATH.open('a', encoding='utf-8') as fh:
        fh.write(line.rstrip() + '\n')


def log_security_event(*, risk: str, category: str, action: str, target: str = '', reason: str = '', details: dict | None = None, session_dir: str | None = None) -> dict:
    payload = {
        'ts': _now(),
        'risk': risk,
        'category': category,
        'action': action,
        'target': str(target or ''),
        'reason': str(reason or ''),
        'details': details or {},
        'session_dir': str(session_dir or ''),
    }
    with JSONL_PATH.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + '\n')
    if risk in {HIGH_RISK, MEDIUM_RISK}:
        _append_text(f"[{payload['ts']}] risk={risk} category={category} action={action} target={target} reason={reason}")
    return payload


def _stop_if_needed(risk: str, category: str, action: str, target: str, reason: str, details: dict | None, session_dir: str | None) -> None:
    log_security_event(risk=risk, category=category, action=action, target=target, reason=reason, details=details, session_dir=session_dir)
    if risk == HIGH_RISK:
        raise SecurityStop(f"High-risk action blocked: {action} | {reason}")


def guard_path_operation(path: str | Path, *, action: str, category: str = 'filesystem', session_dir: str | None = None, allow_create: bool = True) -> Path:
    p = Path(path).expanduser()
    resolved = p.resolve(strict=False)
    workspace = WORKSPACE.resolve()
    reason = ''
    risk = LOW_RISK
    if resolved != workspace and workspace not in resolved.parents:
        risk = HIGH_RISK
        reason = 'path outside workspace'
    elif any(parent.is_symlink() for parent in [resolved] + list(resolved.parents) if parent.exists()):
        risk = HIGH_RISK
        reason = 'symlink path refused'
    elif action in {'delete', 'rmtree', 'unlink'}:
        risk = MEDIUM_RISK
        reason = 'destructive filesystem action'
    elif not allow_create and not resolved.exists():
        risk = MEDIUM_RISK
        reason = 'path does not exist'
    _stop_if_needed(risk, category, action, str(resolved), reason, None, session_dir)
    return resolved


def guard_subprocess_command(cmd: list[str] | tuple[str, ...], *, cwd: str | Path | None = None, session_dir: str | None = None) -> None:
    parts = [str(x) for x in (cmd or [])]
    joined = ' '.join(parts)
    reason = ''
    risk = LOW_RISK
    if not parts:
        risk = HIGH_RISK
        reason = 'empty command'
    else:
        first = Path(parts[0]).name.lower()
        if first in DANGEROUS_TOKENS:
            if first in {'python3', 'python'}:
                pass
            else:
                risk = HIGH_RISK
                reason = f'dangerous executable: {first}'
        for pat in DANGEROUS_PATTERNS:
            if pat.search(joined):
                risk = HIGH_RISK
                reason = f'dangerous command pattern: {pat.pattern}'
                break
        if risk != HIGH_RISK and ('launcher.py' not in joined and 'ui_launcher.py' not in joined):
            risk = MEDIUM_RISK
            reason = 'subprocess not limited to launcher/ui'
    if cwd is not None:
        try:
            guard_path_operation(Path(cwd), action='cwd', category='subprocess', session_dir=session_dir)
        except SecurityStop as exc:
            risk = HIGH_RISK
            reason = str(exc)
    _stop_if_needed(risk, 'subprocess', 'spawn', joined, reason, {'cwd': str(cwd or '')}, session_dir)


def guard_network_request(url: str, *, service: str = 'network', session_dir: str | None = None) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    allowed = {'127.0.0.1', 'localhost', '::1'}
    risk = LOW_RISK
    reason = ''
    if host not in allowed:
        risk = HIGH_RISK
        reason = f'outbound network host refused: {host or url}'
    _stop_if_needed(risk, 'service', service, url, reason, {'host': host}, session_dir)


def inspect_agent_output(text: str, *, speaker: str, session_dir: str | None = None) -> None:
    sample = str(text or '')[:400]
    risk = LOW_RISK
    reason = ''
    for pat in AGENT_HIGH_RISK_PATTERNS:
        if pat.search(sample):
            risk = HIGH_RISK
            reason = f'high-risk command-like output from {speaker}'
            break
    if risk != HIGH_RISK:
        for pat in AGENT_MEDIUM_RISK_PATTERNS:
            if pat.search(sample):
                risk = MEDIUM_RISK
                reason = f'suspicious command-like output from {speaker}'
                break
    if risk != LOW_RISK:
        _stop_if_needed(risk, 'agent_output', speaker, sample, reason, None, session_dir)


def log_effect(effect: str, *, target: str = '', risk: str = LOW_RISK, session_dir: str | None = None, details: dict | None = None) -> None:
    log_security_event(risk=risk, category='effect', action=effect, target=target, reason='', details=details, session_dir=session_dir)
