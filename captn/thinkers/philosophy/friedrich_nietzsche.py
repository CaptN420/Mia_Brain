"""Corpus thinker for friedrich_nietzsche.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class FriedrichNietzscheThinker(CorpusThinker):
    corpus_file = "friedrich_nietzsche.txt"
    domain = "philosophy"
