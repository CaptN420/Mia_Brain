"""Corpus-backed thinkers: deterministic knowledge workers over the .txt corpora.

Each thinker loads its own corpus file from captn/, parses numbered
"Name - contribution" entries into structured findings with pure regex
(no LLM), and synthesizes a compact knowledge block for the debate runtime.

Organised by specialisation:

    captn.thinkers.philosophy/   — philosophical traditions
    captn.thinkers.science/      — scientific & mathematical domains
    captn.thinkers.esoteric/     — occult / esoteric knowledge
    captn.thinkers.polymath/     — multidisciplinary genius

20 thinkers, deliberate domain overlap for diversity: multiple thinkers
cover physics (einstein, newton, curie, physicists), chemistry (chemists,
curie, alchimie), mathematics (mathematicians, pythagoras, newton, turing),
and computation (turing).
"""

from captn.thinkers.base import CorpusThinker

# ── Philosophy (9) ───────────────────────────────────────────────────
from captn.thinkers.philosophy.aristotle import AristotleThinker
from captn.thinkers.philosophy.confucius import ConfuciusThinker
from captn.thinkers.philosophy.friedrich_nietzsche import FriedrichNietzscheThinker
from captn.thinkers.philosophy.immanuel_kant import ImmanuelKantThinker
from captn.thinkers.philosophy.john_locke import JohnLockeThinker
from captn.thinkers.philosophy.philosophers import PhilosophersThinker
from captn.thinkers.philosophy.plato import PlatoThinker
from captn.thinkers.philosophy.rene_descartes import ReneDescartesThinker
from captn.thinkers.philosophy.socrates import SocratesThinker

# ── Science (8) ──────────────────────────────────────────────────────
from captn.thinkers.science.albert_einstein import AlbertEinsteinThinker
from captn.thinkers.science.chemists import ChemistsThinker
from captn.thinkers.science.isaac_newton import IsaacNewtonThinker
from captn.thinkers.science.marie_curie import MarieCurieThinker
from captn.thinkers.science.mathematicians import MathematiciansThinker
from captn.thinkers.science.physicists import PhysicistsThinker
from captn.thinkers.science.pythagoras import PythagorasThinker
from captn.thinkers.science.scientists import ScientistsThinker

# ── Esoteric (1) ─────────────────────────────────────────────────────
from captn.thinkers.esoteric.alchimie import AlchimieThinker

# ── Polymath (2) ─────────────────────────────────────────────────────
from captn.thinkers.polymath.alan_turing import AlanTuringThinker
from captn.thinkers.polymath.leonardo_da_vinci import LeonardoDaVinciThinker

# ── Registry: name -> class ──────────────────────────────────────────
CORPUS_THINKERS = {
    # Science & domain groups
    "alchimie": AlchimieThinker,
    "chemists": ChemistsThinker,
    "mathematicians": MathematiciansThinker,
    "philosophers": PhilosophersThinker,
    "physicists": PhysicistsThinker,
    "scientists": ScientistsThinker,
    # Individual thinkers
    "albert_einstein": AlbertEinsteinThinker,
    "alan_turing": AlanTuringThinker,
    "aristotle": AristotleThinker,
    "confucius": ConfuciusThinker,
    "friedrich_nietzsche": FriedrichNietzscheThinker,
    "immanuel_kant": ImmanuelKantThinker,
    "isaac_newton": IsaacNewtonThinker,
    "john_locke": JohnLockeThinker,
    "leonardo_da_vinci": LeonardoDaVinciThinker,
    "marie_curie": MarieCurieThinker,
    "plato": PlatoThinker,
    "pythagoras": PythagorasThinker,
    "rene_descartes": ReneDescartesThinker,
    "socrates": SocratesThinker,
}


def load_all_thinkers() -> dict:
    """Instantiate every corpus thinker; skip files missing on disk.

    Returns a dict of {name: instance} for all thinkers whose corpus
    file exists and was parsed successfully.
    """
    out = {}
    for name, cls in CORPUS_THINKERS.items():
        try:
            t = cls()
            if t.available:
                out[name] = t
        except Exception:
            continue  # never crash on a single thinker failure
    return out


def load_thinkers_by_domain(domain: str) -> dict:
    """Load only thinkers matching a given domain (partial match)."""
    all_t = load_all_thinkers()
    domain_lower = domain.lower()
    return {
        name: t for name, t in all_t.items()
        if domain_lower in t.domain.lower()
    }


def get_opposing_domain(domain: str) -> str:
    """Return a deliberately contrasting domain for diversity."""
    OPPOSITES = {
        "philosophy": "physics_mathematics",
        "physics": "philosophy",
        "chemistry": "philosophy",
        "mathematics": "alchemy",
        "alchemy": "physics_mathematics",
        "computation": "philosophy",
        "physics_mathematics": "philosophy",
        "physics_chemistry": "philosophy",
        "mathematics_philosophy": "physics",
    }
    return OPPOSITES.get(domain, "philosophy")


__all__ = [
    "CorpusThinker",
    "AlchimieThinker",
    "ChemistsThinker",
    "MathematiciansThinker",
    "PhilosophersThinker",
    "PhysicistsThinker",
    "ScientistsThinker",
    "PythagorasThinker",
    "MarieCurieThinker",
    "IsaacNewtonThinker",
    "AlanTuringThinker",
    "CORPUS_THINKERS",
    "load_all_thinkers",
    "load_thinkers_by_domain",
    "get_opposing_domain",
]