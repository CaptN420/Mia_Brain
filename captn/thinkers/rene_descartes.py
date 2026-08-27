"""Corpus thinker for rene_descartes.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class ReneDescartesThinker(CorpusThinker):
    corpus_file = "rene_descartes.txt"
    domain = "philosophy"
