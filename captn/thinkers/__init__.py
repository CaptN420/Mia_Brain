"""Corpus-backed thinkers: deterministic knowledge workers over the .txt corpora.

Each thinker loads its own corpus file from captn/, parses numbered
"Name - contribution" entries into structured findings with pure regex
(no LLM), and synthesizes a compact knowledge block for the debate runtime.
"""

from captn.thinkers.base import CorpusThinker
from captn.thinkers.alchimie import AlchimieThinker
from captn.thinkers.chemists import ChemistsThinker
from captn.thinkers.mathematicians import MathematiciansThinker
from captn.thinkers.philosophers import PhilosophersThinker
from captn.thinkers.physicists import PhysicistsThinker
from captn.thinkers.scientists import ScientistsThinker

# The 10 greatest thinkers of the world
from captn.thinkers.leonardo_da_vinci import LeonardoDaVinciThinker
from captn.thinkers.socrates import SocratesThinker
from captn.thinkers.plato import PlatoThinker
from captn.thinkers.aristotle import AristotleThinker
from captn.thinkers.confucius import ConfuciusThinker
from captn.thinkers.rene_descartes import ReneDescartesThinker
from captn.thinkers.immanuel_kant import ImmanuelKantThinker
from captn.thinkers.john_locke import JohnLockeThinker
from captn.thinkers.friedrich_nietzsche import FriedrichNietzscheThinker
from captn.thinkers.albert_einstein import AlbertEinsteinThinker

# Registry: name -> class (used by orchestrator / mia.captn_bridge)
CORPUS_THINKERS = {
    "alchimie": AlchimieThinker,
    "chemists": ChemistsThinker,
    "mathematicians": MathematiciansThinker,
    "philosophers": PhilosophersThinker,
    "physicists": PhysicistsThinker,
    "scientists": ScientistsThinker,
    # Greatest thinkers of the world
    "leonardo_da_vinci": LeonardoDaVinciThinker,
    "socrates": SocratesThinker,
    "plato": PlatoThinker,
    "aristotle": AristotleThinker,
    "confucius": ConfuciusThinker,
    "rene_descartes": ReneDescartesThinker,
    "immanuel_kant": ImmanuelKantThinker,
    "john_locke": JohnLockeThinker,
    "friedrich_nietzsche": FriedrichNietzscheThinker,
    "albert_einstein": AlbertEinsteinThinker,
}


def load_all_thinkers() -> dict:
    """Instantiate every corpus thinker; skip files missing on disk."""
    out = {}
    for name, cls in CORPUS_THINKERS.items():
        t = cls()
        if t.available:
            out[name] = t
    return out


__all__ = [
    "CorpusThinker",
    "AlchimieThinker",
    "ChemistsThinker",
    "MathematiciansThinker",
    "PhilosophersThinker",
    "PhysicistsThinker",
    "ScientistsThinker",
    "CORPUS_THINKERS",
    "load_all_thinkers",
]
