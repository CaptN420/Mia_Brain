"""Corpus thinker for marie_curie.txt (deterministic, no LLM).

Marie Curie bridges physics and chemistry — the first to isolate
radioactive elements and establish radiochemistry as a discipline.
"""
from captn.thinkers.base import CorpusThinker


class MarieCurieThinker(CorpusThinker):
    corpus_file = "marie_curie.txt"
    domain = "physics_chemistry"