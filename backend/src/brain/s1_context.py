"""S1 15m setup stage: Trend -> Structure -> Breakout qualification -> Momentum confidence.

Runs before the S1 slot 1/2 window. Reuses existing implementations only:
  Trend     -- trend.observe (EMA20/50/100 ribbon; state STRONG_BULL .. STRONG_BEAR / NEUTRAL)
  Structure -- the 15m BOS/CHoCH event S1 already reads (structure.observe, pivot 2) and
               that observation's own sequence flag `extended_bos` (3rd+ same-direction BOS)
  Breakout  -- breakout lifecycle rules: FAILED = a close back through the broken level,
               stale after BREAKOUT_MAX_LIFETIME_BARS (both from src/breakout)
  Momentum  -- momentum.observe state (RSI/MACD/ROC/EMA-slope behaviour), used as confidence
No FVG, no Volume, no S/R.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT
from ..breakout import BREAKOUT_MAX_LIFETIME_BARS

ALIGNED, NEUTRAL_CTX, COUNTER = "ALIGNED", "NEUTRAL", "COUNTER"
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

_CACHE: Dict[str, Any] = {}


def _cached(name: str, candles: List[dict], fn):
    """Trend / momentum depend only on the closed 15m candles: compute once per 15m close."""
    if not candles:
        return None
    key = (name, len(candles), int(candles[0]["ts"]), int(candles[-1]["ts"]), float(candles[-1]["close"]))
    if _CACHE.get(name, (None,))[0] != key:
        try:
            _CACHE[name] = (key, fn(candles, "15m"))
        except Exception:
            _CACHE[name] = (key, None)
    return _CACHE[name][1]


def trend_direction(trend_obs) -> Optional[str]:
    """LONG / SHORT from the existing trend state, None when NEUTRAL or unavailable."""
    if trend_obs is None or not getattr(trend_obs, "valid", False):
        return None
    state = str(getattr(trend_obs, "state", "") or "").upper()
    if state.endswith("BULL"):
        return LONG
    if state.endswith("BEAR"):
        return SHORT
    return None


def trend_context(trend_dir: Optional[str], side: str) -> str:
    if trend_dir is None:
        return NEUTRAL_CTX
    return ALIGNED if trend_dir == side else COUNTER


def qualify_break(event, struct_obs, form: List[dict], price: Optional[float], side: str, ctx: str) -> Optional[str]:
    """None when the 15m break qualifies as an S1 setup, else the reason it does not."""
    et = (getattr(event, "event_type", "") or "").upper()
    if ctx == COUNTER and et == "BOS":
        return "counter-trend 15m BOS is not an S1 thesis"
    flags = getattr(struct_obs, "flags", None) or {}
    if et == "BOS" and flags.get("extended_bos"):
        return "15m BOS is extended (3rd+ same-direction BOS)"
    ev_ts = getattr(event, "timestamp", None)
    level = getattr(event, "reference_price", None)
    if level is None:
        level = getattr(event, "price", None)
    if ev_ts is None or level is None or not form:
        return "15m break has no timestamp/level"
    ev_ts, level = int(ev_ts), float(level)
    age = (int(form[-1]["ts"]) - ev_ts) // 900
    if age > BREAKOUT_MAX_LIFETIME_BARS:
        return f"15m break is stale ({age} bars > {BREAKOUT_MAX_LIFETIME_BARS})"
    closes = [float(c["close"]) for c in form if int(c["ts"]) > ev_ts]
    if price is not None:
        closes.append(float(price))
    back = any(c < level for c in closes) if side == LONG else any(c > level for c in closes)
    if back:
        return "15m breakout FAILED (closed back through the broken level)"
    return None


def momentum_confidence(mom_obs, side: str) -> str:
    """HIGH = momentum pushing the setup side, LOW = pushing against it or the side is exhausting,
    MEDIUM = anything else (neutral, decelerating, retracing, unavailable)."""
    state = str(getattr(mom_obs, "state", "") or "").upper() if mom_obs is not None else ""
    me, other = ("LONG", "SHORT") if side == LONG else ("SHORT", "LONG")
    if state in (f"{me}_ACCELERATING", f"STRONG_{me}", f"BUILDING_{me}", f"REVERSAL_{me}"):
        return HIGH
    if state in (f"{other}_ACCELERATING", f"STRONG_{other}", f"BUILDING_{other}", f"REVERSAL_{other}",
                 f"{me}_EXHAUSTING"):
        return LOW
    return MEDIUM


ALIGNED_ONLY = False  # research switch (forensic --aligned-only): S1 only with the 15m trend


def setup_allowed(ctx: str, conf: str) -> bool:
    """Momentum is confidence: with the trend it only has to not be against the setup;
    without trend support (neutral or counter-trend CHoCH) it must actively confirm."""
    if ALIGNED_ONLY and ctx != ALIGNED:
        return False
    if ctx == ALIGNED:
        return conf in (HIGH, MEDIUM)
    return conf == HIGH


def not_ready(reason: str) -> Dict[str, Any]:
    return {"trend": None, "trend_dir": None, "context": None, "event": None, "qualified": False,
            "reason": reason, "momentum": None, "confidence": None, "ok": False}


def evaluate_setup(candles_15m_closed: List[dict], form: List[dict], event, struct_obs,
                   side: str, price: Optional[float], aux: Optional[dict] = None) -> Dict[str, Any]:
    """form = the 15m candles the break was read from (S1: the closed ones); price None = closes only."""
    aux = aux or {}
    trend_obs = aux.get("trend")
    if trend_obs is None:
        from ..trend.observe import observe as obs_trend
        trend_obs = _cached("trend", candles_15m_closed, obs_trend)
    mom_obs = aux.get("mom")
    if mom_obs is None:
        from ..momentum.observe import observe as obs_mom
        mom_obs = _cached("mom", candles_15m_closed, obs_mom)
    t_dir = trend_direction(trend_obs)
    ctx = trend_context(t_dir, side)
    out = {
        "trend": getattr(trend_obs, "state", None), "trend_dir": t_dir, "context": ctx,
        "event": getattr(event, "event_type", None), "qualified": False, "reason": None,
        "momentum": getattr(mom_obs, "state", None), "confidence": None, "ok": False,
    }
    reason = qualify_break(event, struct_obs, form, price, side, ctx)
    if reason:
        out["reason"] = reason
        return out
    out["qualified"] = True
    conf = momentum_confidence(mom_obs, side)
    out["confidence"] = conf
    if not setup_allowed(ctx, conf):
        out["reason"] = f"{ctx.lower()} trend context needs stronger momentum (confidence {conf})"
        return out
    out["ok"] = True
    return out
