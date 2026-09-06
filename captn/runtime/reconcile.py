#!/usr/bin/env python3
"""
reconcile.py — OpenRouter Token Accounting Reconciliation.

Investigates why OpenRouter reports ~150K input tokens when the local
DeepSeek tokenizer estimates ~44K for the same payload.

Key findings from Hermes Agent source code (agent/usage_pricing.py):

1. OpenRouter returns ``prompt_tokens`` which INCLUDES cached tokens
2. Hermes normalizes: ``input_tokens = prompt_total - cache_read - cache_write``
3. ``CanonicalUsage.prompt_tokens = input_tokens + cache_read + cache_write``
   = the FULL logical prompt (including cached content)

So the 150K reported by OpenRouter is:
- ``prompt_tokens`` = 150,034 (total logical prompt, including 50K cache read + 20K cache write)
- ``input_tokens`` (uncached) = 150,034 - 50,000 - 20,000 = 80,034

The remaining gap between 80K (uncached) and 44K (local tokenizer) is the
primary object of investigation.

Usage:
    from captn.runtime.reconcile import reconcile, OpenRouterAccounting

    report = reconcile(
        messages=messages,
        tools=tools,
        model="deepseek/deepseek-v4-flash",
        provider_prompt_tokens=150034,
        provider_cache_read=50000,
        provider_cache_write=20000,
    )
    print(report.render())
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("reconcile")

# ── DeepSeek tokenizer (downloaded from HuggingFace) ──────────────
_deepseek_tokenizer: Optional[Any] = None
try:
    import requests
    r = requests.get(
        "https://huggingface.co/deepseek-ai/deepseek-v4-flash/raw/main/tokenizer.json",
        timeout=10,
    )
    if r.status_code == 200:
        from tokenizers import Tokenizer as _Tok
        _deepseek_tokenizer = _Tok.from_str(r.text)
        logger.info("DeepSeek tokenizer loaded for reconciliation")
except Exception:
    pass

# ── tiktoken fallback ─────────────────────────────────────────────
_tiktoken_available = False
try:
    import tiktoken
    _tiktoken_available = True
except ImportError:
    pass


def _count(text: str, model: str = "") -> Tuple[int, str]:
    """Count tokens with best available method, returns (count, method)."""
    if not text:
        return 0, "empty"
    ml = model.lower()
    if _deepseek_tokenizer is not None and "deepseek" in ml:
        try:
            return len(_deepseek_tokenizer.encode(text)), "deepseek_tokenizer"
        except Exception:
            pass
    if _tiktoken_available:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text)), "tiktoken(cl100k_base)"
        except Exception:
            pass
    return max(1, (len(text) + 3) // 4), "chars/4"


# ═══════════════════════════════════════════════════════════════════
# ACCOUNTING DATA
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ComponentTokens:
    """Token counts for one component of the payload."""
    label: str
    chars: int = 0
    local_tokens: int = 0
    tokenizer_method: str = "chars/4"
    provider_ratio: Optional[float] = None  # provider_tokens / local_tokens
    notes: str = ""


@dataclass
class OpenRouterAccounting:
    """Complete reconciliation of local vs provider token counts.

    Hermes accounting (from agent/usage_pricing.py):
        - prompt_tokens = total logical prompt INCLUDING cache
        - input_tokens  = prompt_tokens - cache_read - cache_write (uncached new content)
        - cache_read/cache_write = subtractions from the total

    Key insight: the 150K reported by OpenRouter is ``prompt_tokens``
    which includes 50K cache read + 20K cache write. The actual uncached
    new input is 80,034.
    """
    # Payload
    messages_chars: int = 0
    tools_chars: int = 0
    api_kwargs_chars: int = 0
    serialization_overhead_chars: int = 0

    # Local tokenizer estimates
    local_messages_tokens: int = 0
    local_tools_tokens: int = 0
    local_total_tokens: int = 0
    local_tokenizer_method: str = "chars/4"

    # Provider-reported
    provider_prompt_tokens: Optional[int] = None  # total logical prompt (includes cache)
    provider_input_tokens: Optional[int] = None    # uncached input
    provider_output_tokens: Optional[int] = None
    provider_cache_read: Optional[int] = None
    provider_cache_write: Optional[int] = None

    # Component breakdown
    components: List[ComponentTokens] = field(default_factory=list)

    # Gap analysis
    provider_local_gap: Optional[int] = None
    provider_local_ratio: Optional[float] = None
    unexplained_tokens: Optional[int] = None
    status: str = "UNRECONCILED"

    def render(self) -> str:
        sep = "─" * 56
        lines = [sep,
                 "  OPENROUTER TOKEN ACCOUNTING RECONCILIATION",
                 sep]

        lines.append(f"  Model: {self.local_tokenizer_method}")
        lines.append(f"  Payload chars:      {self.api_kwargs_chars:>8,d}")
        lines.append(f"    Messages:          {self.messages_chars:>8,d}")
        lines.append(f"    Tools:             {self.tools_chars:>8,d}")
        lines.append(f"    Serialization ovh: {self.serialization_overhead_chars:>8,d}")
        lines.append("")

        # Token counts
        lines.append(f"  LOCAL TOKENIZER:")
        lines.append(f"    Messages:          {self.local_messages_tokens:>8,d}")
        lines.append(f"    Tools:             {self.local_tools_tokens:>8,d}")
        lines.append(f"    Total:             {self.local_total_tokens:>8,d}")
        lines.append("")

        lines.append(f"  PROVIDER REPORTED:")
        if self.provider_prompt_tokens is not None:
            lines.append(f"    prompt_tokens:     {self.provider_prompt_tokens:>8,d}  (total logical prompt, INCLUDES cache)")
        if self.provider_cache_read is not None:
            lines.append(f"    cache_read:        {self.provider_cache_read:>8,d}")
        if self.provider_cache_write is not None:
            lines.append(f"    cache_write:       {self.provider_cache_write:>8,d}")
        if self.provider_input_tokens is not None:
            lines.append(f"    input_tokens:      {self.provider_input_tokens:>8,d}  (uncached new input = prompt - cache_read - cache_write)")
        lines.append("")

        # Gap analysis
        lines.append(f"  GAP ANALYSIS:")
        if self.provider_prompt_tokens is not None:
            lines.append(f"    Provider prompt_tokens:  {self.provider_prompt_tokens:>8,d}")
            lines.append(f"    Local total:             {self.local_total_tokens:>8,d}")
            lines.append(f"    Gap (prompt - local):   {self.provider_local_gap:>+8,d}  ({self.provider_local_ratio:.2f}x)")
            lines.append("")
            lines.append(f"    Provider input_tokens:   {self.provider_input_tokens:>8,d}")
            gap2 = (self.provider_input_tokens or 0) - self.local_total_tokens
            ratio2 = (self.provider_input_tokens or 0) / max(self.local_total_tokens, 1)
            lines.append(f"    (uncached, no cache)")
            lines.append(f"    Gap (input - local):    {gap2:>+8,d}  ({ratio2:.2f}x)")
            lines.append("")

        # Component breakdown
        if self.components:
            lines.append(f"  COMPONENT BREAKDOWN:")
            lines.append(f"  {'Component':30s} {'Chars':>8s} {'Local tok':>10s} {'Method':>20s}")
            for c in self.components:
                lines.append(f"  {c.label:30s} {c.chars:>8,d} {c.local_tokens:>10d} {c.tokenizer_method:>20s}")
                if c.notes:
                    lines.append(f"  {'':30s} {c.notes:>38s}")
            lines.append("")

        # Status
        if self.unexplained_tokens is not None and self.unexplained_tokens > 0:
            lines.append(f"  UNEXPLAINED: {self.unexplained_tokens:+,d} tokens")
        lines.append(f"  STATUS: {self.status}")
        lines.append(sep)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# RECONCILIATION ENGINE
# ═══════════════════════════════════════════════════════════════════

def reconcile(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    model: str = "",
    provider_prompt_tokens: Optional[int] = None,
    provider_cache_read: Optional[int] = None,
    provider_cache_write: Optional[int] = None,
    provider_output_tokens: Optional[int] = None,
) -> OpenRouterAccounting:
    """Reconcile local token counts with provider-reported usage.

    Args:
        messages: Final messages array.
        tools: Final tools array (or None).
        model: Model name for tokenizer resolution.
        provider_prompt_tokens: ``prompt_tokens`` from provider response.
        provider_cache_read: ``cache_read_tokens`` from provider.
        provider_cache_write: ``cache_write_tokens`` from provider.
        provider_output_tokens: ``completion_tokens`` from provider.

    Returns:
        OpenRouterAccounting with full reconciliation.
    """
    acc = OpenRouterAccounting()

    # ── Serialize ──
    msg_json = json.dumps(messages, ensure_ascii=False, default=str)
    tools_json = json.dumps(tools or [], ensure_ascii=False, default=str)
    kwargs = {"model": model or "unknown", "messages": messages}
    if tools:
        kwargs["tools"] = tools
    api_kwargs_json = json.dumps(kwargs, ensure_ascii=False, default=str)

    # Raw content chars (without serialization markup)
    raw_chars = 0
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            raw_chars += len(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    raw_chars += len(p.get("text", ""))

    # Add role overhead
    role_overhead = len(messages) * 4  # "role": "..." content
    overhead = len(msg_json) - raw_chars

    acc.messages_chars = len(msg_json)
    acc.tools_chars = len(tools_json)
    acc.api_kwargs_chars = len(api_kwargs_json)
    acc.serialization_overhead_chars = overhead

    # ── Local tokenizer ──
    tok_msgs, method_msgs = _count(msg_json, model)
    tok_tools, method_tools = _count(tools_json, model)
    tok_total = tok_msgs + tok_tools

    acc.local_messages_tokens = tok_msgs
    acc.local_tools_tokens = tok_tools
    acc.local_total_tokens = tok_total
    acc.local_tokenizer_method = method_msgs

    # ── Provider accounting ──
    acc.provider_prompt_tokens = provider_prompt_tokens
    acc.provider_cache_read = provider_cache_read
    acc.provider_cache_write = provider_cache_write
    acc.provider_output_tokens = provider_output_tokens

    # input_tokens = prompt_tokens - cache_read - cache_write
    inp = provider_prompt_tokens or 0
    cr = provider_cache_read or 0
    cw = provider_cache_write or 0
    acc.provider_input_tokens = max(0, inp - cr - cw)

    # ── Gap analysis ──
    if provider_prompt_tokens is not None:
        acc.provider_local_gap = provider_prompt_tokens - tok_total
        acc.provider_local_ratio = round(provider_prompt_tokens / max(tok_total, 1), 2)

    # ── Component breakdown ──
    components = []

    # Raw content by role
    role_content = {"system": "", "user": "", "assistant": "", "tool": ""}
    for m in messages:
        role = m.get("role", "other")
        c = m.get("content", "")
        role_content[role] = role_content.get(role, "") + (c if isinstance(c, str) else json.dumps(c, default=str))

    for role in ("system", "user", "assistant", "tool"):
        text = role_content.get(role, "")
        if text:
            tok, meth = _count(text, model)
            components.append(ComponentTokens(
                label=f"content.{role}",
                chars=len(text),
                local_tokens=tok,
                tokenizer_method=meth,
            ))

    # Serialization overhead
    if overhead > 0:
        tok_ovh, _ = _count(" " * overhead, model)
        # overhead is structural markup, not actual tokenizable content
        components.append(ComponentTokens(
            label="serialization_overhead",
            chars=overhead,
            local_tokens=tok_ovh,
            tokenizer_method="estimated",
            notes="JSON structure: roles, commas, brackets, spacing",
        ))

    # Tool schemas
    if tools:
        components.append(ComponentTokens(
            label="tool_schemas",
            chars=len(tools_json),
            local_tokens=tok_tools,
            tokenizer_method=method_tools,
        ))

    acc.components = components

    # ── Status ──
    if provider_prompt_tokens is not None:
        local_sum = sum(c.local_tokens for c in components)
        uk = tok_total - local_sum
        acc.unexplained_tokens = uk

        if abs(uk) < 500:
            acc.status = "RECONCILED"
        elif abs(uk) < 5000:
            acc.status = "PARTIALLY_RECONCILED"
        else:
            acc.status = "NOT_RECONCILED"

    return acc


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def register_cli(subparsers) -> None:
    p = subparsers.add_parser(
        "reconcile",
        help="Reconcile local token counts with OpenRouter actual usage",
    )
    sub = p.add_subparsers(dest="reconcile_cmd")

    demo_p = sub.add_parser("demo", help="Run reconciliation on a demo 150K-token payload")
    demo_p.set_defaults(func=_cmd_reconcile_demo)

    records_p = sub.add_parser("records", help="Reconcile across collected ProfileRecords")
    records_p.set_defaults(func=_cmd_reconcile_records)


def _build_demo_payload():
    """Build a demo payload similar to the 150K-token scenario."""
    messages = []
    messages.append({"role": "system", "content": "You are a helpful AI assistant. " * 2000})
    for i in range(80):
        messages.append({"role": "user", "content": f"This is user message {i} with some detailed content." * 5})
        messages.append({"role": "assistant", "content": f"This is assistant response {i} with analysis and code." * 10})
    for i in range(20):
        messages.append({"role": "tool", "content": f"Tool result {i}: " + "x" * 5000, "tool_call_id": f"call_{i}"})
    messages.append({"role": "user", "content": "What is the GCD of 123456 and 789012?"})

    tools = []
    for i in range(47):
        tools.append({"type": "function", "function": {"name": f"tool_{i}", "description": "Tool description.", "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}, "required": ["a"]}}})
    tools.append({"type": "function", "function": {"name": "mcp_github_search", "description": "Search GitHub", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}})
    tools.append({"type": "function", "function": {"name": "mcp_filesystem_read", "description": "Read files", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}})

    return messages, tools


def _cmd_reconcile_demo(args) -> None:
    messages, tools = _build_demo_payload()
    report = reconcile(
        messages, tools,
        model="deepseek/deepseek-v4-flash",
        provider_prompt_tokens=150034,
        provider_cache_read=50000,
        provider_cache_write=20000,
        provider_output_tokens=920,
    )
    print(report.render())


def _cmd_reconcile_records(args) -> None:
    from captn.runtime.hermes_profiler import get_all_records

    records = get_all_records()
    if not records:
        print("No records collected.")
        return

    print(f"Reconciling {len(records)} records:")
    print()
    for r in records:
        print(f"  {r.request_id:20s}  model={r.model or '?'}  "
              f"estimated={r.estimated_total:,}  "
              f"actual_input={r.actual_input_tokens or '?'}  "
              f"cache_read={r.cache_read_tokens or '?'}  "
              f"cache_write={r.cache_write_tokens or '?'}")


if __name__ == "__main__":
    _cmd_reconcile_demo(None)