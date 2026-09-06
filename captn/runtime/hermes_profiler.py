#!/usr/bin/env python3
"""
hermes_profiler.py — Production Hermes Agent → OpenRouter token profiler.

Hooks into the Hermes Agent's ``pre_api_request`` lifecycle hook capture the
FINAL provider payload (messages + tools) before it is sent to OpenRouter.

This is the ONLY location that sees the REAL 137k-150k input token requests.

Architecture::

    Hermes Agent turn loop
        │
        ├── turn_request_assembly.py::assemble_api_request()
        │   └── build_api_messages()    ← messages + system prompt assembled
        │
        ├── turn_api_request.py::build_api_request()
        │   └── _build_api_kwargs()     ← FINAL payload with messages + tools
        │       │
        │       └── ✦ CAPTURE POINT ✦   ← pre_api_request hook fires here
        │           │
        │           └── HermesProfiler.profile_request()
        │               ├── snapshot messages[]
        │               ├── snapshot tools[]
        │               ├── attr by category
        │               └── produce REPORT
        │
        └── turn_usage.py::record_response_usage()
            └── capture actual OpenRouter usage  ← comparison point

Zero behavioral changes. Uses the existing ``pre_api_request`` lifecycle hook
so NO core Hermes agent files are modified.

Usage:
    export CAPTN_TOKEN_PROFILING=summary
    # Then run a normal Hermes chat session.
    # The profiler fires automatically via the pre_api_request hook.

    # Or manually:
    from captn.runtime.hermes_profiler import HermesProfiler
    profiler = HermesProfiler(level="verbose")
    profiler.profile_request(messages=messages, tools=tools, system_prompt=system)
    profiler.profile_usage(prompt_tokens=12345, completion_tokens=678)
    print(profiler.render())
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hermes_profiler")

# ═══════════════════════════════════════════════════════════════════
# PRODUCTION RECORD — machine-readable per-request slice
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ProfileRecord:
    """Flat, machine-readable record for one profiled request.

    This is the primary data structure for production analysis:
    every field is a scalar, so it can be appended to a CSV, JSONL,
    or SQLite table without further processing.

    Use ``to_jsonl()`` for serialization::

        {"request_id": "req_000042", "model": "deepseek/deepseek-v4-flash", ...}
    """
    request_id: str
    timestamp: str
    model: str = ""
    provider: str = ""
    session_id: str = ""

    # Estimated (chars/4 heuristic)
    estimated_total: int = 0
    estimated_messages: int = 0
    estimated_tools: int = 0
    message_count: int = 0
    tool_count: int = 0

    # Category breakdown (estimated)
    system_tokens: int = 0
    history_tokens: int = 0
    memory_tokens: int = 0
    memory_prefetch_tokens: int = 0
    skills_tokens: int = 0
    mcp_tokens: int = 0
    tool_schema_tokens: int = 0
    subagent_tokens: int = 0
    current_user_tokens: int = 0
    other_tokens: int = 0

    # Attribution quality
    attributed_total: int = 0
    attribution_coverage: float = 0.0  # percentage

    # Actual usage (from provider response)
    actual_input_tokens: Optional[int] = None
    actual_output_tokens: Optional[int] = None
    actual_total_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None

    # Context Budget telemetry (populated when CAPTN_CONTEXT_BUDGET is active)
    budget_enabled: bool = False
    budget_before_tokens: int = 0
    budget_after_tokens: int = 0
    budget_drop_count: int = 0
    budget_warnings: List[str] = field(default_factory=list)
    system_budget_exceeded: bool = False

    # Diagnostics
    oversized_items: int = 0
    estimate_error_pct: Optional[float] = None  # (actual - estimated) / actual * 100

    # Adaptive Context Manager telemetry
    adaptive_enabled: bool = False
    complexity_score: float = 0.0
    complexity_class: str = ""
    adaptive_total_budget: int = 0
    adaptive_system_budget: int = 0
    adaptive_history_budget: int = 0
    adaptive_memory_budget: int = 0
    adaptive_tools_budget: int = 0
    adaptive_retrieved_budget: int = 0
    adaptive_redistribution_count: int = 0
    adaptive_strategy: str = ""

    @classmethod
    def from_hermes_profile(
        cls, profile: "HermesProfile", *, model: str = "", provider: str = "", session_id: str = ""
    ) -> "ProfileRecord":
        """Build a flat record from a rich HermesProfile."""
        attr = profile.attributed_total
        total = profile.total_estimated
        coverage = (attr / max(total, 1)) * 100

        actual_input = profile.usage.prompt_tokens if profile.usage else None
        err_pct = None
        if actual_input is not None and total > 0:
            err_pct = round((actual_input - total) / max(actual_input, 1) * 100, 2)

        return cls(
            request_id=profile.request_id,
            timestamp=profile.timestamp,
            model=model,
            provider=provider,
            session_id=session_id,
            estimated_total=total,
            estimated_messages=profile.total_messages_tokens,
            estimated_tools=profile.total_tools_tokens,
            message_count=len(profile.messages),
            tool_count=len(profile.tools),
            system_tokens=profile.system_prompt_tokens,
            history_tokens=profile.conversation_history_tokens,
            memory_tokens=profile.memory_tokens,
            memory_prefetch_tokens=profile.memory_prefetch_tokens,
            skills_tokens=profile.skills_tokens,
            mcp_tokens=profile.mcp_tokens,
            tool_schema_tokens=profile.tool_schemas_tokens,
            subagent_tokens=profile.subagent_tokens,
            current_user_tokens=profile.user_message_tokens,
            other_tokens=profile.other_tokens,
            attributed_total=attr,
            attribution_coverage=round(coverage, 2),
            actual_input_tokens=actual_input,
            actual_output_tokens=profile.usage.completion_tokens if profile.usage else None,
            actual_total_tokens=profile.usage.total_tokens if profile.usage else None,
            cache_read_tokens=profile.usage.cache_read if profile.usage else None,
            cache_write_tokens=profile.usage.cache_write if profile.usage else None,
            oversized_items=len(profile.warnings),
            estimate_error_pct=err_pct,
        )

    def to_jsonl(self) -> str:
        """Serialize to a single JSONL line for append-only logging."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "provider": self.provider,
            "session_id": self.session_id,
            "estimated_total": self.estimated_total,
            "estimated_messages": self.estimated_messages,
            "estimated_tools": self.estimated_tools,
            "message_count": self.message_count,
            "tool_count": self.tool_count,
            "system_tokens": self.system_tokens,
            "history_tokens": self.history_tokens,
            "memory_tokens": self.memory_tokens,
            "memory_prefetch_tokens": self.memory_prefetch_tokens,
            "skills_tokens": self.skills_tokens,
            "mcp_tokens": self.mcp_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "subagent_tokens": self.subagent_tokens,
            "current_user_tokens": self.current_user_tokens,
            "other_tokens": self.other_tokens,
            "attributed_total": self.attributed_total,
            "attribution_coverage": self.attribution_coverage,
            "actual_input_tokens": self.actual_input_tokens,
            "actual_output_tokens": self.actual_output_tokens,
            "actual_total_tokens": self.actual_total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "budget_enabled": self.budget_enabled,
            "budget_before_tokens": self.budget_before_tokens,
            "budget_after_tokens": self.budget_after_tokens,
            "budget_drop_count": self.budget_drop_count,
            "budget_warnings": self.budget_warnings,
            "system_budget_exceeded": self.system_budget_exceeded,
            "oversized_items": self.oversized_items,
            "estimate_error_pct": self.estimate_error_pct,
            "adaptive_enabled": self.adaptive_enabled,
            "complexity_score": self.complexity_score,
            "complexity_class": self.complexity_class,
            "adaptive_total_budget": self.adaptive_total_budget,
            "adaptive_system_budget": self.adaptive_system_budget,
            "adaptive_history_budget": self.adaptive_history_budget,
            "adaptive_memory_budget": self.adaptive_memory_budget,
            "adaptive_tools_budget": self.adaptive_tools_budget,
            "adaptive_retrieved_budget": self.adaptive_retrieved_budget,
            "adaptive_redistribution_count": self.adaptive_redistribution_count,
            "adaptive_strategy": self.adaptive_strategy,
        }

    @classmethod
    def fields(cls) -> List[str]:
        """Return the field names in order, for CSV headers."""
        return [
            "request_id", "timestamp", "model", "provider", "session_id",
            "estimated_total", "estimated_messages", "estimated_tools",
            "message_count", "tool_count",
            "system_tokens", "history_tokens", "memory_tokens",
            "memory_prefetch_tokens", "skills_tokens", "mcp_tokens",
            "tool_schema_tokens", "subagent_tokens", "current_user_tokens",
            "other_tokens",
            "attributed_total", "attribution_coverage",
            "actual_input_tokens", "actual_output_tokens", "actual_total_tokens",
            "cache_read_tokens", "cache_write_tokens",
            "budget_enabled", "budget_before_tokens", "budget_after_tokens",
            "budget_drop_count", "budget_warnings", "system_budget_exceeded",
            "oversized_items", "estimate_error_pct",
            "adaptive_enabled", "complexity_score", "complexity_class",
            "adaptive_total_budget", "adaptive_system_budget",
            "adaptive_history_budget", "adaptive_memory_budget",
            "adaptive_tools_budget", "adaptive_retrieved_budget",
            "adaptive_redistribution_count", "adaptive_strategy",
        ]


# ═══════════════════════════════════════════════════════════════════
# RECORDS STORE — in-memory + optional JSONL persistence
# ═══════════════════════════════════════════════════════════════════

# In-memory store of all records for this process lifetime
_records: List[ProfileRecord] = []
_records_lock = threading.Lock()

# Optional JSONL output path (set via env var CAPTN_PROFILE_OUTPUT)
ENV_OUTPUT = "CAPTN_PROFILE_OUTPUT"


def _output_path() -> Optional[str]:
    """Path for append-only JSONL output, or None."""
    p = os.environ.get(ENV_OUTPUT, "").strip()
    return p if p else None


def _append_record(record: ProfileRecord) -> None:
    """Append to in-memory store and optionally write to JSONL."""
    with _records_lock:
        _records.append(record)
    out = _output_path()
    if out:
        try:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "a", encoding="utf-8") as f:
                f.write(record.to_jsonl() + "\n")
        except OSError as e:
            logger.warning("ProfileRecord output write failed: %s", e)


def get_all_records() -> List[ProfileRecord]:
    """Thread-safe snapshot of all records this process."""
    with _records_lock:
        return list(_records)


def clear_records() -> int:
    """Clear the in-memory store. Returns the number cleared."""
    global _records
    with _records_lock:
        n = len(_records)
        _records = []
    return n


def budget_summary() -> Dict[str, Any]:
    """Aggregate budget-specific telemetry from all records."""
    records = get_all_records()
    budgets = [r for r in records if r.budget_enabled]
    if not budgets:
        return {"enabled_requests": 0}

    before = [r.budget_before_tokens for r in budgets]
    after = [r.budget_after_tokens for r in budgets]
    prices = []
    for r in budgets:
        if r.budget_before_tokens > 0 and r.budget_after_tokens <= r.budget_before_tokens:
            prices.append((r.budget_before_tokens - r.budget_after_tokens) / r.budget_before_tokens * 100)
    reductions = sorted(prices) if prices else []

    return {
        "enabled_requests": len(budgets),
        "total_before": sum(before),
        "total_after": sum(after),
        "avg_before": round(sum(before) / max(len(budgets), 1), 1),
        "avg_after": round(sum(after) / max(len(budgets), 1), 1),
        "total_reduction_tokens": sum(before) - sum(after),
        "total_reduction_pct": round((sum(before) - sum(after)) / max(sum(before), 1) * 100, 1),
        "avg_reduction_pct": round(sum(reductions) / max(len(reductions), 1), 1) if reductions else 0.0,
        "p50_reduction_pct": round(reductions[len(reductions)//2], 1) if len(reductions) > 1 else 0.0,
        "total_items_dropped": sum(r.budget_drop_count for r in budgets),
        "system_budget_exceeded": sum(1 for r in budgets if r.system_budget_exceeded),
        "warnings": [w for r in budgets for w in r.budget_warnings[:5]],  # limit to 5 most recent
    }


def _add_budget_telemetry(record: ProfileRecord, *, selection_result: Any) -> None:
    """Populate budget telemetry fields from a SelectionResult."""
    from captn.runtime.context_budget import SelectionResult
    if not isinstance(selection_result, SelectionResult):
        return
    record.budget_enabled = True
    record.budget_before_tokens = selection_result.original_total_tokens
    record.budget_after_tokens = selection_result.selected_total_tokens
    record.budget_drop_count = (selection_result.history_items_dropped
                                + selection_result.memory_items_dropped
                                + selection_result.tools_dropped
                                + selection_result.retrieved_items_dropped)
    record.system_budget_exceeded = selection_result.system_budget_exceeded
    if selection_result.total_budget_exceeded:
        record.budget_warnings.append(f"total_budget_exceeded={selection_result.overflow_tokens}")
    if selection_result.system_budget_exceeded:
        record.budget_warnings.append("system_budget_exceeded")
    if selection_result.tools_budget_exceeded:
        record.budget_warnings.append("tools_budget_exceeded")


def _add_adaptive_telemetry(record: ProfileRecord, *, adaptive_result: Any) -> None:
    """Populate adaptive telemetry fields from an AdaptiveSelectionResult."""
    from captn.runtime.adaptive_manager import AdaptiveSelectionResult
    if not isinstance(adaptive_result, AdaptiveSelectionResult):
        return
    record.adaptive_enabled = True
    record.complexity_score = round(adaptive_result.complexity_score, 4)
    record.complexity_class = adaptive_result.complexity_label
    record.adaptive_total_budget = adaptive_result.adaptive_total_budget
    record.adaptive_system_budget = adaptive_result.system_allocated
    record.adaptive_history_budget = adaptive_result.history_allocated
    record.adaptive_memory_budget = adaptive_result.memory_allocated
    record.adaptive_tools_budget = adaptive_result.tools_allocated
    record.adaptive_retrieved_budget = adaptive_result.retrieved_allocated
    record.adaptive_redistribution_count = adaptive_result.redistribution_count
    record.adaptive_strategy = adaptive_result.budget_strategy


def records_summary() -> Dict[str, Any]:
    """Aggregate statistics over all collected records."""
    records = get_all_records()
    if not records:
        return {"total_requests": 0}

    total_est = sum(r.estimated_total for r in records)
    total_act = sum(r.actual_input_tokens or 0 for r in records)
    n = len(records)
    n_with_actual = sum(1 for r in records if r.actual_input_tokens is not None)

    # Budget telemetry aggregates
    n_with_budget = sum(1 for r in records if r.budget_enabled)
    budgets = [r for r in records if r.budget_enabled]
    budget_before = [r.budget_before_tokens for r in budgets]
    budget_after = [r.budget_after_tokens for r in budgets]
    total_drop = sum(r.budget_drop_count for r in budgets)
    total_sys_exceeded = sum(1 for r in budgets if r.system_budget_exceeded)
    all_warnings = []
    for r in budgets:
        all_warnings.extend(r.budget_warnings)
    # Reduction pct per record
    reductions = []
    for r in budgets:
        if r.budget_before_tokens > 0 and r.budget_after_tokens < r.budget_before_tokens:
            reductions.append((r.budget_before_tokens - r.budget_after_tokens) / r.budget_before_tokens * 100)
    # Oversized: records where original > 30K
    oversized_count = sum(1 for r in records if r.estimated_total > 30000)

    return {
        "total_requests": n,
        "requests_with_actual_usage": n_with_actual,
        "estimated": {
            "total": total_est,
            "avg": round(total_est / n, 1) if n else 0,
            "max": max(r.estimated_total for r in records) if records else 0,
            "min": min(r.estimated_total for r in records) if records else 0,
        },
        "actual": {
            "total": total_act,
            "avg": round(total_act / n_with_actual, 1) if n_with_actual else 0,
            "max": max(r.actual_input_tokens or 0 for r in records) if n_with_actual else 0,
        } if n_with_actual else None,
        "categories": {
            "system": {"total": sum(r.system_tokens for r in records), "avg": round(sum(r.system_tokens for r in records) / n, 1) if n else 0},
            "history": {"total": sum(r.history_tokens for r in records), "avg": round(sum(r.history_tokens for r in records) / n, 1) if n else 0},
            "tools": {"total": sum(r.tool_schema_tokens for r in records), "avg": round(sum(r.tool_schema_tokens for r in records) / n, 1) if n else 0},
            "mcp": {"total": sum(r.mcp_tokens for r in records), "avg": round(sum(r.mcp_tokens for r in records) / n, 1) if n else 0},
            "memory": {"total": sum(r.memory_tokens for r in records), "avg": round(sum(r.memory_tokens for r in records) / n, 1) if n else 0},
            "skills": {"total": sum(r.skills_tokens for r in records), "avg": round(sum(r.skills_tokens for r in records) / n, 1) if n else 0},
            "subagent": {"total": sum(r.subagent_tokens for r in records), "avg": round(sum(r.subagent_tokens for r in records) / n, 1) if n else 0},
            "user_msg": {"total": sum(r.current_user_tokens for r in records), "avg": round(sum(r.current_user_tokens for r in records) / n, 1) if n else 0},
        },
        "budget": {
            "enabled_requests": n_with_budget,
            "total_before": sum(budget_before),
            "total_after": sum(budget_after),
            "avg_before": round(sum(budget_before) / max(n_with_budget, 1), 1) if budget_before else 0,
            "avg_after": round(sum(budget_after) / max(n_with_budget, 1), 1) if budget_after else 0,
            "avg_reduction_pct": round(sum(reductions) / max(len(reductions), 1), 1) if reductions else 0.0,
            "max_reduction_pct": round(max(reductions), 1) if reductions else 0.0,
            "total_items_dropped": total_drop,
            "system_budget_exceeded": total_sys_exceeded,
            "warnings": all_warnings,
        },
        "oversized_requests": oversized_count,
        "avg_coverage": round(sum(r.attribution_coverage for r in records) / n, 2) if n else 0,
        "total_oversized": sum(r.oversized_items for r in records),
        "avg_estimate_error": round(sum(r.estimate_error_pct or 0 for r in records) / max(n_with_actual, 1), 2) if n_with_actual else None,
        "top_models": sorted(
            {(r.model, sum(1 for x in records if x.model == r.model)) for r in records if r.model},
            key=lambda x: -x[1],
        )[:10],
    }


def records_csv() -> str:
    """Render all records as a CSV string."""
    import io
    import csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(ProfileRecord.fields())
    for r in get_all_records():
        d = r.to_dict()
        writer.writerow([d.get(f, "") for f in ProfileRecord.fields()])
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

ENV_VAR = "CAPTN_TOKEN_PROFILING"
ENV_WARN_THRESHOLD = "CAPTN_TOKEN_WARNING_THRESHOLD"
_DEFAULT_WARN_THRESHOLD = 5000  # Warn when single item > 5K tokens

_CHARS_PER_TOKEN = 4.0

# Pattern to detect skills block inside system prompt
_SKILLS_BLOCK_RE = re.compile(r"<available_skills>.*?</available_skills>", re.DOTALL)

# Thread-local request counter
_local = threading.local()


def _request_id() -> str:
    """Unique per-request ID."""
    if not hasattr(_local, "_hp_counter"):
        _local._hp_counter = 0
    _local._hp_counter += 1
    return f"req_{_local._hp_counter:06d}"


def _level() -> str:
    return os.environ.get(ENV_VAR, "off").strip().lower()


def _warn_threshold() -> int:
    raw = os.environ.get(ENV_WARN_THRESHOLD, str(_DEFAULT_WARN_THRESHOLD))
    try:
        return max(100, int(raw))
    except (ValueError, TypeError):
        return _DEFAULT_WARN_THRESHOLD


def _fmt_tokens(text: Any) -> int:
    """chars/4 heuristic, same as context_breakdown.py."""
    if isinstance(text, str):
        return max(1, (len(text) + 3) // 4)
    if isinstance(text, (list, dict)):
        try:
            return max(1, (len(json.dumps(text, ensure_ascii=False, default=str)) + 3) // 4)
        except Exception:
            pass
    return 1


def _msg_tokens(msg: dict) -> int:
    """Estimate tokens for a single message (role overhead + content)."""
    t = 4  # role overhead
    content = msg.get("content", "")
    if isinstance(content, str):
        t += _fmt_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                t += _fmt_tokens(part.get("text", ""))
                if "image_url" in part:
                    t += 250  # rough image token cost
    if "tool_calls" in msg:
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            t += _fmt_tokens(fn.get("name", ""))
            t += _fmt_tokens(fn.get("arguments", ""))
    return t


# ═══════════════════════════════════════════════════════════════════
# PROFILE DATA
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MessageEntry:
    """One message in the final payload."""
    index: int
    role: str
    estimated_tokens: int
    char_count: int
    source: str = ""  # attributed source category


@dataclass
class ToolEntry:
    """One tool/schema in the final payload."""
    name: str
    estimated_tokens: int
    source: str = "tools.builtin"


@dataclass
class UsageData:
    """Actual provider usage from response."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_read: Optional[int] = None
    cache_write: Optional[int] = None
    provider: str = ""
    model: str = ""


@dataclass
class HermesProfile:
    """Complete profile of one Hermes → OpenRouter request."""
    request_id: str
    timestamp: str = ""
    messages: List[MessageEntry] = field(default_factory=list)
    tools: List[ToolEntry] = field(default_factory=list)
    usage: Optional[UsageData] = None
    # Category totals
    system_prompt_tokens: int = 0
    conversation_history_tokens: int = 0
    memory_tokens: int = 0
    memory_prefetch_tokens: int = 0
    skills_tokens: int = 0
    mcp_tokens: int = 0
    tool_schemas_tokens: int = 0
    subagent_tokens: int = 0
    user_message_tokens: int = 0
    other_tokens: int = 0
    # Warnings
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"

    @property
    def total_messages_tokens(self) -> int:
        return sum(m.estimated_tokens for m in self.messages)

    @property
    def total_tools_tokens(self) -> int:
        return sum(t.estimated_tokens for t in self.tools)

    @property
    def total_estimated(self) -> int:
        return self.total_messages_tokens + self.total_tools_tokens

    @property
    def attributed_total(self) -> int:
        return (self.system_prompt_tokens + self.conversation_history_tokens
                + self.memory_tokens + self.memory_prefetch_tokens
                + self.skills_tokens + self.mcp_tokens
                + self.tool_schemas_tokens + self.subagent_tokens
                + self.user_message_tokens + self.other_tokens)

    @property
    def unattributed(self) -> int:
        return self.total_estimated - self.attributed_total

    @property
    def top_contributors(self) -> List[Tuple[str, int, str]]:
        """List of (label, tokens, unit) sorted by tokens descending."""
        items: List[Tuple[str, int, str]] = []
        for m in sorted(self.messages, key=lambda x: -x.estimated_tokens)[:20]:
            items.append((f"Message #{m.index} ({m.role})", m.estimated_tokens, "tokens"))
        for t in sorted(self.tools, key=lambda x: -x.estimated_tokens)[:20]:
            items.append((f"Tool: {t.name}", t.estimated_tokens, "tokens"))
        items.sort(key=lambda x: -x[1])
        return items[:20]

    def render(self, level: str = "summary") -> str:
        lines: List[str] = []
        sep = "─" * 52

        lines.append(sep)
        lines.append(f"  HERMES PRODUCTION TOKEN PROFILE  [{self.request_id}]")
        lines.append(sep)

        if self.total_estimated == 0:
            lines.append("  (no profiled content)")
            return "\n".join(lines)

        total = self.total_estimated

        # Category breakdown
        cats = [
            ("System Prompt", self.system_prompt_tokens),
            ("Conversation History", self.conversation_history_tokens),
            ("Long-Term Memory", self.memory_tokens),
            ("Memory Prefetch", self.memory_prefetch_tokens),
            ("Skills Index", self.skills_tokens),
            ("MCP Context", self.mcp_tokens),
            ("Tools / Schemas", self.tool_schemas_tokens),
            ("Subagent Definitions", self.subagent_tokens),
            ("User Message", self.user_message_tokens),
            ("Other", self.other_tokens),
        ]
        cats = [(l, t) for l, t in cats if t > 0]
        for label, tok in sorted(cats, key=lambda x: -x[1]):
            pct = (tok / max(total, 1)) * 100
            lines.append(f"  {label:30s} {tok:>8d} tokens  ({pct:>5.1f}%)")

        lines.append(sep)
        lines.append(f"  {'Estimated Total':30s} {total:>8d} tokens")
        lines.append(f"  {'  Messages':30s} {self.total_messages_tokens:>8d} tokens  ({len(self.messages)} msgs)")
        lines.append(f"  {'  Tools/Schemas':30s} {self.total_tools_tokens:>8d} tokens  ({len(self.tools)} tools)")

        if self.attributed_total > 0:
            lines.append(sep)
            lines.append(f"  {'Attributed Total':30s} {self.attributed_total:>8d} tokens")
            lines.append(f"  {'Unattributed':30s} {self.unattributed:>+8d} tokens")

        # Actual usage comparison
        if self.usage and self.usage.prompt_tokens is not None:
            lines.append(sep)
            lines.append(f"  {'OPENROUTER ACTUAL INPUT':30s} {self.usage.prompt_tokens:>8d} tokens")
            if self.usage.completion_tokens is not None:
                lines.append(f"  {'OPENROUTER OUTPUT':30s} {self.usage.completion_tokens:>8d} tokens")
            diff = self.usage.prompt_tokens - total
            diff_pct = (diff / max(self.usage.prompt_tokens, 1)) * 100
            lines.append(f"  {'Difference (est vs actual)':30s} {diff:>+8d} tokens  ({diff_pct:+.2f}%)")
            if self.usage.cache_read is not None:
                lines.append(f"  {'Cache read tokens':30s} {self.usage.cache_read:>8d}")
            if self.usage.cache_write is not None:
                lines.append(f"  {'Cache write tokens':30s} {self.usage.cache_write:>8d}")

        # Warnings
        if self.warnings:
            lines.append(sep)
            for w in self.warnings:
                lines.append(f"  ⚠️  {w}")

        # Top contributors (verbose)
        if level == "verbose":
            lines.append(sep)
            lines.append("  TOP CONTRIBUTORS:")
            for i, (label, tok, unit) in enumerate(self.top_contributors[:15], 1):
                lines.append(f"  {i:2d}. {label:40s} {tok:>7d} {unit}")

            # Message-level detail
            if self.messages:
                lines.append(sep)
                lines.append("  MESSAGE BREAKDOWN:")
                lines.append(f"  {'#':>4s} {'Role':>12s} {'Tokens':>7s} {'Chars':>6s} {'Source':>20s}")
                for m in self.messages:
                    lines.append(f"  {m.index:>4d} {m.role:>12s} {m.estimated_tokens:>7d} {m.char_count:>6d} {m.source:>20s}")

            # Tool detail
            if self.tools:
                lines.append(sep)
                lines.append("  TOOL SCHEMA BREAKDOWN:")
                lines.append(f"  {'Name':40s} {'Tokens':>7s} {'Source':>20s}")
                for t in sorted(self.tools, key=lambda x: -x.estimated_tokens):
                    lines.append(f"  {t.name:40s} {t.estimated_tokens:>7d} {t.source:>20s}")

        lines.append(sep)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "estimated": {
                "total_input_tokens": self.total_estimated,
                "messages_tokens": self.total_messages_tokens,
                "tools_tokens": self.total_tools_tokens,
                "message_count": len(self.messages),
                "tool_count": len(self.tools),
            },
            "categories": {
                "system_prompt": self.system_prompt_tokens,
                "conversation_history": self.conversation_history_tokens,
                "long_term_memory": self.memory_tokens,
                "memory_prefetch": self.memory_prefetch_tokens,
                "skills": self.skills_tokens,
                "mcp_context": self.mcp_tokens,
                "tools_schemas": self.tool_schemas_tokens,
                "subagent_definitions": self.subagent_tokens,
                "user_message": self.user_message_tokens,
                "other": self.other_tokens,
            },
            "messages": [
                {"index": m.index, "role": m.role, "estimated_tokens": m.estimated_tokens,
                 "char_count": m.char_count, "source": m.source}
                for m in self.messages
            ],
            "tools": [
                {"name": t.name, "estimated_tokens": t.estimated_tokens, "source": t.source}
                for t in self.tools
            ],
            "warnings": self.warnings,
        }
        if self.usage:
            d["actual"] = {
                "input_tokens": self.usage.prompt_tokens,
                "output_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
                "cache_read": self.usage.cache_read,
                "cache_write": self.usage.cache_write,
                "provider": self.usage.provider,
                "model": self.usage.model,
            }
            if self.usage.prompt_tokens is not None:
                diff = self.usage.prompt_tokens - self.total_estimated
                diff_pct = round((diff / max(self.usage.prompt_tokens, 1)) * 100, 2)
                d["comparison"] = {
                    "estimated_input": self.total_estimated,
                    "actual_input": self.usage.prompt_tokens,
                    "difference": diff,
                    "difference_pct": diff_pct,
                }
        return d


# ═══════════════════════════════════════════════════════════════════
# PROFILER
# ═══════════════════════════════════════════════════════════════════

class HermesProfiler:
    """Profiles Hermes Agent → OpenRouter requests.

    Hooks into the ``pre_api_request`` lifecycle event to capture the
    FINAL provider payload (messages + tools) before it is sent.

    Zero behavioral changes. Observation only.
    """

    _global_disabled: bool = False

    def __init__(
        self,
        level: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        self.level = level or _level()
        self._enabled = self.level != "off" and not self._global_disabled
        self.request_id = request_id or _request_id()
        self._profile: Optional[HermesProfile] = None

    @classmethod
    def is_active(cls) -> bool:
        """Fast check: is profiling active for this process?"""
        return not cls._global_disabled and _level() != "off"

    def profile_request(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: str = "",
        model: str = "",
        provider: str = "",
        session_id: str = "",
    ) -> HermesProfile:
        """Profile one complete Hermes → OpenRouter request.

        This is the main entry point. Called from the pre_api_request hook.

        Args:
            messages: Final messages array to be sent.
            tools: Final tools array to be sent (or None).
            system_prompt: The effective system prompt string.
            model: Model name (e.g. "deepseek/deepseek-v4-flash").
            provider: Provider name (e.g. "openrouter").
            session_id: Session ID for diagnostics.

        Returns:
            HermesProfile with full attribution.
        """
        if not self._enabled:
            return HermesProfile(request_id=self.request_id)

        profile = HermesProfile(request_id=self.request_id)

        # ── Analyze each message ──
        threshold = _warn_threshold()
        system_prompt_tokens = 0
        conversation_tokens = 0
        memory_tokens = 0
        memory_prefetch_tokens = 0
        skills_tokens = 0
        user_msg_tokens = 0
        other_tokens = 0

        # Detect skills block in system prompt
        skills_block = _SKILLS_BLOCK_RE.search(system_prompt) if system_prompt else None
        skills_text = skills_block.group(0) if skills_block else ""
        system_core = system_prompt.replace(skills_text, "") if skills_text else system_prompt

        if skills_text:
            skills_tokens = _fmt_tokens(skills_text)
        if system_core:
            system_prompt_tokens = _fmt_tokens(system_core)

        # Detect memory prefetch context (usually injected as a fenced block in user message)
        _MEMORY_PREFETCH_PATTERN = re.compile(
            r"<context>.*?</context>|<memory>.*?</memory>|\[memory prefetch\].*?(?=\n\n|\Z)",
            re.DOTALL | re.IGNORECASE,
        )

        for idx, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            content_str = content if isinstance(content, str) else json.dumps(content, default=str)

            tok = _msg_tokens(msg)
            char_count = len(content_str)

            # Attribute source
            source = "other"
            if role == "system":
                source = "system_prompt"
                # Already counted above in system_prompt_tokens
            elif role == "user":
                if idx == len(messages) - 1:
                    # Current turn's user message
                    source = "user_message"
                    user_msg_tokens += tok
                    # Check for memory prefetch injection
                    prefetch_matches = _MEMORY_PREFETCH_PATTERN.findall(content_str)
                    if prefetch_matches:
                        prefetch_tok = sum(_fmt_tokens(m) for m in prefetch_matches)
                        memory_prefetch_tokens += prefetch_tok
                        source = "user_message (+ memory prefetch)"
                else:
                    source = "conversation_history"
                    conversation_tokens += tok
            elif role == "assistant":
                source = "conversation_history"
                conversation_tokens += tok
            elif role == "tool":
                source = "conversation_history"
                conversation_tokens += tok
                # Check for oversized tool results
                if tok > threshold:
                    profile.warnings.append(
                        f"TOOL RESULT #{idx} = {tok:,} tokens  (threshold: {threshold:,})"
                    )

            profile.messages.append(MessageEntry(
                index=idx, role=role, estimated_tokens=tok,
                char_count=char_count, source=source,
            ))

            # Warn about oversized messages
            if tok > threshold and role != "tool":
                profile.warnings.append(
                    f"MESSAGE #{idx} ({role}) = {tok:,} tokens  (threshold: {threshold:,})"
                )

        # ── Analyze tools ──
        tool_schemas_tokens = 0
        mcp_tokens = 0
        subagent_tokens = 0

        for tool in (tools or []):
            fn = tool.get("function", tool) if isinstance(tool, dict) else {}
            name = fn.get("name", fn.get("name", str(tool)[:40]))
            tok = _fmt_tokens(tool)

            source = "tools.builtin"
            if name.startswith("mcp_"):
                source = "tools.mcp"
                mcp_tokens += tok
            elif name == "delegate_task":
                source = "tools.subagent"
                subagent_tokens += tok
            else:
                tool_schemas_tokens += tok

            profile.tools.append(ToolEntry(
                name=name, estimated_tokens=tok, source=source,
            ))

            # Warn about oversized tool schemas
            if tok > threshold:
                profile.warnings.append(
                    f"TOOL SCHEMA: {name} = {tok:,} tokens  (threshold: {threshold:,})"
                )

        # ── Set category totals ──
        profile.system_prompt_tokens = system_prompt_tokens
        profile.conversation_history_tokens = conversation_tokens
        profile.memory_tokens = memory_tokens  # (would need external memory provider data)
        profile.memory_prefetch_tokens = memory_prefetch_tokens
        profile.skills_tokens = skills_tokens
        profile.mcp_tokens = mcp_tokens
        profile.tool_schemas_tokens = tool_schemas_tokens
        profile.subagent_tokens = subagent_tokens
        profile.user_message_tokens = user_msg_tokens
        profile.other_tokens = other_tokens

        # Log summary
        total = profile.total_estimated
        logger.info(
            "HERMES_PROFILE request=%s messages=%d tools=%d est_total=%d "
            "sys=%d hist=%d tools=%d user=%d",
            self.request_id, len(messages), len(tools or []), total,
            system_prompt_tokens, conversation_tokens, tool_schemas_tokens, user_msg_tokens,
        )

        # ── Emit machine-readable record ──
        record = ProfileRecord.from_hermes_profile(
            profile, model=model, provider=provider, session_id=session_id,
        )
        _append_record(record)

        self._profile = profile
        return profile

    def profile_usage(
        self,
        *,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        cache_read: Optional[int] = None,
        cache_write: Optional[int] = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        """Record actual usage from the provider response."""
        if not self._enabled:
            return
        if self._profile is None:
            return
        self._profile.usage = UsageData(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
            provider=provider,
            model=model,
        )

    def render(self, level: Optional[str] = None) -> str:
        if self._profile is None:
            return "No profile data captured."
        return self._profile.render(level or self.level)

    def report(self) -> Optional[HermesProfile]:
        return self._profile

    def to_dict(self) -> Dict[str, Any]:
        if self._profile is None:
            return {"request_id": self.request_id, "error": "No profile data"}
        return self._profile.to_dict()


# ═══════════════════════════════════════════════════════════════════
# pre_api_request HOOK HANDLER
# ═══════════════════════════════════════════════════════════════════

# Global profiler instance for the hook handler
_global_profiler: Optional[HermesProfiler] = None
_global_profiler_lock = threading.Lock()


def _get_profiler() -> HermesProfiler:
    """Get or create the global profiler instance."""
    global _global_profiler
    if _global_profiler is None:
        with _global_profiler_lock:
            if _global_profiler is None:
                _global_profiler = HermesProfiler()
    return _global_profiler


def pre_api_request_hook(
    task_id: str = "",
    turn_id: str = "",
    api_request_id: str = "",
    session_id: str = "",
    user_message: str = "",
    conversation_history: Any = None,
    platform: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_call_count: int = 0,
    retry_count: int = 0,
    request_messages: Any = None,
    system_prompt: str = "",
    message_count: int = 0,
    tool_count: int = 0,
    approx_input_tokens: int = 0,
    request_char_count: int = 0,
    max_tokens: Optional[int] = None,
    started_at: Optional[float] = None,
    middleware_trace: Any = None,
    request: Any = None,
) -> None:
    """Handle the pre_api_request lifecycle hook from the Hermes Agent.

    This is called by the Hermes Agent's turn loop JUST BEFORE the
    provider API call. It receives the FINAL messages and tools.

    Register this hook with:
        from hermes_cli.lifecycle import register_hook
        register_hook("pre_api_request", pre_api_request_hook)
    """
    if not HermesProfiler.is_active():
        return

    profiler = _get_profiler()

    # Extract messages and tools from the request
    msgs: List[Dict[str, Any]] = []
    tools_list: List[Dict[str, Any]] = []

    if isinstance(request_messages, list):
        msgs = request_messages
    elif isinstance(request, dict):
        # The request kwarg is the full api_kwargs dict
        msgs = request.get("messages", request.get("input", []))
        tools_list = request.get("tools", [])

    # Also try to get tools from the agent state
    if not tools_list:
        # tools_list may be empty; we capture what we can
        pass

    # Build the profile
    profile = profiler.profile_request(
        messages=msgs,
        tools=tools_list,
        system_prompt=system_prompt,
        model=model,
        provider=provider,
        session_id=session_id,
    )

    # Log the profile
    mode = profiler.level
    if mode == "summary":
        cat_total = sum([
            profile.system_prompt_tokens,
            profile.conversation_history_tokens,
            profile.memory_tokens,
            profile.memory_prefetch_tokens,
            profile.skills_tokens,
            profile.mcp_tokens,
            profile.tool_schemas_tokens,
            profile.subagent_tokens,
            profile.user_message_tokens,
            profile.other_tokens,
        ])
        logger.info(
            "\n%s\n"
            "  HERMES PRODUCTION TOKEN PROFILE  [%s]\n"
            "  Request: session=%s model=%s provider=%s\n"
            "  Messages: %d  Tools: %d  Estimated: %d  Attributed: %d\n"
            "  System: %d  History: %d  Memory: %d  Skills: %d\n"
            "  MCP: %d  Tools: %d  Subagents: %d  User: %d\n"
            "%s",
            "─" * 52, profiler.request_id,
            session_id[:16] if session_id else "?", model, provider,
            len(msgs), len(tools_list), profile.total_estimated, cat_total,
            profile.system_prompt_tokens, profile.conversation_history_tokens,
            profile.memory_tokens, profile.skills_tokens,
            profile.mcp_tokens, profile.tool_schemas_tokens,
            profile.subagent_tokens, profile.user_message_tokens,
            "─" * 52,
        )
    elif mode == "verbose":
        logger.info("\n%s", profile.render(level="verbose"))


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

def _demo_profile() -> HermesProfile:
    """Build a demo profile simulating a 150K-token request."""
    profiler = HermesProfiler(level="verbose", request_id="demo_150k")

    # Build a realistic payload: 128 messages + 47 tools
    messages = []

    # System prompt (~20K tokens)
    system = "You are a helpful AI assistant. " * 2000
    messages.append({"role": "system", "content": system})

    # Conversation history: 80 user/assistant turns (~45K tokens)
    for i in range(80):
        messages.append({"role": "user", "content": f"This is user message {i} with some detailed content about the project." * 5})
        messages.append({"role": "assistant", "content": f"This is assistant response {i} with analysis and code." * 10})

    # Tool results: 20 large tool results (~25K tokens)
    for i in range(20):
        messages.append({"role": "tool", "content": f"Tool result {i}: " + "x" * 5000, "tool_call_id": f"call_{i}"})

    # Current user message
    messages.append({"role": "user", "content": "What is the GCD of 123456 and 789012?"})

    # Tools: 47 schemas (~30K tokens)
    tools = []
    for i in range(47):
        tools.append({
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": f"Description for tool {i} with detailed parameter documentation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param_a": {"type": "string", "description": "Parameter A for tool"},
                        "param_b": {"type": "integer", "description": "Parameter B for tool"},
                        "param_c": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["param_a"],
                },
            },
        })

    # Add some MCP tools
    tools.append({
        "type": "function", "function": {
            "name": "mcp_github_search",
            "description": "Search GitHub repositories via MCP",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    })
    tools.append({
        "type": "function", "function": {
            "name": "mcp_filesystem_read",
            "description": "Read filesystem via MCP",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    })

    profile = profiler.profile_request(
        messages=messages,
        tools=tools,
        system_prompt=system,
        model="deepseek/deepseek-v4-flash",
        provider="openrouter",
    )

    # Simulate usage
    profiler.profile_usage(
        prompt_tokens=150034,
        completion_tokens=920,
        total_tokens=150954,
        cache_read=50000,
        cache_write=20000,
        provider="openrouter",
        model="deepseek/deepseek-v4-flash",
    )

    return profile


def register_cli(subparsers) -> None:
    p = subparsers.add_parser(
        "hermes_profile",
        help="Hermes Agent production token profiler (pre_api_request hook)",
    )
    sub = p.add_subparsers(dest="hermes_profile_cmd")

    status_p = sub.add_parser("status", help="Show profiler status")
    status_p.set_defaults(func=_cmd_status)

    demo_p = sub.add_parser("demo", help="Run a demo with simulated 150K-token request")
    demo_p.set_defaults(func=_cmd_demo)

    records_p = sub.add_parser("records", help="Show collected records summary")
    records_p.set_defaults(func=_cmd_records)

    csv_p = sub.add_parser("csv", help="Export all records as CSV")
    csv_p.set_defaults(func=_cmd_csv)

    clear_p = sub.add_parser("clear", help="Clear in-memory records")
    clear_p.set_defaults(func=_cmd_clear)


def _cmd_status(args) -> None:
    level = _level()
    active = HermesProfiler.is_active()
    print(f"Hermes Production Token Profiler")
    print(f"  Environment: {ENV_VAR}={level}")
    print(f"  Active: {active}")
    print(f"  Warning threshold: {_warn_threshold()}")
    print()
    print(f"  To enable:")
    print(f"    export {ENV_VAR}=summary")
    print(f"    export {ENV_VAR}=verbose")
    print()
    print(f"  Hook registration:")
    print(f"    from hermes_cli.lifecycle import register_hook")
    print(f"    from captn.runtime.hermes_profiler import pre_api_request_hook")
    print(f"    register_hook('pre_api_request', pre_api_request_hook)")


def _cmd_demo(args) -> None:
    profile = _demo_profile()
    print(profile.render(level="verbose"))
    import json as _json
    print("\n\nJSON:")
    print(_json.dumps(profile.to_dict(), indent=2))


def _cmd_records(args) -> None:
    """Show aggregated records summary."""
    summary = records_summary()
    if summary["total_requests"] == 0:
        print("No records collected yet.")
        print("Enable profiling: export CAPTN_TOKEN_PROFILING=summary")
        print("Then run a chat session — every API call is recorded.")
        return

    print(f"Profile Records: {summary['total_requests']} requests")
    if summary.get("requests_with_actual_usage"):
        print(f"  With actual usage: {summary['requests_with_actual_usage']}")
    print()
    print(f"  Estimated tokens:")
    print(f"    Total: {summary['estimated']['total']:,}")
    print(f"    Avg:   {summary['estimated']['avg']:,.1f}")
    print(f"    Max:   {summary['estimated']['max']:,}")
    print(f"    Min:   {summary['estimated']['min']:,}")
    actual = summary.get("actual")
    if actual:
        print(f"  Actual OpenRouter tokens:")
        print(f"    Total: {actual['total']:,}")
        print(f"    Avg:   {actual['avg']:,.1f}")
    print()
    print(f"  Category averages (per request):")
    cats = summary["categories"]
    for label, key in [
        ("System prompt", "system"), ("History", "history"), ("Tools", "tools"),
        ("MCP", "mcp"), ("Memory", "memory"), ("Skills", "skills"),
        ("Subagents", "subagent"), ("User message", "user_msg"),
    ]:
        c = cats.get(key, {})
        if c.get("total", 0) > 0:
            print(f"    {label:20s}: {c['avg']:>8.1f} avg  ({c['total']:>8,d} total)")
    print()
    print(f"  Avg attribution coverage: {summary['avg_coverage']:.1f}%")
    if summary.get("avg_estimate_error") is not None:
        print(f"  Avg estimate error:       {summary['avg_estimate_error']:+.1f}%")
    if summary["total_oversized"]:
        print(f"  Total oversized items:    {summary['total_oversized']}")
    if summary.get("top_models"):
        print(f"  Top models: {', '.join(f'{m[0]} ({m[1]})' for m in summary['top_models'])}")


def _cmd_csv(args) -> None:
    """Export all records as CSV."""
    print(records_csv())


def _cmd_clear(args) -> None:
    """Clear in-memory records."""
    n = clear_records()
    print(f"Cleared {n} records.")


if __name__ == "__main__":
    os.environ[ENV_VAR] = "verbose"
    profile = _demo_profile()
    print(profile.render(level="verbose"))