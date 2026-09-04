#!/usr/bin/env python3
"""Diversification worker — MIA wrapper autour de la version CaptN.

Délégue à captn.workers.transformation.diversifier_worker.
Évite la duplication de code (BUG 1 résolu).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Set

# Importer la version CaptN (la vraie implémentation)
_HERE = Path(__file__).resolve().parent.parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from captn.workers.transformation.diversifier_worker import (
    DiversifierWorker as _CaptnDiversifierWorker,
    structure_signature as _structure_signature,
    symbol_fingerprint as _symbol_fingerprint,
    collect_signatures as _collect_signatures,
    _norm as _norm,
    _extract_vars as _extract_vars,
    MUTATION_REGISTRY,
)

# Ré-exporter les fonctions utiles
structure_signature = _structure_signature
symbol_fingerprint = _symbol_fingerprint
collect_signatures = _collect_signatures
_norm_fn = _norm
_extract_vars_fn = _extract_vars


class DiversifierWorker(_CaptnDiversifierWorker):
    """MIA wrapper — identique à la version CaptN, mais avec le nom MIA.
    Toute modification doit être faite dans captn/workers/transformation/diversifier_worker.py
    """
    pass