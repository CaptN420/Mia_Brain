#!/usr/bin/env python3
"""No-repetition worker — MIA wrapper autour de la version CaptN.

Délégue à captn.workers.quality.no_repetition_worker.
Évite la duplication de code (BUG 1 résolu).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent.parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from captn.workers.quality.no_repetition_worker import (
    NoRepetitionWorker as _CaptnNoRepetitionWorker,
    structure_signature,
    symbol_fingerprint,
)


class NoRepetitionWorker(_CaptnNoRepetitionWorker):
    """MIA wrapper — identique à la version CaptN.
    Toute modification doit être faite dans captn/workers/quality/no_repetition_worker.py
    """
    pass