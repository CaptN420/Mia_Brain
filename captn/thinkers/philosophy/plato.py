"""Corpus thinker for plato.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class PlatoThinker(CorpusThinker):
    corpus_file = "plato.txt"
    domain = "philosophy"
