"""Observation Hunt V3 — live 15m, first three 5m bars.

Closed 15m = map. Live 15m 5ms = trigger.
Do not wait for the live 15m to close.

5m #1 close through prior 15m high/low = impulse FIRE.
5m #2 / #3 = continuation if #1 only wicked through.
SL 1.5 ATR / TP 2.5 ATR on closed-15m ATR.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..contract import LONG, SHORT, NEUTRAL, STATE_LONG, STATE_SHORT, STATE_WAIT
from ..indicators import arrays, atr as atr14

HUNT_VERSION_V3 = "OBSERVATION_HUNT_M5_V3"
SL_ATR = 1.5
TP_ATR = 2.5
MIN_BREAK_ATR = 0.15  # ignore tick-throughs


def _wait(why: str, extra: Optional[Dict] = None) -> Dict:
    out = {
        "action": "WAIT",
        "state": STATE_WAIT,
        "direction": NEUTRAL,
        "entry_readiness": False,
        "why_state": [why],
        "blocking_reasons": [why],
        "brain_version": HUNT_VERSION_V3,
        "hunt": {"armed": False, "slot": 0, "m5_path": "wait"},
    }
    if extra:
        out.update(extra)
    return out


def _atr15(closed_15m: List[dict]) -> float:
    if len(closed_15m) < 16:
        return 0.0
    aa = arrays(closed_15m)
    return float(atr14(aa["high"], aa["low"], aa["close"], 14) or 0.0)


def _fire(side: str, level: float, atr_v: float, path: str, slot: int, why: List[str]) -> Dict:
    if side == LONG:
        sl, tp, state = level - SL_ATR * atr_v, level + TP_ATR * atr_v, STATE_LONG
    else:
        sl, tp, state = level + SL_ATR * atr_v, level - TP_ATR * atr_v, STATE_SHORT
    return {
        "action": "FIRE",
        "state": state,
        "direction": side,
        "entry_readiness": True,
        "entry": float(level),
        "stop": float(sl),
        "target": float(tp),
        "atr_15m": float(atr_v),
        "size": "FULL",
        "why_state": why,
        "blocking_reasons": [],
        "brain_version": HUNT_VERSION_V3,
        "event": "IMPULSE_15M",
        "hunt": {
            "armed": True,
            "level": float(level),
            "side": side,
            "slot": slot,
            "m5_path": path,
            "late": False,
        },
    }


def evaluate_hunt_v3(
    closed_15m: List[dict],
    live_5ms: List[dict],
    candles_4h: Optional[List[dict]] = None,
) -> Dict:
    """live_5ms = closed 5m bars inside the *forming* 15m, oldest first (len 1..3)."""
    if not closed_15m or len(closed_15m) < 20:
        return _wait("not enough closed 15m for the map")
    if not live_5ms or not (1 <= len(live_5ms) <= 3):
        return _wait("need 1–3 live 5m bars inside the forming 15m")

    atr_v = _atr15(closed_15m)
    if atr_v <= 0:
        return _wait("invalid 15m ATR")

    prior = closed_15m[-1]
    map_high = float(prior["high"])
    map_low = float(prior["low"])
    slot = len(live_5ms)
    bar = live_5ms[-1]
    cl = float(bar["close"])
    form_high = max(float(b["high"]) for b in live_5ms)
    form_low = min(float(b["low"]) for b in live_5ms)

    extra = {
        "hunt": {
            "armed": False,
            "slot": slot,
            "map_high": map_high,
            "map_low": map_low,
            "m5_path": "wait",
        }
    }

    broke_up = cl > map_high and (cl - map_high) >= MIN_BREAK_ATR * atr_v
    broke_dn = cl < map_low and (map_low - cl) >= MIN_BREAK_ATR * atr_v
    wick_up = form_high > map_high
    wick_dn = form_low < map_low

    if broke_up and broke_dn:
        return _wait("live 5m broke both sides of the prior 15m", extra)

    if broke_up:
        return _fire(
            LONG, map_high, atr_v, f"impulse_{slot}", slot,
            [
                f"5m #{slot} closed through prior 15m high {map_high:.2f}",
                "live 15m pumping — do not wait for 15m close",
                "fill at broken 15m high, not a retest",
            ],
        )
    if broke_dn:
        return _fire(
            SHORT, map_low, atr_v, f"impulse_{slot}", slot,
            [
                f"5m #{slot} closed through prior 15m low {map_low:.2f}",
                "live 15m dumping — do not wait for 15m close",
                "fill at broken 15m low, not a retest",
            ],
        )

    if slot >= 2 and wick_up and cl >= map_high:
        return _fire(
            LONG, map_high, atr_v, f"hold_{slot}", slot,
            [
                f"prior 15m high wicked on an earlier 5m, 5m #{slot} held above",
                "live 15m still pumping",
            ],
        )
    if slot >= 2 and wick_dn and cl <= map_low:
        return _fire(
            SHORT, map_low, atr_v, f"hold_{slot}", slot,
            [
                f"prior 15m low wicked on an earlier 5m, 5m #{slot} held below",
                "live 15m still dumping",
            ],
        )

    if wick_up or wick_dn:
        extra["hunt"]["armed"] = True
        extra["hunt"]["m5_path"] = "armed_wick"
        return _wait(f"5m #{slot} wicked the map but did not close through — wait next 5m", extra)

    return _wait(f"5m #{slot} still inside prior 15m range", extra)
