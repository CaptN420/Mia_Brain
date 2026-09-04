"""Corpus thinker for alan_turing.txt (deterministic, no LLM).

Alan Turing bridges computation, mathematics, and artificial
intelligence — founder of theoretical computer science.
"""
from captn.thinkers.base import CorpusThinker


class AlanTuringThinker(CorpusThinker):
    corpus_file = "alan_turing.txt"
    domain = "computation"