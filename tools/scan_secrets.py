#!/usr/bin/env python3
"""Captn pre-commit secret scanner.

Scans candidate files (everything except .gitignored / binary / vendored
dirs) for likely secrets. Exits non-zero if anything suspicious is found,
so it is safe to drop into a pre-commit hook.

Heuristics (intentionally simple, low false-positive):
  1. Known provider-key shapes (OpenAI sk-..., GitHub ghp_..., AWS AKIA...,
     Bearer tokens, and `key=/secret=/token=/password=` assignments with a
     long value) anywhere outside the expected secret stores (.captn/, .env).
  2. High-entropy quoted tokens (>=32 chars), with filesystem-path strings
     excluded (they look high-entropy but are not secrets).

Usage:
    python tools/scan_secrets.py          # scan whole repo
    python tools/scan_secrets.py path/..  # scan a subtree
Exit code: 0 clean, 1 secrets found, 2 usage/error.
"""
from __future__ import annotations

import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))

# Directories we never walk into.
SKIP_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
    "crawled", "mirror", "generated", "Code_base", "_converted",
    "dogfood-output", "wd-40", "alchimie",
}

# Extensions we treat as text and scan.
TEXT_EXT = {
    ".py", ".md", ".bat", ".sh", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".env", ".example", ".cff",
}

# Known provider key prefixes.
PROVIDER_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9]{20,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.=]{20,}\b"),
    re.compile(r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]", re.I),
]

# A "looks like a path" detector so we don't flag filesystem strings.
PATHISH = re.compile(r"[/\\]|(workspace|desktop|users|home|captn|\.py$|\.json$|\.md$)", re.I)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _looks_like_secret(line: str) -> bool:
    """True if a line plausibly contains a hardcoded secret."""
    for pat in PROVIDER_PATTERNS:
        if pat.search(line):
            return True
    # High-entropy quoted token, but not something that just looks like a
    # filesystem path (paths are long alphanumeric strings and would
    # otherwise false-positive like crazy).
    for m in re.finditer(r"['\"]([A-Za-z0-9_\-+/=]{32,})['\"]", line):
        token = m.group(1)
        if PATHISH.search(token):
            continue
        if _shannon_entropy(token) > 4.0:
            return True
    return False


def _scan_file(path: str, rel: str) -> list[str]:
    findings: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return findings

    in_captn = rel.replace("\\", "/").startswith(".captn/")
    in_dotenv = os.path.basename(rel) == ".env" or rel.endswith(".env")

    for i, line in enumerate(lines, 1):
        if _looks_like_secret(line):
            # Secrets in .captn/ and .env are expected; only flag if outside them.
            if in_captn or in_dotenv:
                continue
            findings.append(f"{rel}:{i}: {line.strip()[:160]}")
    return findings


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT
    all_findings: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped dirs in place.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in TEXT_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, REPO_ROOT)
            scanned += 1
            all_findings.extend(_scan_file(full, rel))

    if all_findings:
        print("POTENTIAL SECRETS FOUND — review before committing:\n")
        for f in all_findings:
            print("  " + f)
        print(f"\n({scanned} files scanned.)")
        return 1
    print(f"OK: no secrets detected in {scanned} scanned files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
