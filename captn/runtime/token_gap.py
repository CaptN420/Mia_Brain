#!/usr/bin/env python3
"""
token_gap.py — Diagnostic investigation of the 64k vs 150k token gap.

Investigates why Hermes ``chars/4`` heuristic estimates ~64k tokens for a
production request while OpenRouter reports ~150k actual input tokens.

Root causes investigated:
  1. Tokenizer mismatch (chars/4 vs real tokenizer encoding)
  2. Tool schema JSON serialization overhead
  3. Duplicate content in the payload
  4. Serialization overhead (api_kwargs JSON)
  5. Provider-side token counting differences

Usage:
    from captn.runtime.token_gap import TokenGapAnalyzer, analyze_gap

    analyzer = TokenGapAnalyzer()
    gap = analyzer.analyze(messages=messages, tools=tools)
    print(gap.report())

    # Or integrate with HermesProfiler:
    gap_fields = analyzer.extract_diagnostics(messages, tools)
    # -> dict with chars, hashes, per-tool sizes

Zero behavioral changes. Observation only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("token_gap")

# ═══════════════════════════════════════════════════════════════════
# TOKENIZER INVESTIGATION
# ═══════════════════════════════════════════════════════════════════

# Try to load tiktoken for real tokenizer estimates
_tiktoken_available = False
_tiktoken_encodings: Dict[str, Any] = {}

try:
    import tiktoken
    _tiktoken_available = True
except ImportError:
    pass

# Try the DeepSeek tokenizer via direct download
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
        logger.info("DeepSeek tokenizer loaded via direct download")
except Exception:
    pass


def _resolve_tiktoken_encoding(model: str) -> Optional[Any]:
    """Resolve a tiktoken encoding for a model name.

    Caches results per model.
    """
    if model in _tiktoken_encodings:
        return _tiktoken_encodings[model]

    if not _tiktoken_available:
        return None

    try:
        encoding = tiktoken.encoding_for_model(model)
        _tiktoken_encodings[model] = encoding
        return encoding
    except Exception:
        pass

    # For OpenRouter models like "deepseek/deepseek-v4-flash", try the base name
    model_short = model.split("/")[-1] if "/" in model else model
    try:
        encoding = tiktoken.encoding_for_model(model_short)
        _tiktoken_encodings[model] = encoding
        return encoding
    except Exception:
        pass

    # Fallback: try known encodings by model family
    model_lower = model.lower()
    if any(k in model_lower for k in ("gpt-4", "gpt-3.5", "gpt-4o")):
        try:
            encoding = tiktoken.get_encoding("o200k_base")
            _tiktoken_encodings[model] = encoding
            return encoding
        except Exception:
            pass
    elif any(k in model_lower for k in ("claude", "anthropic")):
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            _tiktoken_encodings[model] = encoding
            return encoding
        except Exception:
            pass
    elif any(k in model_lower for k in ("deepseek", "codestral", "mistral")):
        try:
            # DeepSeek uses its own tokenizer; cl100k_base is a rough approximation
            encoding = tiktoken.get_encoding("cl100k_base")
            _tiktoken_encodings[model] = encoding
            return encoding
        except Exception:
            pass

    return None


def _tiktoken_count(text: str, encoding: Any) -> int:
    """Count tokens using tiktoken encoding."""
    if encoding is None or not text:
        return 0
    try:
        return len(encoding.encode(text))
    except Exception:
        # Fallback for binary/unusual content
        return 0


def _count_tokens_real(text: str, model: str = "") -> Tuple[int, str]:
    """Count tokens using the best available method.

    Priority:
      1. DeepSeek tokenizer (direct download) for DeepSeek models
      2. tiktoken for known models
      3. Fallback to chars/4

    Returns (token_count, method_name).
    """
    if not text:
        return 0, "empty"

    # Priority 1: DeepSeek tokenizer for DeepSeek models
    model_lower = model.lower()
    if _deepseek_tokenizer is not None and "deepseek" in model_lower:
        try:
            n = len(_deepseek_tokenizer.encode(text))
            return n, "deepseek_tokenizer"
        except Exception:
            pass

    # Priority 2: tiktoken
    if _tiktoken_available:
        enc = _resolve_tiktoken_encoding(model)
        if enc is not None:
            try:
                n = _tiktoken_count(text, enc)
                enc_name = getattr(enc, "name", str(type(enc).__name__))
                return n, f"tiktoken({enc_name})"
            except Exception:
                pass

        # Try generic tiktoken encoding
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            n = _tiktoken_count(text, enc)
            return n, "tiktoken(cl100k_base)"
        except Exception:
            pass

    # Fallback: chars/4
    return max(1, (len(text) + 3) // 4), "chars/4"


# ═══════════════════════════════════════════════════════════════════
# DUPLICATE DETECTION
# ═══════════════════════════════════════════════════════════════════

def _content_hash(text: str) -> str:
    """SHA-256 prefix of content (8 chars, no content stored)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _detect_duplicates(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Detect duplicate content in the payload using content hashes.

    Returns dict with:
      - duplicate_count: number of duplicate content blocks found
      - duplicate_chars: total characters of duplicated content
      - duplicate_by_type: breakdown by message role
      - duplicate_by_system: count of system prompts with identical content
      - duplicate_by_tool_result: count of tool results with identical content
    """
    content_hashes: Dict[str, List[Tuple[int, str]]] = {}
    duplicate_count = 0
    duplicate_chars = 0
    dup_by_type: Dict[str, int] = {}

    for idx, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        content_str = content if isinstance(content, str) else json.dumps(content, default=str)

        if not content_str.strip():
            continue

        h = _content_hash(content_str)
        if h in content_hashes:
            # Found a duplicate
            dup_count = 1
            for prev_idx, prev_role in content_hashes[h]:
                # Only count as duplicate if same role (system prompts can be identical)
                if prev_role == role:
                    dup_count = 1
                    break
            duplicate_count += dup_count
            duplicate_chars += len(content_str)
            dup_by_type[role] = dup_by_type.get(role, 0) + dup_count
            content_hashes[h].append((idx, role))
        else:
            content_hashes[h] = [(idx, role)]

    # Also check tool schemas for duplicates
    tool_hashes: Dict[str, int] = {}
    tool_dup_count = 0
    tool_dup_chars = 0
    for tool in (tools or []):
        tool_str = json.dumps(tool, sort_keys=True, default=str)
        if not tool_str.strip():
            continue
        h = _content_hash(tool_str)
        if h in tool_hashes:
            tool_dup_count += 1
            tool_dup_chars += len(tool_str)
        else:
            tool_hashes[h] = 1

    return {
        "duplicate_count": duplicate_count + tool_dup_count,
        "duplicate_chars": duplicate_chars + tool_dup_chars,
        "duplicate_messages": duplicate_count,
        "duplicate_message_chars": duplicate_chars,
        "duplicate_tools": tool_dup_count,
        "duplicate_tool_chars": tool_dup_chars,
        "duplicate_by_type": dup_by_type,
    }


# ═══════════════════════════════════════════════════════════════════
# TOOL SIZE ANALYSIS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ToolSizeEntry:
    """Size and attribution for one tool schema."""
    name: str
    serialized_chars: int
    estimated_tokens: int
    tokenizer_tokens: int = 0
    tokenizer_method: str = "chars/4"
    source: str = "tools.builtin"
    is_mcp: bool = False
    is_subagent: bool = False


def _analyze_tools(
    tools: Optional[List[Dict[str, Any]]],
    model: str = "",
) -> Dict[str, Any]:
    """Measure every tool independently.

    Returns dict with:
      - total_chars: sum of serialized tool character counts
      - total_estimated: sum of chars/4 estimates
      - total_tokenizer: sum of real tokenizer counts (if available)
      - tokenizer_method: method used for tokenizer counts
      - tool_count: number of tools
      - top_10: list of ToolSizeEntry for the largest tools
      - all_tools: list of ToolSizeEntry for all tools
      - by_source: {source: total_chars} breakdown
    """
    entries: List[ToolSizeEntry] = []
    total_chars = 0
    total_estimated = 0
    total_tokenizer = 0
    tokenizer_method = "chars/4"

    for tool in (tools or []):
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        name = fn.get("name", str(tool)[:40]) if isinstance(fn, dict) else str(tool)[:40]

        serialized = json.dumps(tool, ensure_ascii=False, default=str)
        chars = len(serialized)
        est = max(1, (chars + 3) // 4)

        # Try real tokenizer
        t_tok = 0
        t_method = "chars/4"
        if _tiktoken_available and model:
            tok, t_method = _count_tokens_real(serialized, model)
            t_tok = tok
            tokenizer_method = t_method

        source = "tools.builtin"
        is_mcp = name.startswith("mcp_")
        is_subagent = name == "delegate_task"
        if is_mcp:
            source = "tools.mcp"
        elif is_subagent:
            source = "tools.subagent"

        entries.append(ToolSizeEntry(
            name=name, serialized_chars=chars, estimated_tokens=est,
            tokenizer_tokens=t_tok, tokenizer_method=t_method,
            source=source, is_mcp=is_mcp, is_subagent=is_subagent,
        ))
        total_chars += chars
        total_estimated += est
        total_tokenizer += t_tok

    # Sort by serialized chars descending
    entries.sort(key=lambda x: -x.serialized_chars)

    # Breakdown by source
    by_source: Dict[str, int] = {}
    for e in entries:
        by_source[e.source] = by_source.get(e.source, 0) + e.serialized_chars

    return {
        "total_chars": total_chars,
        "total_estimated_tokens": total_estimated,
        "total_tokenizer_tokens": total_tokenizer,
        "tokenizer_method": tokenizer_method,
        "tool_count": len(entries),
        "top_10": entries[:10],
        "all_tools": entries,
        "by_source": by_source,
    }


# ═══════════════════════════════════════════════════════════════════
# PAYLOAD ANALYSIS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PayloadAnalysis:
    """Detailed character-level analysis of a request payload."""
    # Message counts
    total_messages: int = 0
    system_messages: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    tool_messages: int = 0

    # Character counts by role
    system_chars: int = 0
    history_chars: int = 0  # user + assistant + tool (non-current)
    current_user_chars: int = 0
    tool_result_chars: int = 0

    # Total character counts
    messages_chars: int = 0
    tools_chars: int = 0
    api_kwargs_serialized_chars: int = 0

    # Token estimates
    heuristic_messages: int = 0
    heuristic_tools: int = 0
    heuristic_total: int = 0
    tokenizer_messages: int = 0
    tokenizer_tools: int = 0
    tokenizer_total: int = 0
    tokenizer_method: str = "chars/4"

    # Duplicates
    duplicates: Dict[str, Any] = field(default_factory=dict)

    # Tool analysis
    tool_analysis: Dict[str, Any] = field(default_factory=dict)

    # Gap metrics
    actual_input_tokens: Optional[int] = None
    actual_vs_estimated_ratio: Optional[float] = None
    actual_minus_estimated: Optional[int] = None
    tokenizer_gap: Optional[int] = None
    tokenizer_vs_actual: Optional[float] = None

    # Serialization overhead
    serialization_overhead_chars: int = 0  # JSON wrapper chars (messages array, tool array, etc.)

    def render(self) -> str:
        lines: List[str] = []
        sep = "─" * 52

        lines.append(sep)
        lines.append("  HERMES TOKEN GAP INVESTIGATION")
        lines.append(sep)

        lines.append(f"  Messages: {self.total_messages} ({self.system_messages} sys, "
                      f"{self.user_messages} user, {self.assistant_messages} asst, "
                      f"{self.tool_messages} tool)")
        lines.append(f"  Tools:    {self.tool_analysis.get('tool_count', 0)}")

        lines.append(sep)
        lines.append(f"  {'Character counts:':30s}")
        lines.append(f"    Messages total:              {self.messages_chars:>8,d}")
        lines.append(f"      System:                    {self.system_chars:>8,d}")
        lines.append(f"      History (user+asst+tool):  {self.history_chars:>8,d}")
        lines.append(f"      Current user:              {self.current_user_chars:>8,d}")
        lines.append(f"      Tool results:              {self.tool_result_chars:>8,d}")
        lines.append(f"    Tools / schemas:             {self.tools_chars:>8,d}")
        lines.append(f"    Serialization overhead:      {self.serialization_overhead_chars:>8,d}")

        lines.append(sep)
        lines.append(f"  {'Token estimates:':30s}")
        lines.append(f"    Heuristic (chars/4):         {self.heuristic_total:>8,d}")
        lines.append(f"      Messages:                  {self.heuristic_messages:>8,d}")
        lines.append(f"      Tools:                     {self.heuristic_tools:>8,d}")
        if self.tokenizer_total > 0:
            lines.append(f"    Tokenizer ({self.tokenizer_method}):  {self.tokenizer_total:>8,d}")
            lines.append(f"      Messages:                  {self.tokenizer_messages:>8,d}")
            lines.append(f"      Tools:                     {self.tokenizer_tools:>8,d}")

        lines.append(sep)
        if self.actual_input_tokens is not None:
            lines.append(f"  {'OpenRouter actual input:':30s} {self.actual_input_tokens:>8,d}")
            if self.actual_vs_estimated_ratio is not None:
                lines.append(f"  {'Actual/Estimated ratio:':30s} {self.actual_vs_estimated_ratio:>.2f}x")
            if self.actual_minus_estimated is not None:
                lines.append(f"  {'Actual - Estimated gap:':30s} {self.actual_minus_estimated:>+8,d}")
            if self.tokenizer_total > 0 and self.actual_input_tokens:
                t_gap = self.actual_input_tokens - self.tokenizer_total
                t_ratio = self.actual_input_tokens / max(self.tokenizer_total, 1)
                lines.append(f"  {'Actual - Tokenizer gap:':30s} {t_gap:>+8,d}")
                lines.append(f"  {'Actual/Tokenizer ratio:':30s} {t_ratio:>.2f}x")

        # Duplicates
        if self.duplicates:
            dup = self.duplicates
            if dup.get("duplicate_count", 0) > 0:
                lines.append(sep)
                lines.append(f"  {'Duplicate content:':30s}")
                lines.append(f"    Duplicate blocks:            {dup['duplicate_count']:>8,d}")
                lines.append(f"    Duplicate chars:             {dup['duplicate_chars']:>8,d}")

        # Tool breakdown
        ta = self.tool_analysis
        if ta.get("top_10"):
            lines.append(sep)
            lines.append(f"  {'Top 10 largest tools:':30s}")
            lines.append(f"  {'Name':40s} {'Chars':>8s} {'Est tok':>8s} {'Type':>12s}")
            for t in ta["top_10"]:
                lines.append(f"  {t.name:40s} {t.serialized_chars:>8,d} {t.estimated_tokens:>8d} {t.source:>12s}")

        # By-source tool breakdown
        if ta.get("by_source"):
            lines.append(sep)
            lines.append(f"  {'Tool schema by source:':30s}")
            for src, chars in sorted(ta["by_source"].items(), key=lambda x: -x[1]):
                pct = (chars / max(ta["total_chars"], 1)) * 100
                lines.append(f"    {src:20s}: {chars:>8,d} chars  ({pct:>5.1f}%)")

        lines.append(sep)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# GAP ANALYZER
# ═══════════════════════════════════════════════════════════════════

def _serialize_messages_json(messages: List[Dict[str, Any]]) -> str:
    """Serialize messages as they would be sent to the API.

    This is the critical measurement: the actual JSON bytes sent over the wire.
    """
    return json.dumps(messages, ensure_ascii=False, default=str)


def _serialize_tools_json(tools: Optional[List[Dict[str, Any]]]) -> str:
    """Serialize tools as they would be sent to the API."""
    if not tools:
        return "[]"
    return json.dumps(tools, ensure_ascii=False, default=str)


def _serialize_api_kwargs(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """Simulate the full api_kwargs JSON serialization.

    This is what the OpenAI SDK serializes and sends to OpenRouter.
    """
    kwargs: Dict[str, Any] = {
        "model": "placeholder",
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    return json.dumps(kwargs, ensure_ascii=False, default=str)


def analyze_payload(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    model: str = "",
    actual_input_tokens: Optional[int] = None,
) -> PayloadAnalysis:
    """Analyze a request payload and produce a detailed token gap report.

    Args:
        messages: The final messages array sent to the API.
        tools: The final tools array sent to the API.
        model: Model name for tokenizer resolution.
        actual_input_tokens: Actual input tokens reported by OpenRouter.

    Returns:
        PayloadAnalysis with all diagnostic fields.
    """
    analysis = PayloadAnalysis()

    # ── Message counts and character analysis ──
    messages_json = _serialize_messages_json(messages)
    tools_json = _serialize_tools_json(tools)
    api_kwargs_json = _serialize_api_kwargs(messages, tools)

    analysis.messages_chars = len(messages_json)
    analysis.tools_chars = len(tools_json)
    analysis.api_kwargs_serialized_chars = len(api_kwargs_json)

    # Serialization overhead: the JSON wrapper beyond the raw content
    # This includes: message roles, structure, array brackets, commas, etc.
    raw_content_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            raw_content_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    raw_content_chars += len(part.get("text", ""))
        # Add tool_calls content
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                raw_content_chars += len(fn.get("name", ""))
                raw_content_chars += len(fn.get("arguments", ""))

    analysis.serialization_overhead_chars = analysis.messages_chars - raw_content_chars

    # ── Per-role breakdown ──
    total_system_chars = 0
    total_history_chars = 0
    current_user_chars = 0
    total_tool_result_chars = 0
    total_heuristic_messages = 0

    for idx, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        content_str = content if isinstance(content, str) else json.dumps(content, default=str)
        content_len = len(content_str)

        # Update counts
        analysis.total_messages += 1
        if role == "system":
            analysis.system_messages += 1
            total_system_chars += content_len
        elif role == "user":
            analysis.user_messages += 1
            if idx == len(messages) - 1:
                current_user_chars += content_len
            else:
                total_history_chars += content_len
        elif role == "assistant":
            analysis.assistant_messages += 1
            total_history_chars += content_len
        elif role == "tool":
            analysis.tool_messages += 1
            total_tool_result_chars += content_len
            total_history_chars += content_len

        # "4" = role overhead per message + tool_calls overhead
        total_heuristic_messages += 4
        if isinstance(content, str):
            total_heuristic_messages += max(1, (len(content) + 3) // 4)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_heuristic_messages += max(1, (len(part.get("text", "")) + 3) // 4)
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                total_heuristic_messages += max(1, (len(fn.get("name", "")) + 3) // 4)
                total_heuristic_messages += max(1, (len(fn.get("arguments", "")) + 3) // 4)

    analysis.system_chars = total_system_chars
    analysis.history_chars = total_history_chars
    analysis.current_user_chars = current_user_chars
    analysis.tool_result_chars = total_tool_result_chars
    analysis.heuristic_messages = total_heuristic_messages

    # ── Tool analysis ──
    ta = _analyze_tools(tools, model)
    analysis.tool_analysis = ta
    analysis.heuristic_tools = ta["total_estimated_tokens"]

    # ── Heuristic total ──
    analysis.heuristic_total = analysis.heuristic_messages + analysis.heuristic_tools

    # ── Tokenizer estimate ──
    if _tiktoken_available:
        # Try to count messages with real tokenizer
        tok_msgs, tok_method = _count_tokens_real(messages_json, model)
        analysis.tokenizer_messages = tok_msgs
        analysis.tokenizer_method = tok_method

        # Tools
        tok_tools, _ = _count_tokens_real(tools_json, model)
        analysis.tokenizer_tools = tok_tools

        analysis.tokenizer_total = tok_msgs + tok_tools

    # ── Duplicates ──
    analysis.duplicates = _detect_duplicates(messages, tools)

    # ── Gap metrics ──
    analysis.actual_input_tokens = actual_input_tokens
    if actual_input_tokens is not None:
        analysis.actual_vs_estimated_ratio = round(
            actual_input_tokens / max(analysis.heuristic_total, 1), 2
        )
        analysis.actual_minus_estimated = actual_input_tokens - analysis.heuristic_total
        if analysis.tokenizer_total > 0:
            analysis.tokenizer_gap = actual_input_tokens - analysis.tokenizer_total
            analysis.tokenizer_vs_actual = round(
                actual_input_tokens / max(analysis.tokenizer_total, 1), 2
            )

    return analysis


# ═══════════════════════════════════════════════════════════════════
# DIAGNOSTIC FIELDS FOR ProfileRecord
# ═══════════════════════════════════════════════════════════════════

def extract_diagnostics(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    model: str = "",
    actual_input_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Extract diagnostic fields for ProfileRecord.

    Returns a dict of scalar fields that can be merged into ProfileRecord.
    """
    analysis = analyze_payload(messages, tools, model=model, actual_input_tokens=actual_input_tokens)

    result: Dict[str, Any] = {
        # Character counts
        "messages_chars": analysis.messages_chars,
        "tools_chars": analysis.tools_chars,
        "api_kwargs_chars": analysis.api_kwargs_serialized_chars,
        "system_chars": analysis.system_chars,
        "history_chars": analysis.history_chars,
        "current_user_chars": analysis.current_user_chars,
        "tool_result_chars": analysis.tool_result_chars,
        "serialization_overhead_chars": analysis.serialization_overhead_chars,

        # Tokenizer
        "tokenizer_messages": analysis.tokenizer_messages,
        "tokenizer_tools": analysis.tokenizer_tools,
        "tokenizer_total": analysis.tokenizer_total,
        "tokenizer_method": analysis.tokenizer_method,

        # Duplicates
        "duplicate_count": analysis.duplicates.get("duplicate_count", 0),
        "duplicate_chars": analysis.duplicates.get("duplicate_chars", 0),

        # Gap metrics
        "actual_vs_estimated_ratio": analysis.actual_vs_estimated_ratio,
        "actual_minus_estimated": analysis.actual_minus_estimated,
        "tokenizer_gap": analysis.tokenizer_gap,
        "tokenizer_vs_actual": analysis.tokenizer_vs_actual,

        # Tool breakdown
        "tool_serialized_chars": analysis.tool_analysis.get("total_chars", 0),
        "tool_tokenizer_tokens": analysis.tool_analysis.get("total_tokenizer_tokens", 0),
        "tool_chars_by_source": analysis.tool_analysis.get("by_source", {}),
    }

    # Top 10 largest tools (names only, no content)
    top_tools = analysis.tool_analysis.get("top_10", [])
    result["top_10_tools"] = [
        {"name": t.name, "chars": t.serialized_chars, "tokens": t.estimated_tokens, "source": t.source}
        for t in top_tools
    ]

    return result


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def register_cli(subparsers) -> None:
    p = subparsers.add_parser(
        "gap",
        help="Investigate token gap between heuristic estimate and OpenRouter actual",
    )
    sub = p.add_subparsers(dest="gap_cmd")

    analyze_p = sub.add_parser("analyze", help="Analyze a demo payload")
    analyze_p.set_defaults(func=_cmd_gap_analyze)

    records_p = sub.add_parser("records", help="Analyze token gap across collected records")
    records_p.set_defaults(func=_cmd_gap_records)


def _cmd_gap_analyze(args) -> None:
    """Analyze a demo 150K-token payload."""
    # Build a demo payload similar to _demo_profile
    messages = []
    system = "You are a helpful AI assistant. " * 2000
    messages.append({"role": "system", "content": system})

    for i in range(80):
        messages.append({"role": "user", "content": f"This is user message {i} with some detailed content about the project." * 5})
        messages.append({"role": "assistant", "content": f"This is assistant response {i} with analysis and code." * 10})

    for i in range(20):
        messages.append({"role": "tool", "content": f"Tool result {i}: " + "x" * 5000, "tool_call_id": f"call_{i}"})

    messages.append({"role": "user", "content": "What is the GCD of 123456 and 789012?"})

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

    tools.append({"type": "function", "function": {"name": "mcp_github_search", "description": "Search GitHub", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}})
    tools.append({"type": "function", "function": {"name": "mcp_filesystem_read", "description": "Read files", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}})

    # Analyze
    analysis = analyze_payload(
        messages, tools,
        model="deepseek/deepseek-v4-flash",
        actual_input_tokens=150034,
    )

    print(analysis.render())
    print()

    # Also print diagnostics
    diag = extract_diagnostics(messages, tools, model="deepseek/deepseek-v4-flash", actual_input_tokens=150034)
    import json as _json
    print("DIAGNOSTIC FIELDS:")
    # Print only non-zero and non-empty fields
    for k, v in sorted(diag.items()):
        if v is not None and v != "" and v != 0 and v != {} and v != []:
            if isinstance(v, float):
                print(f"  {k:40s} = {v:.2f}")
            elif isinstance(v, dict):
                print(f"  {k:40s} = {_json.dumps(v)}")
            else:
                print(f"  {k:40s} = {v}")
    print()
    print(f"  tiktoken available: {_tiktoken_available}")
    if _tiktoken_available:
        enc = _resolve_tiktoken_encoding("deepseek/deepseek-v4-flash")
        print(f"  resolved encoding: {enc.name if enc else 'None'}")


def _cmd_gap_records(args) -> None:
    """Analyze token gap across all collected records."""
    from captn.runtime.hermes_profiler import get_all_records

    records = get_all_records()
    if not records:
        print("No records collected yet.")
        return

    n = len(records)
    n_with_actual = sum(1 for r in records if r.actual_input_tokens is not None)

    print(f"Token Gap Analysis: {n} records, {n_with_actual} with actual usage")
    print()

    if n_with_actual == 0:
        return

    # Compute gap metrics
    gaps = []
    for r in records:
        if r.actual_input_tokens is not None:
            gap = r.actual_input_tokens - r.estimated_total
            ratio = r.actual_input_tokens / max(r.estimated_total, 1)
            gaps.append({
                "request_id": r.request_id,
                "model": r.model,
                "estimated": r.estimated_total,
                "actual": r.actual_input_tokens,
                "gap": gap,
                "ratio": round(ratio, 2),
            })

    if not gaps:
        return

    avg_est = sum(g["estimated"] for g in gaps) / len(gaps)
    avg_act = sum(g["actual"] for g in gaps) / len(gaps)
    avg_gap = sum(g["gap"] for g in gaps) / len(gaps)
    avg_ratio = sum(g["ratio"] for g in gaps) / len(gaps)

    print(f"  Average estimated:       {avg_est:>10,.1f}")
    print(f"  Average actual:          {avg_act:>10,.1f}")
    print(f"  Average token gap:       {avg_gap:>+10,.1f}")
    print(f"  Average actual/estimated:{avg_ratio:>10.2f}x")
    print()

    # Largest gaps
    gaps.sort(key=lambda x: -x["gap"])
    print("  Largest token gaps:")
    for g in gaps[:5]:
        print(f"    {g['request_id']:20s}  est={g['estimated']:>8,d}  actual={g['actual']:>8,d}  gap={g['gap']:+>8,d}  ratio={g['ratio']:.2f}x")


__all__ = [
    "TokenGapAnalyzer", "PayloadAnalysis", "ToolSizeEntry",
    "analyze_payload", "extract_diagnostics", "_detect_duplicates",
    "_analyze_tools", "_count_tokens_real",
]