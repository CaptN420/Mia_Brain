#!/usr/bin/env python3
"""
intent_parser.py — Déterministe NL → structured intent (zero LLM).

Transforme une phrase en langage naturel en un intent structuré sans
aucun appel LLM. Utilise : tokenize → stopword-strip → stem →
fuzzy-match (Jaro-Winkler) → classification du mode → extraction de
paramètres → détection multi-step.

Inspiré par O1-O intent_parser.py (412 LOC, zero LLM).

Usage:
    from captn.runtime.intent_parser import parse_intent, Intent

    intent = parse_intent("generate python code for fibonacci")
    # Intent(mode=BUILD, domain=coding, params={...})
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("intent_parser")

# ── Modes ───────────────────────────────────────────────────────────
MODES = frozenset({
    "BUILD",       # Generate / create / scaffold code
    "DEBUG",       # Debug / fix / repair
    "ANALYZE",     # Analyze / inspect / scan
    "MIRROR",      # Mirror / transform / diversify
    "MUTATE",      # Mutate / evolve / generate variants
    "VALIDATE",    # Validate / check / verify
    "SEARCH",      # Search / query / find
    "CONVERT",     # Convert / translate / transform format
    "ARCHIVE",     # Archive / store / save
    "CHAT",        # General question / explanation
})

# Mode keywords — weighted for disambiguation
_MODE_KEYWORDS: Dict[str, List[Tuple[str, int]]] = {
    "BUILD": [
        ("generate", 10), ("create", 10), ("write", 8), ("build", 8),
        ("make", 6), ("scaffold", 10), ("new", 5), ("produce", 5),
        ("implement", 8), ("code", 4),
    ],
    "DEBUG": [
        ("debug", 10), ("fix", 10), ("repair", 10), ("bug", 10),
        ("error", 8), ("crash", 8), ("issue", 6), ("problem", 5),
        ("broken", 8), ("incorrect", 7), ("wrong", 6),
    ],
    "ANALYZE": [
        ("analyze", 10), ("analyse", 10), ("inspect", 10), ("scan", 9),
        ("check", 7), ("review", 7), ("audit", 8), ("examine", 7),
        ("lint", 10), ("validate", 5), ("quality", 5),
    ],
    "MIRROR": [
        ("mirror", 10), ("reflect", 9), ("opposite", 9), ("invert", 8),
        ("reverse", 8), ("transform", 6), ("diversify", 8),
        ("alternative", 7), ("variant", 7),
    ],
    "MUTATE": [
        ("mutate", 10), ("evolve", 9), ("breed", 8), ("variate", 8),
        ("generation", 5), ("new variant", 8),
    ],
    "VALIDATE": [
        ("validate", 10), ("verify", 10), ("confirm", 8), ("test", 7),
        ("assert", 8), ("check", 6), ("prove", 7),
    ],
    "SEARCH": [
        ("search", 10), ("find", 9), ("query", 9), ("lookup", 8),
        ("seek", 7), ("locate", 7), ("where", 6),
    ],
    "CONVERT": [
        ("convert", 10), ("translate", 10), ("transform", 8),
        ("parse", 8), ("serialize", 7), ("export", 7), ("import", 7),
        ("to json", 8), ("to yaml", 8),
    ],
    "ARCHIVE": [
        ("archive", 10), ("save", 8), ("store", 8), ("backup", 9),
        ("record", 7), ("persist", 8), ("log", 6),
    ],
}

# ── Domains ─────────────────────────────────────────────────────────
DOMAINS = frozenset({
    "coding", "math", "physics", "chemistry", "alchemy",
    "philosophy", "data", "text", "general",
})

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "coding": ["code", "python", "function", "class", "import", "ast",
               "refactor", "lint", "type hint", "docstring"],
    "math": ["math", "equation", "solve", "derivative", "integral",
             "algebra", "calculus", "sympy", "number"],
    "physics": ["physics", "force", "energy", "velocity", "kinematics",
                "newton", "einstein", "quantum"],
    "chemistry": ["chemistry", "molecule", "reaction", "element",
                  "molar", "compound"],
    "alchemy": ["alchemy", "transmute", "alchimie", "hermetic",
                "philosopher", "stone"],
    "philosophy": ["philosophy", "aristotle", "plato", "kant",
                   "descartes", "nietzsche", "ethics"],
    "data": ["data", "dataset", "jsonl", "crawl", "ingest", "extract"],
    "text": ["text", "translate", "language", "document", "read"],
}


# ── Stopwords ────────────────────────────────────────────────────────
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "was", "are",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall",
    "can", "need", "want", "please", "just", "also", "very", "too",
    "this", "that", "these", "those", "i", "you", "he", "she", "it",
    "we", "they", "me", "my", "your", "his", "her", "its", "our",
    "their", "le", "la", "les", "un", "une", "des", "du", "de", "ce",
    "ces", "et", "ou", "mais", "pour", "avec", "sur", "dans", "par",
}

_WORD_RE = re.compile(r"[a-zàâçéèêëîïôûùüÿñæœ]{2,}")
_NUMBER_RE = re.compile(r"\d+")


# ── Intent data class ────────────────────────────────────────────────
@dataclass
class Intent:
    """Structured intent parsed from natural language.

    Attributes:
        raw:             Original input text
        mode:            One of MODES (BUILD, DEBUG, ANALYZE, …)
        domain:          Knowledge domain (coding, math, …)
        params:          Extracted parameters {name: value}
        keywords:        Extracted keywords (non-stopword tokens)
        confidence:      Classification confidence (0.0–1.0)
        multi_step:      True if multiple actions detected
        sub_intents:     Sub-intents if multi_step
    """
    raw: str
    mode: str = "CHAT"
    domain: str = "general"
    params: Dict[str, Any] = field(default_factory=dict)
    keywords: Set[str] = field(default_factory=set)
    confidence: float = 0.0
    multi_step: bool = False
    sub_intents: List[Intent] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"Intent(mode={self.mode}, domain={self.domain}, "
            f"confidence={self.confidence:.2f}, "
            f"params={self.params})"
        )


# ── Jaro-Winkler fuzzy matching ─────────────────────────────────────
def _jaro_winkler(s1: str, s2: str) -> float:
    """Jaro-Winkler similarity (0.0–1.0)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_dist = max(len1, len2) // 2 - 1
    match_dist = max(match_dist, 0)

    s1_matches = [False] * len1
    s2_matches = [False] * len2

    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while k < len2 and not s2_matches[k]:
            k += 1
        if k < len2 and s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (
        (matches / len1)
        + (matches / len2)
        + ((matches - transpositions / 2) / matches)
    ) / 3

    # Winkler prefix boost
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    return jaro + (prefix * 0.1 * (1 - jaro))


def _fuzzy_match_keyword(word: str, keywords: List[str]) -> Tuple[Optional[str], float]:
    """Fuzzy match a word against a keyword list using Jaro-Winkler."""
    best = None
    best_score = 0.0
    for kw in keywords:
        score = _jaro_winkler(word.lower(), kw.lower())
        if score > best_score and score >= 0.85:
            best_score = score
            best = kw
    return best, best_score


# ── Parsing pipeline ────────────────────────────────────────────────

def _tokenize(text: str) -> Set[str]:
    """Extract meaningful word tokens, lowercased."""
    return {m.group() for m in _WORD_RE.finditer(text.lower()) if m.group() not in _STOPWORDS}


def _classify_mode(words: Set[str], text: str) -> Tuple[str, float]:
    """Classify the intent mode using weighted keyword scoring."""
    low_text = text.lower()
    best_mode = "CHAT"
    best_score = 0.0

    for mode, keywords in _MODE_KEYWORDS.items():
        score = 0.0
        for kw, weight in keywords:
            if kw in low_text:
                score += weight
            else:
                # Fuzzy match each word against the keyword
                for w in words:
                    matched, fuzz_score = _fuzzy_match_keyword(w, [kw])
                    if matched:
                        score += weight * fuzz_score
                        break

        if score > best_score:
            best_score = score
            best_mode = mode

    # Normalize confidence
    max_possible = sum(w for _, w in _MODE_KEYWORDS.get(best_mode, []))
    confidence = min(1.0, best_score / max(max_possible, 1) * 2) if max_possible > 0 else 0.0
    return best_mode, confidence


def _classify_domain(words: Set[str], text: str) -> str:
    """Classify the knowledge domain."""
    low_text = text.lower()
    best_domain = "general"
    best_score = 0

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(3 for kw in keywords if kw in low_text)
        score += sum(2 for w in words if w in keywords)
        if score > best_score:
            best_score = score
            best_domain = domain
    return best_domain


def _extract_params(words: Set[str], text: str) -> Dict[str, Any]:
    """Extract structured parameters from the text."""
    params: Dict[str, Any] = {}
    low_text = text.lower()

    # Numbers
    numbers = _NUMBER_RE.findall(text)
    if numbers:
        params["numbers"] = [int(n) for n in numbers]

    # File paths (containing / or .py)
    path_re = re.findall(r'[\w./\\]+\.(?:py|json|yaml|txt|md|jsonl)', text)
    if path_re:
        params["paths"] = path_re

    # Language detection
    for lang in ("python", "javascript", "typescript", "go", "rust",
                 "java", "c++", "c#", "ruby", "php", "swift"):
        if lang in low_text:
            params["language"] = lang
            break

    # Mode-specific params
    if "file" in words or "fichier" in words:
        file_re = re.findall(r'(?:file|fichier)\s+([\w./\\]+)', low_text)
        if file_re:
            params["file"] = file_re[0]

    return params


def _detect_multi_step(words: Set[str], text: str) -> bool:
    """Detect if the intent contains multiple actions."""
    connectors = {"then", "and", "then", "ensuite", "puis", "also", "plus"}
    separators = {";", "|", "&&"}
    has_connector = any(c in words for c in connectors)
    has_separator = any(s in text for s in separators)
    return has_connector or has_separator


# ── Public API ──────────────────────────────────────────────────────

def parse_intent(text: str) -> Intent:
    """Parse natural language into a structured Intent.

    Args:
        text: Natural language description (e.g. "generate python fibonacci")

    Returns:
        Intent with mode, domain, params, confidence.
    """
    if not text or not text.strip():
        return Intent(raw=text or "", confidence=0.0)

    text = text.strip()
    words = _tokenize(text)
    mode, confidence = _classify_mode(words, text)
    domain = _classify_domain(words, text)
    params = _extract_params(words, text)
    multi_step = _detect_multi_step(words, text)

    intent = Intent(
        raw=text,
        mode=mode,
        domain=domain,
        params=params,
        keywords=words,
        confidence=confidence,
        multi_step=multi_step,
    )

    # Multi-step decomposition
    if multi_step and ";" in text:
        parts = [p.strip() for p in text.split(";") if p.strip()]
        for part in parts[:5]:
            intent.sub_intents.append(parse_intent(part))

    return intent


def format_intent(intent: Intent) -> str:
    """Human-readable representation of an intent."""
    lines = [
        f"Mode:       {intent.mode}",
        f"Domain:     {intent.domain}",
        f"Confidence: {intent.confidence:.0%}",
    ]
    if intent.params:
        lines.append(f"Params:     {intent.params}")
    if intent.multi_step:
        lines.append(f"Steps:      {len(intent.sub_intents) or 'multiple'}")
    if intent.keywords:
        kw = sorted(intent.keywords)
        lines.append(f"Keywords:   {', '.join(kw[:8])}")
        if len(kw) > 8:
            lines[-1] += f" +{len(kw) - 8} more"
    return "\n".join(lines)


__all__ = [
    "Intent", "parse_intent", "format_intent",
    "MODES", "DOMAINS",
]