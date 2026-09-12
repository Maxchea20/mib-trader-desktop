"""Brain chart gate — price vs MarketState levels.

Agents do not set side. Consensus does not set side.
Side is allowed only when the closed price is actually at a mapped level.
"""
from typing import Dict, List, Optional, Tuple

from ..contract import LONG, SHORT, NEUTRAL

AT_LEVEL_ATR = 0.60
BREAK_BUFFER_ATR = 0.05


def _atr(market_state, atr_value: Optional[float]) -> float:
    if atr_value and atr_value > 0:
        return float(atr_value)
    if market_state is None:
        return 0.0
    vol = getattr(market_state, "volatility", None)
    if vol is None:
        return 0.0
    return float(getattr(vol, "atr", 0.0) or 0.0)


def _collect_levels(market_state):
    supports = []
    resistances = []
    if market_state is None:
        return supports, resistances
    loc = getattr(market_state, "location", None)
    if loc is not None:
        s = getattr(loc, "support", None)
        r = getattr(loc, "resistance", None)
        if s:
            supports.append(("location.support", float(s)))
        if r:
            resistances.append(("location.resistance", float(r)))
    st = getattr(market_state, "structure", None)
    if st is not None:
        ll = getattr(st, "last_low", None)
        pl = getattr(st, "previous_low", None)
        lh = getattr(st, "last_high", None)
        ph = getattr(st, "previous_high", None)
        if ll:
            supports.append(("structure.last_low", float(ll)))
        if pl:
            supports.append(("structure.previous_low", float(pl)))
        if lh:
            resistances.append(("structure.last_high", float(lh)))
        if ph:
            resistances.append(("structure.previous_high", float(ph)))
    return supports, resistances


def _nearest(levels, price, atr):
    if not levels:
        return None
    ranked = sorted(((abs(price - px) / atr, name, px) for name, px in levels), key=lambda x: x[0])
    dist, name, px = ranked[0]
    return dist, name, px


def chart_permission(price: float, market_state=None, atr_value: Optional[float] = None) -> Dict:
    empty = {
        "ok": False,
        "side": NEUTRAL,
        "level": None,
        "level_kind": None,
        "level_source": None,
        "distance_atr": None,
        "support": None,
        "resistance": None,
        "detail": "no MarketState — Brain refuses to take a side from votes alone",
    }
    atr = _atr(market_state, atr_value)
    if market_state is None or atr <= 0 or price <= 0:
        return empty
    supports, resistances = _collect_levels(market_state)
    near_s = _nearest(supports, price, atr)
    near_r = _nearest(resistances, price, atr)
    empty["support"] = near_s[2] if near_s else None
    empty["resistance"] = near_r[2] if near_r else None
    at_s = near_s is not None and near_s[0] <= AT_LEVEL_ATR
    at_r = near_r is not None and near_r[0] <= AT_LEVEL_ATR
    if at_s and at_r:
        if near_s[0] < near_r[0]:
            at_r = False
        elif near_r[0] < near_s[0]:
            at_s = False
        else:
            empty["detail"] = (
                f"price {price:.1f} equally near {near_s[1]} {near_s[2]} and "
                f"{near_r[1]} {near_r[2]} — ambiguous, no side"
            )
            empty["distance_atr"] = near_s[0]
            return empty
    if at_s:
        dist, src, level = near_s
        if price < level - BREAK_BUFFER_ATR * atr:
            return {
                **empty,
                "level": level, "level_kind": "support", "level_source": src,
                "distance_atr": round(dist, 3),
                "detail": f"at {src} {level} but close already through the level",
            }
        return {
            "ok": True, "side": LONG, "level": level, "level_kind": "support",
            "level_source": src, "distance_atr": round(dist, 3),
            "support": level, "resistance": empty["resistance"],
            "detail": f"price {price:.1f} at {src} {level} ({dist:.2f} ATR)",
        }
    if at_r:
        dist, src, level = near_r
        if price > level + BREAK_BUFFER_ATR * atr:
            return {
                **empty,
                "level": level, "level_kind": "resistance", "level_source": src,
                "distance_atr": round(dist, 3),
                "detail": f"at {src} {level} but close already through the level",
            }
        return {
            "ok": True, "side": SHORT, "level": level, "level_kind": "resistance",
            "level_source": src, "distance_atr": round(dist, 3),
            "support": empty["support"], "resistance": level,
            "detail": f"price {price:.1f} at {src} {level} ({dist:.2f} ATR)",
        }
    bits = []
    if near_s:
        bits.append(f"{near_s[1]} {near_s[2]} is {near_s[0]:.2f} ATR away")
    if near_r:
        bits.append(f"{near_r[1]} {near_r[2]} is {near_r[0]:.2f} ATR away")
    empty["distance_atr"] = min([x[0] for x in (near_s, near_r) if x], default=None)
    empty["detail"] = (
        "not at a mapped level (" + "; ".join(bits) + f", need ≤ {AT_LEVEL_ATR} ATR)"
        if bits else "MarketState has no support/resistance/swing to check"
    )
    return empty
