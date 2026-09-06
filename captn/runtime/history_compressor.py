#!/usr/bin/env python3
"""
history_compressor.py — Compress conversation history into structured summaries.

When a conversation exceeds N exchanges, earlier turns are condensed into a
structured summary that preserves:
  - Established facts (decisions, config, paths)
  - Completed actions (tools called, results obtained)
  - Open questions / pending items
  - Dead ends (approaches tried and abandoned)

This prevents linear context growth while keeping all semantically important
information accessible.

Usage:
    from captn.runtime.history_compressor import compress_history, HistorySummary

    # Compress a conversation history
    summary = compress_history([
        {"role": "user", "content": "set path to /home/project"},
        {"role": "assistant", "content": "OK, path set"},
        ...
    ], max_exchanges=10)
    print(summary.render())
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("history_compressor")

# ── Patterns ──────────────────────────────────────────────────────────
_PATH_SET_RE = re.compile(
    r"(?:path|dir|directory|folder|workspace)[:=]\s*([~/][\w./\\-]+)",
    re.IGNORECASE,
)
_CONFIG_RE = re.compile(
    r"(?:config|setting|parameter|flag)[:=]\s*(\S+)",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(?:^|\s)(error(?!\w)|failed(?!\w)|exception(?!\w)|traceback(?!\w)|crash(?!\w)|bug(?!\w))\s*:?\s*(.+?)[.!]?$",
    re.IGNORECASE | re.MULTILINE,
)
_TOOL_CALL_RE = re.compile(
    r"(?:ran?|called|executed?|trigger|invoke)\s+(?:tool\s+)?([\w_]+)",
    re.IGNORECASE,
)
_RESULT_RE = re.compile(
    r"(?:result|output|returned)\s*(?::|is|was)\s*(.+)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"(?:decide[ds]?|chose?|selected?|opted?|prefer|settled?|confirmed?)\s+(?:on\s+)?(.+?)[.!]?$",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"(?:what|how|why|when|where|who|which|is|are|can|could|should|would|does|has)\s+\S",
    re.IGNORECASE,
)


# ── Data classes ──────────────────────────────────────────────────────
@dataclass
class HistorySummary:
    """Compressed summary of conversation history."""
    facts: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    pending: List[str] = field(default_factory=list)
    dead_ends: List[str] = field(default_factory=list)
    exchange_count: int = 0
    original_chars: int = 0
    compressed_chars: int = 0

    def render(self, max_lines: int = 30) -> str:
        """Render the summary as a structured text block."""
        parts: List[str] = []
        if self.facts:
            parts.append("## Established Facts")
            for f in self.facts:
                parts.append(f"  - {f}")
        if self.completed:
            parts.append("## Completed")
            for c in self.completed:
                parts.append(f"  - {c}")
        if self.pending:
            parts.append("## Pending / Open")
            for p in self.pending:
                parts.append(f"  - {p}")
        if self.dead_ends:
            parts.append("## Dead Ends")
            for d in self.dead_ends:
                parts.append(f"  - {d}")
        parts.append(f"*Summary: {self.exchange_count} exchanges, "
                     f"{len(self.facts)} facts, {len(self.pending)} pending*")

        text = "\n".join(parts)
        # Truncate if too long
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines - 3] + [
                f"... ({len(lines) - max_lines + 3} more lines truncated)",
                lines[-2], lines[-1],
            ]
        return "\n".join(lines)

    def savings_report(self) -> str:
        """Human-readable compression savings."""
        pct = ((self.original_chars - self.compressed_chars)
               / max(self.original_chars, 1) * 100)
        return (
            f"History compressed: {self.original_chars} → {self.compressed_chars} chars "
            f"({pct:.0f}% savings, {self.exchange_count} exchanges → {len(self)} items)"
        )

    def __len__(self) -> int:
        return len(self.facts) + len(self.completed) + len(self.pending) + len(self.dead_ends)

    def __bool__(self) -> bool:
        return len(self) > 0


# ── Extraction helpers ────────────────────────────────────────────────

def _extract_paths(text: str) -> List[str]:
    return _PATH_SET_RE.findall(text)


def _extract_decisions(text: str) -> List[str]:
    return _DECISION_RE.findall(text)


def _extract_errors(text: str) -> List[str]:
    return [m.group(2).strip() for m in _ERROR_RE.finditer(text) if m.group(2).strip()]


def _extract_tool_calls(text: str) -> List[str]:
    return _TOOL_CALL_RE.findall(text)


def _is_question(text: str) -> bool:
    return bool(_QUESTION_RE.search(text))


# ── Compression pipeline ──────────────────────────────────────────────

def compress_history(
    exchanges: List[Dict[str, str]],
    *,
    max_exchanges: int = 10,
    preserve_last: int = 3,
) -> HistorySummary:
    """Compress a list of exchanges into a structured summary.

    Args:
        exchanges: List of dicts with 'role' and 'content' (or 'text').
        max_exchanges: Keep the last N exchanges verbatim; compress older.
        preserve_last: Number of most recent exchanges to keep verbatim
                       (subset of max_exchanges).

    Returns:
        HistorySummary with facts, completed, pending, dead_ends.
    """
    summary = HistorySummary()
    if not exchanges:
        return summary

    original_text = "\n".join(
        e.get("content", e.get("text", "")) for e in exchanges
    )
    summary.original_chars = len(original_text)

    # First pass: collect facts, decisions, errors from all exchanges
    seen_facts: set[str] = set()
    seen_tools: set[str] = set()
    open_questions: list[str] = []

    for i, exchange in enumerate(exchanges):
        role = exchange.get("role", "unknown")
        content = exchange.get("content", exchange.get("text", ""))
        if not content:
            continue

        # Paths and config → facts
        paths = _extract_paths(content)
        for p in paths:
            if p not in seen_facts:
                summary.facts.append(f"Path: {p}")
                seen_facts.add(p)

        # Decisions → facts
        decisions = _extract_decisions(content)
        for d in decisions:
            d_clean = d.strip().rstrip(".")
            if d_clean not in seen_facts:
                summary.facts.append(d_clean)
                seen_facts.add(d_clean)

        # Tool calls → completed
        tools = _extract_tool_calls(content)
        for t in tools:
            if t not in seen_tools:
                summary.completed.append(f"Called tool: {t}")
                seen_tools.add(t)

        # Errors → dead ends
        errors = _extract_errors(content)
        for err in errors:
            if err not in summary.dead_ends:
                summary.dead_ends.append(f"Error: {err}")

        # Questions (user role) → pending
        if role == "user" and _is_question(content):
            # Extract the question subject (first 60 chars)
            q = content.strip()[:80].rstrip("?.") + "?"
            if q not in open_questions:
                open_questions.append(q)

    # Add open questions to pending
    summary.pending = open_questions[:10]  # limit to 10

    summary.exchange_count = len(exchanges)

    # Render compressed text and measure
    compressed = summary.render(max_lines=50)
    summary.compressed_chars = len(compressed)

    savings = ((summary.original_chars - summary.compressed_chars)
               / max(summary.original_chars, 1) * 100)
    logger.info("compress_history: %d chars → %d chars (%.0f%% savings, %d exchanges)",
                summary.original_chars, summary.compressed_chars,
                savings, summary.exchange_count)

    return summary


def format_compressed_history(
    summary: HistorySummary,
    recent_exchanges: List[Dict[str, str]],
    max_recent: int = 3,
) -> str:
    """Format compressed history + recent exchanges for context injection.

    This is the main entry point for use in CaptN-BRAIN context building.

    Args:
        summary: Compressed history summary.
        recent_exchanges: The most recent exchanges (verbatim).
        max_recent: How many recent exchanges to include.

    Returns:
        Formatted context string.
    """
    parts: List[str] = []

    # Compressed summary first
    if summary:
        parts.append("[Compressed History]")
        parts.append(summary.render(max_lines=25))
        parts.append("")

    # Recent exchanges (verbatim, truncated)
    if recent_exchanges:
        parts.append("[Recent Exchanges]")
        for ex in recent_exchanges[-max_recent:]:
            role = ex.get("role", "?").upper()
            content = ex.get("content", ex.get("text", ""))
            # Truncate very long exchanges
            if len(content) > 500:
                content = content[:250] + f"\n... [{len(content) - 500} more chars]"
            parts.append(f"{role}: {content}")
        parts.append("")

    return "\n".join(parts)


__all__ = [
    "HistorySummary", "compress_history", "format_compressed_history",
]