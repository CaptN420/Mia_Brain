"""Corpus thinker for leonardo_da_vinci.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class LeonardoDaVinciThinker(CorpusThinker):
    corpus_file = "leonardo_da_vinci.txt"
    domain = "polymath"
