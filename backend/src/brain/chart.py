"""Brain chart gate — price vs MarketState levels.

Agents do not set side. Consensus does not set side.
Side is allowed only when the closed price is actually at a mapped level.
"""
from typing import Any, Dict, Optional

from ..contract import LONG, SHORT, NEUTRAL

AT_LEVEL_ATR = 0.40
BREAK_BUFFER_ATR = 0.05


def _loc(market_state) -> Any:
    if market_state is None:
        return None
    return getattr(market_state, "location", None)


def _atr(market_state, atr_value: Optional[float]) -> float:
    if atr_value and atr_value > 0:
        return float(atr_value)
    if market_state is None:
        return 0.0
    vol = getattr(market_state, "volatility", None)
    if vol is None:
        return 0.0
    return float(getattr(vol, "atr", 0.0) or 0.0)


def chart_permission(price: float, market_state=None, atr_value: Optional[float] = None) -> Dict:
    empty = {
        "ok": False,
        "side": NEUTRAL,
        "level": None,
        "level_kind": None,
        "distance_atr": None,
        "support": None,
        "resistance": None,
        "detail": "no MarketState — Brain refuses to take a side from votes alone",
    }
    loc = _loc(market_state)
    atr = _atr(market_state, atr_value)
    if loc is None or atr <= 0 or price <= 0:
        return empty

    support = getattr(loc, "support", None)
    resistance = getattr(loc, "resistance", None)
    empty["support"] = support
    empty["resistance"] = resistance

    dist_s = abs(price - float(support)) / atr if support else None
    dist_r = abs(price - float(resistance)) / atr if resistance else None

    at_s = dist_s is not None and dist_s <= AT_LEVEL_ATR
    at_r = dist_r is not None and dist_r <= AT_LEVEL_ATR

    if at_s and at_r:
        if dist_s < dist_r:
            at_r = False
        elif dist_r < dist_s:
            at_s = False
        else:
            empty["detail"] = (
                f"price {price:.1f} sits equally on support {support} and "
                f"resistance {resistance} — ambiguous, no side"
            )
            empty["distance_atr"] = dist_s
            return empty

    if at_s:
        broken = price < float(support) - BREAK_BUFFER_ATR * atr
        if broken:
            return {
                **empty,
                "level": float(support),
                "level_kind": "support",
                "distance_atr": round(dist_s, 3),
                "detail": f"at support {support} but close already through the level",
            }
        return {
            "ok": True,
            "side": LONG,
            "level": float(support),
            "level_kind": "support",
            "distance_atr": round(dist_s, 3),
            "support": support,
            "resistance": resistance,
            "detail": f"price {price:.1f} at support {support} ({dist_s:.2f} ATR)",
        }

    if at_r:
        broken = price > float(resistance) + BREAK_BUFFER_ATR * atr
        if broken:
            return {
                **empty,
                "level": float(resistance),
                "level_kind": "resistance",
                "distance_atr": round(dist_r, 3),
                "detail": f"at resistance {resistance} but close already through the level",
            }
        return {
            "ok": True,
            "side": SHORT,
            "level": float(resistance),
            "level_kind": "resistance",
            "distance_atr": round(dist_r, 3),
            "support": support,
            "resistance": resistance,
            "detail": f"price {price:.1f} at resistance {resistance} ({dist_r:.2f} ATR)",
        }

    bits = []
    if dist_s is not None:
        bits.append(f"support {support} is {dist_s:.2f} ATR away")
    if dist_r is not None:
        bits.append(f"resistance {resistance} is {dist_r:.2f} ATR away")
    empty["distance_atr"] = min([d for d in (dist_s, dist_r) if d is not None], default=None)
    empty["detail"] = (
        "not at a mapped level (" + "; ".join(bits) + f", need ≤ {AT_LEVEL_ATR} ATR)"
        if bits else "MarketState has no support/resistance to check"
    )
    return empty
