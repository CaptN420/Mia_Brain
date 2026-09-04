#!/usr/bin/env python3
"""
output_compressor.py — Compress tool/CLI output before it reaches an LLM.

RTK (Rust Token Killer) showed that 60-90% of shell command output is
mechanical noise: progress bars, duplicate lines, timestamps, banners.
This module strips that noise deterministically before the output is fed
to any LLM call, saving input tokens.

Usage:
    from captn.runtime.output_compressor import compress

    # Auto-detect format and compress
    clean = compress(raw_tool_output)

    # Explicit format
    clean = compress(raw, format="git-log")
    clean = compress(raw, format="pytest")
    clean = compress(raw, format="pip")
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("output_compressor")

# ── Pattern libraries ────────────────────────────────────────────────

# Progressive regexes — matched in order, first match wins per line
_PROGRESS_BAR = re.compile(
    r"^(?:\d+%|\d+/\d+|.*[\u2580-\u259f].*|"
    r"⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏|"
    r"[▰▱■□▪▫]+)"
)

_TIMESTAMP_LINE = re.compile(
    r"^\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(:\d{2})?"
    r"(\s*[+-]\d{2}:?\d{2})?\s",
)
_TIMESTAMP_ISO = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}[,.]\d+")

_DOWNLOAD_LINE = re.compile(
    r"^(?:Downloading|Uploading|Fetching|Installing|Collecting|Building)\s",
    re.IGNORECASE,
)

_BANNER_LINE = re.compile(
    r"^(?:─{3,}|═{3,}|={3,}|-{3,}|OK\s|SUCCESS\s|INFO\s|WARNING\s|ERROR\s|DEBUG\s)",
)

_CACHED_LINE = re.compile(r"^\s*(?:Using cached|already satisfied|Requirement already)", re.IGNORECASE)

_GIT_STAT = re.compile(r"^\s*\d+\s+files?\s+changed")

_REPEATED_BLANK = re.compile(r"\n{3,}")


# ── Format-specific compressors ─────────────────────────────────────

def _compress_git_log(text: str) -> str:
    """Git log: keep commit hash + subject, drop timestamps and stats."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _TIMESTAMP_LINE.match(stripped):
            continue
        if stripped.startswith("commit ") and len(stripped) > 40:
            lines.append(stripped[:41])  # hash line
        elif stripped.startswith(("Author:", "Date:")):
            continue
        elif _GIT_STAT.match(stripped):
            continue
        else:
            lines.append(stripped)
    return "\n".join(lines)


def _compress_pytest(text: str) -> str:
    """Pytest: keep test results and failures, drop progress and timing."""
    lines = []
    keep = False
    for line in text.splitlines():
        stripped = line.strip()
        if "FAILED" in stripped or "PASSED" in stripped or "ERROR" in stripped:
            keep = True
            lines.append(line)
        elif stripped.startswith("tests/test_"):
            lines.append(line)
        elif stripped.startswith(("==", "--", "_____")):
            lines.append(line)
        elif stripped.startswith(("test_", "    ", "E   ", "E ")):
            lines.append(line)
        elif keep and not stripped:
            lines.append("")
        elif not stripped:
            continue
    return "\n".join(lines)


def _compress_pip(text: str) -> str:
    """Pip install: keep errors and final summary, drop download bars."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _DOWNLOAD_LINE.match(stripped):
            continue
        if _PROGRESS_BAR.match(stripped):
            continue
        if _CACHED_LINE.match(stripped):
            continue
        if stripped.startswith("Collecting"):
            # Keep the package name, drop version chatter
            lines.append(stripped.split("#")[0].strip())
        else:
            lines.append(stripped)
    return "\n".join(lines)


_FORMAT_COMPRESSORS: Dict[str, Callable[[str], str]] = {
    "git-log": _compress_git_log,
    "gitlog": _compress_git_log,
    "pytest": _compress_pytest,
    "pip": _compress_pip,
}


# ── Generic noise filter ────────────────────────────────────────────

def _strip_noise_lines(text: str) -> List[str]:
    """Remove mechanical noise lines from generic text."""
    out: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _PROGRESS_BAR.match(stripped):
            continue
        if _BANNER_LINE.match(stripped):
            continue
        if _TIMESTAMP_ISO.match(stripped):
            # Keep the message part after the timestamp
            cleaned = re.sub(r"^\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}[,.]\d+\s*", "", stripped)
            if cleaned:
                out.append(cleaned)
            continue
        out.append(stripped)
    return out


def _dedupe_consecutive(lines: List[str]) -> List[str]:
    """Remove consecutive duplicate lines (keep first occurrence)."""
    if not lines:
        return lines
    out: List[str] = [lines[0]]
    for line in lines[1:]:
        if line != out[-1]:
            out.append(line)
    return out


def _compress_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines into 1."""
    return _REPEATED_BLANK.sub("\n\n", text).strip()


# ── Public API ──────────────────────────────────────────────────────

def compress(
    text: str,
    fmt: Optional[str] = None,
    max_lines: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> str:
    """Compress tool output, keeping semantic content.

    Args:
        text: Raw tool/CLI output to compress.
        fmt: Optional format hint (``git-log``, ``pytest``, ``pip``).
        max_lines: Hard cap on output lines (keeps head + tail if exceeded).
        max_chars: Hard cap on output characters.

    Returns:
        Compressed text, stripped of mechanical noise.
    """
    if not text:
        return ""

    original_len = len(text)
    original_lines = text.count("\n") + 1

    # Format-specific compression
    if fmt:
        compressor = _FORMAT_COMPRESSORS.get(fmt)
        if compressor:
            text = compressor(text)
    else:
        # Auto-detect format from content
        auto_fmt = _auto_detect_format(text)
        if auto_fmt:
            compressor = _FORMAT_COMPRESSORS.get(auto_fmt)
            if compressor:
                text = compressor(text)

    # Generic noise stripping (applied always)
    lines = _strip_noise_lines(text)
    lines = _dedupe_consecutive(lines)
    text = "\n".join(lines)
    text = _compress_blank_lines(text)

    # Hard caps
    if max_lines and text.count("\n") + 1 > max_lines:
        lines = text.splitlines()
        half = max_lines // 2
        text = "\n".join(lines[:half] + [f"... ({len(lines) - max_lines} more lines)"] + lines[-half:])

    if max_chars and len(text) > max_chars:
        text = text[:max_chars // 2] + f"\n... [truncated, {len(text) - max_chars} chars dropped]\n" + text[-max_chars // 2:]

    compressed_len = len(text)
    savings = ((original_len - compressed_len) / max(original_len, 1)) * 100
    logger.debug("compress: %d→%d chars (%.0f%% savings, %d→%d lines)",
                 original_len, compressed_len, savings,
                 original_lines, text.count("\n") + 1)

    return text


def _auto_detect_format(text: str) -> Optional[str]:
    """Guess the output format from content."""
    if not text:
        return None
    head = text[:500].lower()
    if "commit " in head and "author:" in head:
        return "git-log"
    if "test session" in head or "tests/test_" in head:
        return "pytest"
    if "collecting" in head or "downloading" in head:
        return "pip"
    return None


def savings_report(text: str, compressed: str) -> dict:
    """Return structured savings metrics."""
    orig_chars = len(text)
    comp_chars = len(compressed)
    return {
        "original_chars": orig_chars,
        "compressed_chars": comp_chars,
        "savings_pct": round(((orig_chars - comp_chars) / max(orig_chars, 1)) * 100, 1),
        "original_lines": text.count("\n") + 1,
        "compressed_lines": compressed.count("\n") + 1,
    }


__all__ = ["compress", "savings_report"]