"""PHASE G — BRAIN V2 (decision interpretation layer).

Consumes the standardized outputs of the 10 agents plus the HTF regime
and produces exactly one legacy state — LONG / SHORT / WAIT / AVOID —
with a fully traceable reasoning chain. The external contract (every
field the old Brain returned, and the exact meaning of `state`) is
unchanged: autotrader.py gates real trade execution on
`brain["state"] in ("LONG", "SHORT")` via a literal string match
(confirmed by direct inspection), so that value can never change shape.
"""
from typing import List, Dict, Optional
from ..config import CONFIG_VERSION, ASSUMPTIONS
from .. import settings
from ..contract import (LONG, SHORT, NEUTRAL, STATE_LONG, STATE_SHORT,
                        STATE_WAIT, STATE_AVOID, clamp)
from .confluence import find_confluence_zones
from .conflict import detect_conflict
from .mtf import apply_gate
from . import evidence as ev
from . import scoring

EXCLUDE_FROM_EXECUTION = scoring.EXCLUDE_FROM_EXECUTION

_CONSENSUS_BANDS = [
    (60, "STRONG_LONG"), (30, "LONG"), (10, "WEAK_LONG"),
    (-10, "NEUTRAL"), (-30, "WEAK_SHORT"), (-60, "SHORT"),
]


def _consensus_band(consensus: float) -> str:
    for threshold, label in _CONSENSUS_BANDS:
        if consensus >= threshold:
            return label
    return "STRONG_SHORT"
