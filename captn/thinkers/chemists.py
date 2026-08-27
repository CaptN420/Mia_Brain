"""Corpus thinker for chemists.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class ChemistsThinker(CorpusThinker):
    corpus_file = "chemists.txt"
    domain = "chemistry"
