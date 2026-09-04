"""Corpus thinker for pythagoras.txt (deterministic, no LLM).

Pythagoras bridges mathematics and philosophy — the first to prove
geometric theorems and argue the universe is fundamentally mathematical.
"""
from captn.thinkers.base import CorpusThinker


class PythagorasThinker(CorpusThinker):
    corpus_file = "pythagoras.txt"
    domain = "mathematics_philosophy"