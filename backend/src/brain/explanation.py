"""A4 — Honest reconstructable Brain explanation. No trading-logic changes."""
from typing import Dict, List, Optional
from ..contract import LONG, SHORT, NEUTRAL
from . import evidence as ev

EXPLANATION_VERSION = "BRAIN_EXPLANATION_V1"
_ROLE_ORDER = ("STRUCTURE", "DRIVE", "LOCATION", "PATTERN")
_NAMES = {
    "market_structure": "Market Structure", "breakout": "Breakout",
    "fibonacci": "Fibonacci", "elliott_wave": "Elliott Wave", "volume": "Volume",
    "momentum": "Momentum", "support_resistance": "Support/Resistance",
    "trend": "Trend", "pattern": "Pattern", "fair_value_gap": "FVG",
}
_STRENGTH = ((80, "strong"), (65, "moderate"), (45, "medium"), (0, "weak"))
_PRIMARY = {
    "WAIT_INSUFFICIENT_DATA": "insufficient valid agents",
    "WAIT_CONFLICT": "conflict veto or severe opposing role-mass",
    "NEUTRAL": "consensus inside the neutral band",
    "BIAS_LONG": "setup not yet developed",
    "BIAS_SHORT": "setup not yet developed",
    "SETUP_LONG": "no valid trigger event",
    "SETUP_SHORT": "no valid trigger event",
    "WAIT_POOR_LOCATION": "location quality too weak",
    "WAIT_EXTENDED": "price already extended from trigger origin",
    "WAIT_NO_EXTENSION_REF": "extension origin unknown",
    "WAIT_HTF": "counter-trend HTF gate raised the bar above current consensus",
    "WAIT_LOW_CONFIDENCE": "directional confidence below required",
}


def _name(aid):
    return _NAMES.get(aid, aid)


def _word(score):
    for cut, w in _STRENGTH:
        if score >= cut:
            return w
    return "weak"


def _find(agents, aid):
    for r in agents:
        if r.agent == aid:
            return r
    return None
