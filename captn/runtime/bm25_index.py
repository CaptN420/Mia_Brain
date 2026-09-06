#!/usr/bin/env python3
"""
bm25_index.py — BM25 document scoring for memory/context selectivity.

Provides a BM25+ index that scores documents (fragments, memories, tool
descriptions) against a query and returns only the top-k most relevant ones.

This replaces simple keyword-overlap scoring with proper TF-IDF weighting,
giving substantially better precision for memory retrieval.

Implementation:
  - Standard BM25 with k1=1.5, b=0.75 (Okapi defaults)
  - Tokenizes on [a-zà-ü]{2,} with stopword filtering
  - Query-time scoring: sum(idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl/avgdl)))

Usage:
    from captn.runtime.bm25_index import Bm25Index, build_index

    docs = [
        {"id": "math_gcd", "text": "Greatest common divisor of two integers"},
        {"id": "review_lint", "text": "Run ruff, pylint, flake8, bandit on code"},
    ]
    index = build_index(docs, key="text", stopwords=STOPWORDS)
    results = index.query("find gcd of two numbers", top_k=3)
    # -> [("math_gcd", 2.34), ...]
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("bm25_index")

# ── Default stopwords (FR + EN) ───────────────────────────────────────
_DEFAULT_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "was", "are",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall",
    "can", "need", "want", "please", "just", "also", "very", "too",
    "this", "that", "these", "those", "i", "you", "he", "she", "it",
    "we", "they", "me", "my", "your", "his", "her", "its", "our",
    "their", "le", "la", "les", "un", "une", "des", "du", "de", "ce",
    "ces", "et", "ou", "mais", "pour", "avec", "sur", "dans", "par",
    "not", "no", "nor", "so", "if", "then", "than", "also",
}

_WORD_RE = re.compile(r"[a-zàâçéèêëîïôûùüÿñæœ]{2,}")


def _tokenize(text: str, stopwords: Set[str]) -> List[str]:
    """Lowercase tokenizer with stopword filter."""
    return [m.group() for m in _WORD_RE.finditer(text.lower())
            if m.group() not in stopwords]


# ── BM25 Parameters ───────────────────────────────────────────────────
@dataclass
class Bm25Config:
    """BM25+ parameters."""
    k1: float = 1.5       # Term frequency saturation
    b: float = 0.75       # Length normalization
    delta: float = 0.5    # BM25+ delta (smoothing for long queries)


# ── BM25 Index ────────────────────────────────────────────────────────
class Bm25Index:
    """BM25+ document index for scoring queries against a corpus.

    Thread-safe after construction (immutable index).
    """

    def __init__(
        self,
        documents: List[Dict[str, str]],
        *,
        id_key: str = "id",
        text_key: str = "text",
        config: Optional[Bm25Config] = None,
        stopwords: Optional[Set[str]] = None,
    ):
        self.config = config or Bm25Config()
        self.stopwords = stopwords or _DEFAULT_STOPWORDS
        self.id_key = id_key
        self.text_key = text_key

        # Build index
        self.doc_ids: List[str] = []
        self.doc_lengths: List[int] = []
        self.term_doc_freq: Dict[str, int] = {}  # term → N docs containing it
        self.term_postings: Dict[str, List[Tuple[int, int]]] = {}  # term → [(doc_idx, tf), ...]

        self._build(documents)

    def _build(self, documents: List[Dict[str, str]]) -> None:
        """Build the inverted index from document list."""
        if not documents:
            self.avgdl = 0.0
            self.N = 0
            return

        total_terms = 0
        for doc in documents:
            doc_id = doc.get(self.id_key, str(len(self.doc_ids)))
            text = doc.get(self.text_key, "")
            tokens = _tokenize(text, self.stopwords)
            doc_idx = len(self.doc_ids)
            self.doc_ids.append(doc_id)
            self.doc_lengths.append(len(tokens))
            total_terms += len(tokens)

            # Count term frequency in this document
            tf_map: Dict[str, int] = {}
            for t in tokens:
                tf_map[t] = tf_map.get(t, 0) + 1

            for term, tf in tf_map.items():
                self.term_doc_freq[term] = self.term_doc_freq.get(term, 0) + 1
                self.term_postings.setdefault(term, []).append((doc_idx, tf))

        self.N = len(self.doc_ids)
        self.avgdl = total_terms / max(self.N, 1)

    def query(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """Score all documents against query, return top-k (id, score).

        Args:
            query: Natural language query string.
            top_k: Number of top results to return.

        Returns:
            List of (doc_id, bm25_score) sorted descending by score.
        """
        if not self.N:
            return []

        query_tokens = _tokenize(query, self.stopwords)
        if not query_tokens:
            return []

        # Deduplicate query terms and count TF in query
        qtf: Dict[str, int] = {}
        for t in query_tokens:
            qtf[t] = qtf.get(t, 0) + 1

        # Score each document
        scores: List[float] = [0.0] * self.N
        k1 = self.config.k1
        b = self.config.b
        delta = self.config.delta

        for term, q_tf in qtf.items():
            idf = self._idf(term)
            if idf == 0.0 or term not in self.term_postings:
                continue
            for doc_idx, tf in self.term_postings[term]:
                dl = self.doc_lengths[doc_idx]
                # BM25+ scoring with delta smoothing
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * (dl / self.avgdl))
                scores[doc_idx] += idf * (numerator / denominator + delta * q_tf / len(qtf))

        # Sort and return top_k
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: -x[1])

        results = [(self.doc_ids[idx], round(score, 4))
                   for idx, score in indexed[:top_k]
                   if score > 0]

        # Log scoring stats
        if results:
            top_score = results[0][1]
            logger.debug("BM25: query=%r top=%s score=%.3f hits=%d",
                         query[:50], results[0][0], top_score, len(results))
        return results

    def _idf(self, term: str) -> float:
        """BM25 inverse document frequency (smooth)."""
        n = self.term_doc_freq.get(term, 0)
        if n == 0:
            return 0.0
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    @property
    def stats(self) -> Dict[str, object]:
        """Index statistics for observability."""
        return {
            "documents": self.N,
            "unique_terms": len(self.term_doc_freq),
            "avgdl": round(self.avgdl, 2) if self.N else 0.0,
        }


# ── Convenience builder ───────────────────────────────────────────────
def build_index(
    documents: List[Dict[str, str]],
    *,
    id_key: str = "id",
    text_key: str = "text",
    k1: float = 1.5,
    b: float = 0.75,
    stopwords: Optional[Set[str]] = None,
) -> Bm25Index:
    """Build a BM25 index from a list of document dicts.

    Args:
        documents: List of dicts with id_key and text_key fields.
        id_key: Key for document identifier in each dict.
        text_key: Key for document text in each dict.
        k1: BM25 term frequency saturation parameter.
        b: BM25 length normalization parameter.
        stopwords: Set of stopwords (default: FR+EN).

    Returns:
        Bm25Index ready for querying.
    """
    config = Bm25Config(k1=k1, b=b)
    return Bm25Index(
        documents,
        id_key=id_key,
        text_key=text_key,
        config=config,
        stopwords=stopwords,
    )


# ── Fragment Registry Adapter ─────────────────────────────────────────
def build_fragment_index(fragments: list, text_key: str = "description") -> Bm25Index:
    """Build a BM25 index from FragmentRegistry fragments.

    Each fragment's name, description, and tags are concatenated as the
    searchable text.

    Args:
        fragments: List of Fragment objects (from fragment_registry).
        text_key: Ignored; fragments have structured fields.

    Returns:
        Bm25Index over fragment descriptions + names + tags.
    """
    docs = []
    for f in fragments:
        combined = f"{f.name} {f.description} {' '.join(f.tags)}"
        docs.append({"id": f.name, "text": combined})
    return build_index(docs, id_key="id", text_key="text")


__all__ = [
    "Bm25Index", "Bm25Config", "build_index", "build_fragment_index",
]