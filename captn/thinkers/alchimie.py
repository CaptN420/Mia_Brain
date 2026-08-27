"""Corpus thinker for alchimie_thinker.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class AlchimieThinker(CorpusThinker):
    corpus_file = "alchimie_thinker.txt"
    domain = "alchemy"
