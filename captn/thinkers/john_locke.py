"""Corpus thinker for john_locke.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class JohnLockeThinker(CorpusThinker):
    corpus_file = "john_locke.txt"
    domain = "philosophy"
