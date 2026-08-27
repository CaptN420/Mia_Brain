"""Corpus thinker for physicists.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class PhysicistsThinker(CorpusThinker):
    corpus_file = "physicists.txt"
    domain = "physics"
