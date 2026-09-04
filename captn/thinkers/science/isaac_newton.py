"""Corpus thinker for isaac_newton.txt (deterministic, no LLM).

Isaac Newton bridges physics and mathematics — founder of classical
mechanics, universal gravitation, and calculus.
"""
from captn.thinkers.base import CorpusThinker


class IsaacNewtonThinker(CorpusThinker):
    corpus_file = "isaac_newton.txt"
    domain = "physics_mathematics"