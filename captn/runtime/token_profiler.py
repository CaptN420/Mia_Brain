#!/usr/bin/env python3
"""
token_profiler.py — Production-safe token attribution & context profiling.

Measures every component of an LLM request BEFORE it is sent and compares
estimated tokens against actual provider usage. Zero behavioral changes:
profiler is OBSERVATION ONLY.

Categories:
  SYSTEM_PROMPT       system prompts (core, personality, runtime, other)
  CONVERSATION_HISTORY assistant/user/tool messages
  LONG_TERM_MEMORY    memory & user profile context
  RETRIEVED_CONTEXT   BM25, RAG, fragment registry results
  TOOLS               tool definitions, JSON schemas, MCP tools
  USER_MESSAGE        the current user message
  OTHER               anything not matching the above

Usage:
    from captn.runtime.token_profiler import TokenProfiler, profile

    profiler = TokenProfiler(level="summary")
    profiler.add("system", system_text)
    profiler.add("history", history_messages)
    profiler.add("memory", memory_text)
    profiler.add("tools", tool_schemas)
    profiler.report()

Levels:
    off       — no profiling (zero overhead)
    summary   — per-category totals only
    verbose   — per-item breakdown (which tools, which memories)

Zero overhead when disabled:
    if TokenProfiler.is_disabled():
        result = client.chat.completions.create(...)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("token_profiler")

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Environment variable to control profiling
ENV_VAR = "CAPTN_TOKEN_PROFILING"
ENV_REQUEST_ID = "CAPTN_PROFILE_REQUEST_ID"  # override request id (testing)

# Default chars-per-token ratio for estimation
_CHARS_PER_TOKEN = 4.0

# Category labels (human-readable)
CATEGORY_LABELS = {
    "system_prompt": "System Prompt",
    "conversation_history": "Conversation History",
    "long_term_memory": "Long-Term Memory",
    "retrieved_context": "Retrieved / RAG Context",
    "tools": "Tools / Schemas",
    "user_message": "User Message",
    "other": "Other Context",
}

# Sub-category labels
SUBCAT_LABELS = {
    "system.core": "Core Identity",
    "system.personality": "Personality",
    "system.runtime": "Runtime Instructions",
    "system.skills": "Skills Index",
    "system.rules": "Rules",
    "system.other": "Other System",
    "history.user": "User Messages",
    "history.assistant": "Assistant Messages",
    "history.tool": "Tool Messages",
    "history.total": "Total History",
    "memory.memory": "Memory Entries",
    "memory.user_profile": "User Profile",
    "memory.mcp": "MCP Memory",
    "memory.other": "Other Memory",
    "tools.builtin": "Built-in Tools",
    "tools.mcp": "MCP Tools",
    "tools.subagent": "Subagent Definitions",
    "tools.schemas": "Tool Schemas",
    "tools.other": "Other Tools",
    "retrieval.bm25": "BM25 Fragments",
    "retrieval.rag": "RAG Context",
    "retrieval.files": "Context Files",
    "retrieval.other": "Other Retrieval",
}

# ═══════════════════════════════════════════════════════════════════
# PROFILER STATE
# ═══════════════════════════════════════════════════════════════════

# Thread-local request context
_request_local = threading.local()


def _current_request_id() -> str:
    """Return a unique request id for this profiling session."""
    override = os.environ.get(ENV_REQUEST_ID)
    if override:
        return override
    if not hasattr(_request_local, "request_counter"):
        _request_local.request_counter = 0
    _request_local.request_counter += 1
    return f"req_{_request_local.request_counter:06d}"


def _profiling_level() -> str:
    """Read profiling level from environment."""
    return os.environ.get(ENV_VAR, "off").strip().lower()


def _format_tokens(text: Union[str, list, dict]) -> int:
    """Estimate tokens from text (or JSON-serializable data).

    Uses the same char/4 heuristic as context_breakdown.py.
    """
    if isinstance(text, str):
        return max(1, (len(text) + 3) // 4)
    try:
        return max(1, (len(json.dumps(text, ensure_ascii=False, default=str)) + 3) // 4)
    except Exception:
        return 0


def _estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate tokens for a list of OpenAI-format messages.

    Each message is: role overhead (~4 tokens) + content tokens.
    This is the same heuristic used by model_metadata.estimate_messages_tokens_rough.
    """
    total = 0
    for msg in messages:
        total += 4  # role overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _format_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += _format_tokens(part.get("text", ""))
        # Tool calls have additional overhead
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                total += _format_tokens(tc.get("function", {}).get("name", ""))
                total += _format_tokens(tc.get("function", {}).get("arguments", ""))
    return total


# ═══════════════════════════════════════════════════════════════════
# PROFILE DATA
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ProfileEntry:
    """A single profiled component."""
    category: str           # primary category key
    subcategory: str = ""   # optional sub-category
    estimated_tokens: int = 0
    char_count: int = 0
    item_count: int = 0      # messages, memories, tools
    label: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = SUBCAT_LABELS.get(self.subcategory, self.subcategory)
            if not self.label:
                self.label = CATEGORY_LABELS.get(self.category, self.category)


@dataclass
class UsageRecord:
    """Actual provider usage data (from API response)."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    provider: str = ""
    model: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfileReport:
    """Complete token profile for one request."""
    request_id: str
    timestamp: str = ""
    entries: List[ProfileEntry] = field(default_factory=list)
    usage: Optional[UsageRecord] = None
    final_estimate: int = 0
    sum_categories: int = 0
    unattributed: int = 0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"

    @property
    def total_estimated(self) -> int:
        return sum(e.estimated_tokens for e in self.entries) if self.entries else 0

    @property
    def category_totals(self) -> Dict[str, int]:
        """Sum by primary category."""
        totals: Dict[str, int] = {}
        for e in self.entries:
            totals[e.category] = totals.get(e.category, 0) + e.estimated_tokens
        return totals

    def add(self, entry: ProfileEntry) -> None:
        self.entries.append(entry)

    def finalize(self, final_estimate: int) -> None:
        self.final_estimate = final_estimate
        self.sum_categories = sum(e.estimated_tokens for e in self.entries)
        self.unattributed = final_estimate - self.sum_categories

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "estimated": {
                "total_input_tokens": self.total_estimated,
                "final_request_tokens": self.final_estimate,
                "sum_categories": self.sum_categories,
                "unattributed_tokens": self.unattributed,
            },
            "categories": {},
            "entries": [],
        }
        # Category totals
        for cat, total in sorted(self.category_totals.items()):
            pct = (total / max(self.total_estimated, 1)) * 100
            result["categories"][cat] = {
                "tokens": total,
                "pct": round(pct, 1),
                "label": CATEGORY_LABELS.get(cat, cat),
            }

        # Individual entries
        for entry in self.entries:
            result["entries"].append({
                "category": entry.category,
                "subcategory": entry.subcategory,
                "label": entry.label,
                "estimated_tokens": entry.estimated_tokens,
                "char_count": entry.char_count,
                "item_count": entry.item_count,
            })

        # Actual usage
        if self.usage:
            result["actual"] = {
                "input_tokens": self.usage.prompt_tokens,
                "output_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
                "cache_write_tokens": self.usage.cache_write_tokens,
                "provider": self.usage.provider,
                "model": self.usage.model,
                "raw": self.usage.raw,
            }
            # Compare estimated vs actual
            if self.usage.prompt_tokens is not None:
                diff = self.usage.prompt_tokens - self.total_estimated
                diff_pct = (diff / max(self.usage.prompt_tokens, 1)) * 100
                result["comparison"] = {
                    "estimated_input": self.total_estimated,
                    "actual_input": self.usage.prompt_tokens,
                    "difference": diff,
                    "difference_pct": round(diff_pct, 2),
                }
        return result

    def render(self, level: str = "summary") -> str:
        """Render a human-readable profile report.

        Args:
            level: "summary" or "verbose"
        """
        lines: List[str] = []
        cat_totals = self.category_totals
        total_est = self.total_estimated

        lines.append("─" * 50)
        lines.append(f"  MIA BRAIN TOKEN PROFILE  [{self.request_id}]")
        lines.append("─" * 50)

        if total_est == 0:
            lines.append("  (no profiled content)")
            return "\n".join(lines)

        # Per-category breakdown
        # Sort by token count descending
        sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])

        for cat, tok in sorted_cats:
            pct = (tok / max(total_est, 1)) * 100
            label = CATEGORY_LABELS.get(cat, cat)
            lines.append(f"  {label:30s} {tok:>7d} tokens  ({pct:>5.1f}%)")

        lines.append("─" * 50)
        lines.append(f"  {'Estimated Total':30s} {total_est:>7d} tokens")

        # Final request estimate (if different from sum)
        if self.final_estimate and self.final_estimate != total_est:
            lines.append(f"  {'Final Request Est.':30s} {self.final_estimate:>7d} tokens")
            if self.unattributed:
                lines.append(f"  {'Unattributed':30s} {self.unattributed:>7d} tokens")

        # Actual usage comparison
        if self.usage and self.usage.prompt_tokens is not None:
            actual = self.usage.prompt_tokens
            lines.append("─" * 50)
            lines.append(f"  {'ACTUAL PROVIDER INPUT':30s} {actual:>7d} tokens")
            if self.usage.completion_tokens is not None:
                lines.append(f"  {'ACTUAL OUTPUT':30s} {self.usage.completion_tokens:>7d} tokens")
            diff = actual - total_est
            diff_pct = (diff / max(actual, 1)) * 100
            lines.append(f"  {'Difference':30s} {diff:>+7d} tokens  ({diff_pct:+.2f}%)")
            if self.usage.cache_read_tokens is not None:
                lines.append(f"  {'Cache read':30s} {self.usage.cache_read_tokens:>7d} tokens")
            if self.usage.cache_write_tokens is not None:
                lines.append(f"  {'Cache write':30s} {self.usage.cache_write_tokens:>7d} tokens")

        # Top contributors (verbose only)
        if level == "verbose" and len(self.entries) > len(cat_totals):
            lines.append("─" * 50)
            lines.append("  TOP CONTRIBUTORS:")
            top_items = sorted(self.entries, key=lambda e: -e.estimated_tokens)[:10]
            for i, entry in enumerate(top_items, 1):
                label = entry.label or SUBCAT_LABELS.get(entry.subcategory, entry.subcategory)
                count_str = f" ({entry.item_count} items)" if entry.item_count > 1 else ""
                lines.append(f"  {i:2d}. {label:30s} {entry.estimated_tokens:>7d} tokens{count_str}")

        lines.append("─" * 50)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# PROFILER
# ═══════════════════════════════════════════════════════════════════

class TokenProfiler:
    """Profile token usage per LLM request.

    Usage:
        profiler = TokenProfiler(level="summary")
        profiler.add("system", system_prompt_text, subcategory="system.core")
        profiler.add("history", messages_list, subcategory="history.total")
        profiler.add("memory", memory_text, subcategory="memory.memory")
        profiler.add_usage({"prompt_tokens": 12345, "completion_tokens": 678})
        report = profiler.report()
        print(profiler.render())
    """

    # Class-level switch — ALL profilers share this
    _disabled: bool = False

    def __init__(
        self,
        level: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Initialize profiler.

        Args:
            level: "off", "summary", "verbose". Defaults to env var CAPTN_TOKEN_PROFILING.
            request_id: Optional unique request identifier.
        """
        if level is None:
            level = _profiling_level()
        self.level = level
        self._enabled = level != "off" and not self._disabled
        self.request_id = request_id or _current_request_id()
        self.entries: List[ProfileEntry] = []
        self.usage: Optional[UsageRecord] = None
        self.final_estimate: Optional[int] = None

    @classmethod
    def is_disabled(cls) -> bool:
        """Fast check — use to skip profiler setup entirely when profiling is off."""
        return cls._disabled or _profiling_level() == "off"

    @classmethod
    def disable(cls) -> None:
        """Globally disable all profilers (tests, performance)."""
        cls._disabled = True

    @classmethod
    def enable(cls) -> None:
        """Re-enable profiling."""
        cls._disabled = False

    # ── Add content ────────────────────────────────────────────────

    def add(
        self,
        category: str,
        content: Union[str, list, dict, None],
        *,
        subcategory: str = "",
        item_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a content component to the profile.

        Args:
            category: Primary category key (system_prompt, conversation_history, etc.)
            content: Text, message list, or JSON-serializable data.
            subcategory: Optional finer-grained key (system.core, history.user, etc.)
            item_count: Number of items (messages, memories, tools) — counted automatically
                        when content is a list.
            metadata: Additional metadata for verbose output.
        """
        if not self._enabled:
            return

        if content is None:
            return

        if isinstance(content, list) and item_count == 0:
            item_count = len(content)

        tokens = _format_tokens(content)
        if tokens == 0:
            return

        # Char count for diagnostics
        if isinstance(content, str):
            char_count = len(content)
        elif isinstance(content, (list, dict)):
            try:
                char_count = len(json.dumps(content, ensure_ascii=False, default=str))
            except Exception:
                char_count = 0
        else:
            char_count = 0

        entry = ProfileEntry(
            category=category,
            subcategory=subcategory,
            estimated_tokens=tokens,
            char_count=char_count,
            item_count=item_count,
            metadata=metadata or {},
        )
        self.entries.append(entry)

    def add_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        subcategory: str = "history.total",
    ) -> None:
        """Add conversation messages. Splits by role automatically when verbose."""
        if not self._enabled:
            return

        user_msgs = sum(1 for m in messages if m.get("role") == "user")
        asst_msgs = sum(1 for m in messages if m.get("role") == "assistant")
        tool_msgs = sum(1 for m in messages if m.get("role") == "tool")

        # Total estimate using message-aware tokenizer
        total_tokens = _estimate_messages_tokens(messages)

        entry = ProfileEntry(
            category="conversation_history",
            subcategory=subcategory,
            estimated_tokens=total_tokens,
            char_count=sum(len(m.get("content", "")) for m in messages),
            item_count=len(messages),
            metadata={
                "user_count": user_msgs,
                "assistant_count": asst_msgs,
                "tool_count": tool_msgs,
            },
        )
        self.entries.append(entry)

        # Verbose: per-role breakdown
        if self.level == "verbose":
            for role, subcat in [
                ("user", "history.user"),
                ("assistant", "history.assistant"),
                ("tool", "history.tool"),
            ]:
                role_msgs = [m for m in messages if m.get("role") == role]
                if role_msgs:
                    tok = sum(
                        _format_tokens(m.get("content", ""))
                        for m in role_msgs
                    )
                    self.entries.append(ProfileEntry(
                        category="conversation_history",
                        subcategory=subcat,
                        estimated_tokens=tok,
                        item_count=len(role_msgs),
                    ))

    def add_tools(
        self,
        tools: List[Dict[str, Any]],
        *,
        subcategory: str = "tools.builtin",
    ) -> None:
        """Add tool schemas/definitions."""
        if not self._enabled or not tools:
            return
        tokens = _format_tokens(tools)
        entry = ProfileEntry(
            category="tools",
            subcategory=subcategory,
            estimated_tokens=tokens,
            char_count=len(json.dumps(tools, ensure_ascii=False, default=str)) if tools else 0,
            item_count=len(tools),
            metadata={t.get("function", t).get("name", t.get("name", "?")): 1 for t in tools[:50]},
        )
        self.entries.append(entry)

    def add_memory(
        self,
        memory_text: str,
        *,
        subcategory: str = "memory.memory",
        item_count: int = 0,
    ) -> None:
        """Add long-term memory content."""
        self.add("long_term_memory", memory_text, subcategory=subcategory, item_count=item_count)

    def add_usage(self, usage_data: Dict[str, Any], *, provider: str = "", model: str = "") -> None:
        """Record actual usage from provider response.

        Args:
            usage_data: Dict with keys like prompt_tokens, completion_tokens, etc.
            provider: Provider name (e.g. "openrouter")
            model: Model name (e.g. "deepseek/deepseek-v4-flash")
        """
        if not self._enabled:
            return

        self.usage = UsageRecord(
            prompt_tokens=usage_data.get("prompt_tokens") or usage_data.get("input_tokens"),
            completion_tokens=usage_data.get("completion_tokens") or usage_data.get("output_tokens"),
            total_tokens=usage_data.get("total_tokens"),
            cache_read_tokens=usage_data.get("cache_read_tokens"),
            cache_write_tokens=usage_data.get("cache_write_tokens"),
            provider=provider,
            model=model,
            raw=usage_data,
        )

    def finalize(self, final_text_or_messages: Union[str, List[Dict[str, Any]]]) -> None:
        """Record the final request estimate before sending.

        Compare sum(categories) vs actual request size to detect hidden inflation.
        """
        if not self._enabled:
            return
        self.final_estimate = _format_tokens(final_text_or_messages)
        if isinstance(final_text_or_messages, list):
            self.final_estimate = _estimate_messages_tokens(final_text_or_messages)

    def report(self) -> ProfileReport:
        """Build and return the final profile report."""
        report = ProfileReport(
            request_id=self.request_id,
            entries=self.entries,
            usage=self.usage,
        )
        if self.final_estimate is not None:
            report.finalize(self.final_estimate)
        return report

    def render(self, level: Optional[str] = None) -> str:
        """Render a human-readable profile report."""
        report = self.report()
        return report.render(level or self.level)


# ═══════════════════════════════════════════════════════════════════
# CONVENIENCE API
# ═══════════════════════════════════════════════════════════════════

def profile(
    level: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TokenProfiler:
    """Create a new profiler with the given level.

    Shortcut::
        p = profile("summary")
        p.add("system", text)
        p.add("tools", tool_schemas)
        print(p.render())
    """
    return TokenProfiler(level=level, request_id=request_id)


def estimate_tokens(text: Union[str, list, dict]) -> int:
    """Quick token estimate using char/4 heuristic."""
    return _format_tokens(text)


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Quick token estimate for OpenAI-format messages."""
    return _estimate_messages_tokens(messages)


# ═══════════════════════════════════════════════════════════════════
# CLI STATUS COMMAND
# ═══════════════════════════════════════════════════════════════════

def register_cli(subparsers) -> None:
    p = subparsers.add_parser("token_profile", help="Token profiler status and config")
    sub = p.add_subparsers(dest="token_profile_cmd")

    status_p = sub.add_parser("status", help="Show profiler status")
    status_p.set_defaults(func=_cmd_status)

    test_p = sub.add_parser("test", help="Run a self-test profile")
    test_p.set_defaults(func=_cmd_test)


def _cmd_status(args) -> None:
    level = _profiling_level()
    disabled = TokenProfiler._disabled
    print(f"Token Profiler")
    print(f"  Environment variable: {ENV_VAR}={level}")
    print(f"  Level: {level}")
    print(f"  Disabled: {disabled}")
    print(f"  Active: {'no' if disabled or level == 'off' else 'yes'}")
    print()
    print(f"  Enable with: export {ENV_VAR}=summary")
    print(f"  Verbose:     export {ENV_VAR}=verbose")


def _cmd_test(args) -> None:
    """Self-test: build a profile and render it."""
    prof = TokenProfiler(level="verbose", request_id="test_001")

    # System prompt (simulated)
    prof.add("system_prompt",
             "You are an AI assistant. You help with tasks. Be concise. Be accurate. "
             "Use tools when needed. Follow the user's instructions carefully. "
             "You have access to terminal, file system, and web search.",
             subcategory="system.core",
             item_count=1)

    prof.add("system_prompt",
             "Your personality is helpful, precise, and professional.",
             subcategory="system.personality")

    prof.add("system_prompt",
             "Available skills: math, coding, physics, chemistry, philosophy, data",
             subcategory="system.skills")

    # Conversation history (simulated)
    history = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
        {"role": "user", "content": "What is the population of Paris?"},
        {"role": "assistant", "content": "The population of Paris is approximately 2.1 million within city limits."},
        {"role": "user", "content": "Calculate the GCD of 48 and 180."},
        {"role": "assistant", "content": "The GCD of 48 and 180 is 12."},
        {"role": "user", "content": "Now find the LCM."},
        {"role": "assistant", "content": "The LCM of 48 and 180 is 720."},
        {"role": "user", "content": "Show me how to calculate factorial in Python."},
        {"role": "assistant", "content": "def factorial(n): return 1 if n <= 1 else n * factorial(n-1)"},
    ]
    prof.add_messages(history, subcategory="history.total")

    # Long-term memory
    memory_text = (
        "User prefers Python for scripting.\n"
        "User works on CaptN-BRAIN project.\n"
        "User speaks French and English.\n"
        "Previous session: discussed mirror agent architecture.\n"
        "Known tools: GCD, LCM, factorial, prime check -- all deterministic.\n"
        "User's frequent operations: math and coding.\n"
    )
    prof.add_memory(memory_text, item_count=6)

    # Tools
    tools = [
        {"type": "function", "function": {"name": "web_search", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "write_file", "description": "Write to a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "terminal", "description": "Run a command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "gcd", "description": "GCD of two numbers", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}}},
    ]
    prof.add_tools(tools, subcategory="tools.builtin")

    # User message
    prof.add("user_message", "What is the GCD of 123456 and 789012?", subcategory="", item_count=1)

    # Finalize with the complete request
    full_request = "system message\n" * 100 + "\n".join(m.get("content", "") for m in history) + "\nWhat is the GCD of 123456 and 789012?"
    prof.finalize(full_request)

    # Simulate actual usage
    prof.add_usage(
        {"prompt_tokens": 4250, "completion_tokens": 120, "total_tokens": 4370},
        provider="openrouter",
        model="deepseek/deepseek-v4-flash",
    )

    print(prof.render(level="verbose"))

    # Also print JSON
    import json as _json
    print("\n\nJSON OUTPUT:")
    print(_json.dumps(prof.report().to_dict(), indent=2))


__all__ = [
    "TokenProfiler", "ProfileReport", "ProfileEntry", "UsageRecord",
    "profile", "estimate_tokens", "estimate_messages_tokens",
    "CATEGORY_LABELS", "SUBCAT_LABELS",
]