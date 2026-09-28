"""Compatibility facade for the single Hunt brain.

The live decision authority is hunt_brain.evaluate_hunt.
This module remains only so existing imports/replay callers do not break.
"""
from .hunt_brain import evaluate_hunt, reset_hunt_state, VERSION, SL_ATR, TP_ATR

S1_VERSION = VERSION


def reset_s1_state() -> None:
    reset_hunt_state()


def evaluate_s1(candles_15m, candle_5m, candles_5m=None, candles_1m=None, aux=None):
    return evaluate_hunt(
        candles_15m=candles_15m,
        candle_5m=candle_5m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
        aux=aux,
    )
