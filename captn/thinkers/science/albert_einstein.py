"""Corpus thinker for albert_einstein.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class AlbertEinsteinThinker(CorpusThinker):
    corpus_file = "albert_einstein.txt"
    domain = "physics"
