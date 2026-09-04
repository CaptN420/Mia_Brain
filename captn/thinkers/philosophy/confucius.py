"""Corpus thinker for confucius.txt (deterministic, no LLM)."""

from captn.thinkers.base import CorpusThinker


class ConfuciusThinker(CorpusThinker):
    corpus_file = "confucius.txt"
    domain = "philosophy"
