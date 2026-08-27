"""Corpus thinker for aristotle.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class AristotleThinker(CorpusThinker):
    corpus_file = "aristotle.txt"
    domain = "philosophy"
