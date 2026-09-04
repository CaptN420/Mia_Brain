"""Corpus thinker for scientists.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class ScientistsThinker(CorpusThinker):
    corpus_file = "scientists.txt"
    domain = "science"
