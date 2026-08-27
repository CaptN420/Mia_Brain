"""Corpus thinker for mathematicians.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class MathematiciansThinker(CorpusThinker):
    corpus_file = "mathematicians.txt"
    domain = "mathematics"
