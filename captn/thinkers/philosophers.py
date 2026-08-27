"""Corpus thinker for philosophers.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class PhilosophersThinker(CorpusThinker):
    corpus_file = "philosophers.txt"
    domain = "philosophy"
