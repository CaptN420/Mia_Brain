"""Corpus thinker for immanuel_kant.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class ImmanuelKantThinker(CorpusThinker):
    corpus_file = "immanuel_kant.txt"
    domain = "philosophy"
