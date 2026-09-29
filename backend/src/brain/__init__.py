"""Brain — legacy helpers package (NOT an analysis agent).

The live FIRE authority is src.trend_break. The old Hunt exports below are
kept lazily for historical/backtest callers only and are never imported by
the live path.
"""
from .weather import classify as classify_weather, side_allowed, WEATHER_VERSION
from .lifecycle import (
    LIFECYCLE_VERSION,
    HOLD,
    TRAIL,
    EXIT,
    reevaluate,
    position_from_fire,
    thesis_from_fire,
    size_from_risk,
)

_LEGACY = {"evaluate_hunt", "reset_hunt_state", "HUNT_VERSION", "SL_ATR", "TP_ATR",
           "evaluate_s1", "S1_VERSION"}


def __getattr__(name):
    if name in _LEGACY:
        from . import hunt_brain as _h
        return {
            "evaluate_hunt": _h.evaluate_hunt, "evaluate_s1": _h.evaluate_hunt,
            "reset_hunt_state": _h.reset_hunt_state, "HUNT_VERSION": _h.VERSION,
            "S1_VERSION": _h.VERSION, "SL_ATR": _h.SL_ATR, "TP_ATR": _h.TP_ATR,
        }[name]
    raise AttributeError(name)
