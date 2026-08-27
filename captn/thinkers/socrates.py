"""Corpus thinker for socrates.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class SocratesThinker(CorpusThinker):
    corpus_file = "socrates.txt"
    domain = "philosophy"
