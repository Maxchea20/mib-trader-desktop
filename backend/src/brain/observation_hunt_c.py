"""Hunt C — locked book from the 60d / monthly tests.

FIRE only when:
  - 5m #3 of the live 15m closes through the prior 15m high/low (impulse_3 / hold_3),
    or V2 clean/ugly tap on 5m #1/#2 after that 15m closed
  - that 15m is a V2 arm with CHoCH or first BOS after CHoCH (no BREAKOUT_DETECTED)
  - 4h weather allows the side (no 1% sleeve)
  - fill at the 15m level, not the 5m close
  - SL 1.5 ATR / TP 2.5 ATR

V2 evaluate_hunt is unchanged. This module is the C door.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..contract import LONG, SHORT, NEUTRAL, STATE_WAIT
from .observation_hunt import evaluate_hunt, SL_ATR, TP_ATR
from .observation_hunt_v3 import evaluate_hunt_v3
from .weather import classify, side_allowed

HUNT_VERSION_C = "OBSERVATION_HUNT_M5_C"


def parent_open(ts5: int) -> int:
    return int(ts5) - (int(ts5) % 900)


def slot_of(ts5: int) -> int:
    return int(((int(ts5) % 900) // 300) + 1)


def _wait(why: str, extra: Optional[Dict] = None) -> Dict:
    out = {
        "action": "WAIT",
        "state": STATE_WAIT,
        "direction": NEUTRAL,
        "entry_readiness": False,
        "why_state": [why],
        "blocking_reasons": [why],
        "brain_version": HUNT_VERSION_C,
        "size": "FULL",
        "hunt": {"armed": False},
    }
    if extra:
        out.update(extra)
    return out


def structure_ok(v2: Dict) -> bool:
    hunt = v2.get("hunt") or {}
    ev = (hunt.get("event") or v2.get("event") or "").upper()
    bq = v2.get("bos_quality") or {}
    first = bool(hunt.get("first_bos_after_choch") or bq.get("first_bos_after_choch"))
    if ev in ("CHOCH", "CHoCH"):
        return True
    return ev == "BOS" and first


def _stamp(fire: Dict, path: str, event: Optional[str]) -> Dict:
    out = dict(fire)
    out["brain_version"] = HUNT_VERSION_C
    out["size"] = "FULL"
    out["v3a_path"] = path
    out["event"] = event or out.get("event")
    why = list(out.get("why_state") or [])
    out["why_state"] = ["Hunt C"] + why
    hunt = dict(out.get("hunt") or {})
    hunt["m5_path"] = path
    out["hunt"] = hunt
    # fill at the 15m level, never the 5m close
    level = float(hunt.get("level") or out.get("entry") or 0)
    atr_v = float(out.get("atr_15m") or 0)
    side = out.get("direction")
    if level and atr_v and side in (LONG, SHORT):
        out["entry"] = level
        if side == LONG:
            out["stop"] = level - SL_ATR * atr_v
            out["target"] = level + TP_ATR * atr_v
        else:
            out["stop"] = level + SL_ATR * atr_v
            out["target"] = level - TP_ATR * atr_v
    return out


def evaluate_hunt_c(
    candles_15m: List[dict],
    candle_5m: dict,
    live_5ms: Optional[List[dict]] = None,
    candles_4h: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
) -> Dict:
    """One closed 5m tick.

    candles_15m[-1] must be the last *closed* 15m as of this 5m.
    On 5m #3 that is the 15m that just completed.
    live_5ms = 1..3 fives inside that parent 15m (oldest first).
    """
    if not candles_15m or not candle_5m:
        return _wait("missing candles")
    live = live_5ms or [candle_5m]
    slot = len(live) if live else slot_of(candle_5m["ts"])
    wx = classify(candles_4h or [], candles_1h) if candles_4h else None

    v2 = evaluate_hunt(
        candles_15m,
        {"ts": int(candle_5m["ts"]), "open": float(candle_5m["close"]),
         "high": float(candle_5m["close"]), "low": float(candle_5m["close"]),
         "close": float(candle_5m["close"]), "volume": 0},
        candles_4h=candles_4h,
        candles_5m=None,
    )
    hunt = v2.get("hunt") or {}
    armed = bool(hunt.get("armed") or v2.get("action") == "FIRE")
    side = hunt.get("side") or v2.get("direction")
    event = hunt.get("event") or v2.get("event")

    if not armed:
        return _wait("C: 15m is not a V2 arm", extra={"v2": {"why": v2.get("why_state")}})
    if not structure_ok(v2):
        return _wait(
            f"C: skip {event or 'non-structure'} — only CHoCH / first BOS",
            extra={"event": event},
        )
    if side not in (LONG, SHORT):
        return _wait("C: no hunt side")
    if wx and not side_allowed(wx.get("flag"), side):
        return _wait(f"C: 4h weather {wx.get('flag')} blocks {side}")

    if slot == 3:
        prior = candles_15m[:-1]
        v3 = evaluate_hunt_v3(prior, live, candles_4h=candles_4h)
        if v3.get("action") == "FIRE" and v3.get("direction") == side:
            path = (v3.get("hunt") or {}).get("m5_path") or "impulse_3"
            return _stamp(v3, path, event)
        return _wait("C: 5m #3 did not close through prior 15m range")

    # 5m #1 / #2 of the *next* 15m: V2 clean/ugly tap of the armed 15m
    v2_fill = evaluate_hunt(
        candles_15m, candle_5m, candles_4h=candles_4h, candles_5m=candles_5m,
    )
    if v2_fill.get("action") == "FIRE" and v2_fill.get("direction") == side:
        path = "v2_" + str((v2_fill.get("hunt") or {}).get("m5_path") or "clean")
        return _stamp(v2_fill, path, event)
    return _wait(
        "C: no V2 tap on this 5m",
        extra={"hunt": v2_fill.get("hunt") or {"armed": True}},
    )
