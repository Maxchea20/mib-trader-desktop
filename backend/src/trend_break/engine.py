"""Trend Break engine — the single setup and the single FIRE authority.

  1D + 4H trendline bias      -> master direction (context only)
  1H closed-candle line break -> the ONLY setup
  15M / 5M                    -> break-candle quality (graded, not a setup)
  CHoCH / BOS                 -> confidence gauge only
  1M                          -> pullback, then reclaim/continuation = FIRE
  SL = 1.5 ATR, TP = 3.0 ATR, both from the actual FIRE entry price

`evaluate()` is a pure function of the closed candles it is given (plus an
optional live price for the entry fill).  It carries no hidden state: the
setup lifecycle is replayed causally from the latest 1H break on every call,
so the same call works live and on any historical prefix.  Because the
replay finds the first valid 1M trigger after the break, a setup can fire
only once; later calls report DONE.  There is no re-arm.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

from . import trendline as tl
from .gauges import break_quality, structure_gauge
from .trendline import LONG, SHORT

VERSION = "TREND_BREAK_V1"
TF_SEC = {"1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300, "1m": 60}

# setup_state values
IDLE = "IDLE"
MASTER_DIRECTION = "MASTER_DIRECTION"
TREND_BREAK_1H = "TREND_BREAK_1H"
BREAK_15M = "BREAK_15M_CONFIRMATION"
BREAK_5M = "BREAK_5M_CONFIRMATION"
WAIT_PULLBACK = "WAIT_PULLBACK"
ENTRY_1M = "1M_ENTRY_OPPORTUNITY"
FIRE = "FIRE"
DONE = "DONE"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"


@dataclass
class TrendBreakConfig:
    setup_tf: str = "1h"                    # "1h" (1D+4H master) or "15m" (1D+4H+1H master)
    length: int = 14
    slope_mult: float = 1.0
    atr_period: int = 14
    atr_tf: str = "15m"
    sl_atr: float = 1.5
    tp_atr: float = 3.0
    require_master_alignment: bool = True   # 1D and 4H must both agree with the 1H break
    setup_max_age_bars: int = 8             # setup-timeframe bars a setup may stay alive
    quality_window: int = 6
    min_quality: float = 0.20               # floor only; quality is otherwise graded
    min_confidence: float = 0.30
    expansion_min_atr: float = 0.20         # extension beyond the broken line (ATR units)
    pullback_min_atr: float = 0.50          # retrace from the extreme = room to breathe
    bounce_max_bars: int = 6                # trigger within N 1M bars of the pullback extreme
    invalidation_atr: float = 1.0           # close this far back through the line cancels
    fresh_bars: int = 2                     # trigger must be within N closed 1M bars
    max_entry_slip_atr: float = 0.5         # live price vs trigger close (no chasing)


@dataclass
class TrendBreakDecision:
    fire: bool = False
    setup_state: str = IDLE
    reason: str = ""
    direction: Optional[str] = None
    entry: Optional[float] = None
    atr: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    trend_break_timeframe: str = "1h"
    trend_break_ts: Optional[int] = None
    break_level: Optional[float] = None
    invalid_level: Optional[float] = None
    master_1d_direction: str = "NEUTRAL"
    master_4h_direction: str = "NEUTRAL"
    master_1h_direction: str = "NEUTRAL"    # only used when setup_tf == "15m"
    setup_tf: str = "1h"
    master_alignment: str = "NONE"
    trendline_1h_direction: str = "NEUTRAL"
    trendline_1h_value: Optional[float] = None
    break_detected: bool = False
    break_direction: Optional[str] = None
    break_1h: Optional[Dict[str, Any]] = None
    m15_quality: Optional[Dict[str, Any]] = None
    m5_quality: Optional[Dict[str, Any]] = None
    structure_confidence: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None
    pullback_detected: bool = False
    m1_entry_trigger: Optional[Dict[str, Any]] = None
    invalidated: bool = False
    version: str = VERSION
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entry_price"] = self.entry
        return d

    def to_signal(self) -> Dict[str, Any]:
        """Shape consumed by the existing autotrader execution pipe."""
        return {
            "action": "FIRE" if self.fire else "WAIT",
            "direction": self.direction,
            "entry": self.entry, "stop": self.sl, "target": self.tp,
            "atr_15m": self.atr,
            "timing": "TREND_BREAK", "timing_state": self.setup_state,
            "event": "TREND_BREAK_1H", "engine": "TREND_BREAK",
            "thesis_ts": self.trend_break_ts, "thesis_level": self.break_level,
            "thesis_invalid": self.invalid_level, "thesis_id": self.trend_break_ts,
            "why_state": [self.reason], "blocking_reasons": [] if self.fire else [self.reason],
            "brain_version": self.version, "slot": None, "size": "FULL", "ok": True,
            "trend_break": self.to_dict(),
        }


# --- helpers ---------------------------------------------------------------

_TL_CACHE: Dict[tuple, tl.TrendlineResult] = {}


def _lines(rows: Sequence[dict], tf: str, cfg: TrendBreakConfig) -> tl.TrendlineResult:
    key = (tf, len(rows), rows[0]["ts"] if rows else 0, rows[-1]["ts"] if rows else 0,
           cfg.length, cfg.slope_mult, cfg.atr_period)
    hit = _TL_CACHE.get(key)
    if hit is None:
        if len(_TL_CACHE) > 64:
            _TL_CACHE.clear()
        hit = _TL_CACHE[key] = tl.compute(rows, cfg.length, cfg.slope_mult, cfg.atr_period)
    return hit


def _closed(rows: Optional[Sequence[dict]], tf: str, asof: Optional[float]) -> List[dict]:
    rows = list(rows or [])
    if asof is None:
        return rows
    step = TF_SEC[tf]
    return [c for c in rows if int(c["ts"]) + step <= asof]


def _atr_at(rows: Sequence[dict], asof: float, tf: str, cfg: TrendBreakConfig) -> float:
    sub = _closed(rows, tf, asof)
    a = tl.atr_series(sub, cfg.atr_period)
    return float(a[-1]) if a and a[-1] else 0.0


def _gauges(d: str, b: tl.Break, data: Dict[str, List[dict]], asof: float,
            cfg: TrendBreakConfig, master_score: float) -> Dict[str, Any]:
    setup_sec = TF_SEC[cfg.setup_tf]
    c15 = _closed(data["15m"], "15m", asof)
    c5 = _closed(data["5m"], "5m", asof)
    lv = lambda ts: b.line.value_at_ts(ts, setup_sec)
    q15 = break_quality(c15, d, lv, b.ts, 900, cfg.quality_window, cfg.atr_period)
    q5 = break_quality(c5, d, lv, b.ts, 300, cfg.quality_window, cfg.atr_period)
    st = structure_gauge(c15, c5, d, b.ts)
    conf = 0.30 * master_score + 0.25 * q15["score"] + 0.25 * q5["score"] + 0.20 * st["score"]
    return {"q15": q15, "q5": q5, "structure": st, "confidence": round(conf, 4),
            "ok": q15["score"] >= cfg.min_quality and q5["score"] >= cfg.min_quality
                  and conf >= cfg.min_confidence}


# --- engine ----------------------------------------------------------------

def evaluate(candles: Dict[str, Sequence[dict]], live_price: Optional[float] = None,
             config: Optional[TrendBreakConfig] = None,
             now_ts: Optional[float] = None) -> TrendBreakDecision:
    """candles: {"1d","4h","1h","15m","5m","1m"} -> oldest-first rows with
    ts (open time, seconds), open/high/low/close.  Pass now_ts to drop any
    candle not yet closed at that instant; without it the caller guarantees
    every candle is closed."""
    cfg = config or TrendBreakConfig()
    d = TrendBreakDecision()
    if cfg.setup_tf not in ("1h", "15m"):
        raise ValueError("setup_tf must be '1h' or '15m'")
    stf = cfg.setup_tf
    ssec = TF_SEC[stf]
    d.setup_tf = d.trend_break_timeframe = stf
    masters = ("1d", "4h") if stf == "1h" else ("1d", "4h", "1h")
    data = {tf: _closed(candles.get(tf), tf, now_ts) for tf in TF_SEC}
    if len(data[stf]) < 2 * cfg.length + 2 or not data["1m"] or not data["15m"]:
        d.reason = "insufficient closed candle history"
        return d
    t_now = float(data["1m"][-1]["ts"]) + 60.0

    # 1D / 4H master direction (context only)
    for tf, attr in (("1d", "master_1d_direction"), ("4h", "master_4h_direction"),
                     ("1h", "master_1h_direction")):
        if tf not in masters:
            continue
        rows = data[tf]
        if len(rows) >= 2 * cfg.length + 2:
            setattr(d, attr, tl.direction(_lines(rows, tf, cfg), rows))
        else:
            d.notes.append(f"{tf}: not enough candles for trendline ({len(rows)})")

    res1h = _lines(data[stf], stf, cfg)      # the setup-timeframe trendline
    b = res1h.latest_break()
    d.trendline_1h_direction = tl.direction(res1h, data[stf])
    if b is None:
        d.setup_state = MASTER_DIRECTION
        d.reason = f"no {stf.upper()} trendline break yet"
        return d

    side = b.direction
    s = 1.0 if side == LONG else -1.0
    break_close = b.ts + ssec
    d.direction = side
    d.break_detected = True
    d.break_direction = side
    d.trend_break_ts = b.ts
    d.break_level = b.line_value
    d.trendline_1h_value = b.line.value_at_index(res1h.last_index)
    d.break_1h = {"ts": b.ts, "close": b.close, "line_value": b.line_value,
                  "line_kind": b.line.kind, "pivot_ts": b.line.pivot_ts,
                  "pivot_price": b.line.pivot_price, "slope": b.line.slope}

    opp = SHORT if side == LONG else LONG
    d1, d4, d1h = d.master_1d_direction, d.master_4h_direction, d.master_1h_direction
    mvals = [{"1d": d1, "4h": d4, "1h": d1h}[m] for m in masters]
    if all(v == side for v in mvals):
        d.master_alignment, master_score = "ALIGNED", 1.0
    elif opp in mvals:
        d.master_alignment, master_score = "CONFLICT", 0.0
    else:
        d.master_alignment, master_score = "PARTIAL", 0.6

    if t_now - break_close > cfg.setup_max_age_bars * ssec:
        d.setup_state, d.invalidated = EXPIRED, True
        d.reason = f"{stf.upper()} {side} break older than {cfg.setup_max_age_bars} bars — expired"
        return d
    if d.master_alignment == "CONFLICT" or (cfg.require_master_alignment and d.master_alignment != "ALIGNED"):
        d.setup_state = MASTER_DIRECTION
        d.reason = (f"{stf.upper()} {side} break not backed by master direction "
                    f"1D={d1} / 4H={d4}" + (f" / 1H={d1h}" if "1h" in masters else ""))
        return d

    A = _atr_at(data[cfg.atr_tf], break_close, cfg.atr_tf, cfg)
    if A <= 0:
        d.reason = f"{cfg.atr_tf} ATR unavailable at break"
        return d
    level_s = s * b.line_value
    inv_s = level_s - cfg.invalidation_atr * A
    d.invalid_level = s * inv_s
    d.setup_state = TREND_BREAK_1H

    # causal 1M replay from the break candle onward
    m1 = [c for c in data["1m"] if int(c["ts"]) >= b.ts]
    if not m1:
        d.reason = "no 1M candles since break"
        return d
    fav = (lambda c: float(c["high"])) if side == LONG else (lambda c: -float(c["low"]))
    adv = (lambda c: float(c["low"])) if side == LONG else (lambda c: -float(c["high"]))
    ext = pb = None
    pb_idx = -1
    trigger = None
    armed = False
    rejected = 0
    for k, c in enumerate(m1):
        f = fav(c)
        a = adv(c)
        if ext is None or f > ext:
            ext, pb, pb_idx = f, f, k
        elif a < pb:
            pb, pb_idx = a, k
        if int(c["ts"]) < break_close:
            continue                              # break candle itself: track only
        close_s = s * float(c["close"])
        if close_s < inv_s:
            d.setup_state, d.invalidated = CANCELLED, True
            d.reason = (f"{side} setup cancelled: 1M close {float(c['close']):.2f} back through "
                        f"breakout structure ({s * inv_s:.2f})")
            d.pullback_detected = armed
            return d
        armed = (ext - level_s) >= cfg.expansion_min_atr * A and (ext - pb) >= cfg.pullback_min_atr * A
        if not armed or k == 0:
            continue
        prev = m1[k - 1]
        bullish = s * (float(c["close"]) - float(c["open"])) > 0
        if bullish and close_s > fav(prev) and (k - pb_idx) <= cfg.bounce_max_bars:
            g = _gauges(side, b, data, float(c["ts"]) + 60.0, cfg, master_score)
            if g["ok"]:
                trigger = (c, g)
                break
            rejected += 1
            d.notes.append(f"1M trigger at {int(c['ts'])} rejected by quality/confidence gate")
    d.pullback_detected = armed

    if trigger is None:
        g = _gauges(side, b, data, t_now, cfg, master_score)
        d.m15_quality, d.m5_quality = g["q15"], g["q5"]
        d.structure_confidence, d.confidence = g["structure"], g["confidence"]
        if g["q15"]["score"] < cfg.min_quality:
            d.setup_state, d.reason = BREAK_15M, "15M break quality below floor"
        elif g["q5"]["score"] < cfg.min_quality:
            d.setup_state, d.reason = BREAK_5M, "5M break quality below floor"
        elif armed:
            d.setup_state, d.reason = ENTRY_1M, "pullback has room — waiting for 1M reclaim/continuation"
        else:
            d.setup_state, d.reason = WAIT_PULLBACK, "trend break qualified — letting price breathe (no chasing)"
        return d

    c, g = trigger
    d.m15_quality, d.m5_quality = g["q15"], g["q5"]
    d.structure_confidence, d.confidence = g["structure"], g["confidence"]
    t_close = float(c["ts"]) + 60.0
    d.m1_entry_trigger = {"ts": int(c["ts"]), "close": float(c["close"]), "kind": "RECLAIM_CONTINUATION"}
    if t_now - t_close > cfg.fresh_bars * 60:
        d.setup_state = DONE
        d.reason = f"setup already triggered at {int(c['ts'])} — finished, no re-arm"
        return d
    entry = float(live_price) if live_price else float(c["close"])
    atr_fire = _atr_at(data[cfg.atr_tf], t_close, cfg.atr_tf, cfg)
    if atr_fire <= 0:
        d.setup_state, d.reason = ENTRY_1M, "ATR unavailable at fire"
        return d
    if abs(entry - float(c["close"])) > cfg.max_entry_slip_atr * atr_fire:
        d.setup_state = ENTRY_1M
        d.reason = "live price ran away from the 1M trigger — not chasing"
        return d
    d.fire = True
    d.setup_state = FIRE
    d.entry, d.atr = entry, atr_fire
    d.sl = entry - s * cfg.sl_atr * atr_fire
    d.tp = entry + s * cfg.tp_atr * atr_fire
    d.reason = (f"{side} Trend Break: {stf.upper()} break → pullback → 1M reclaim; "
                f"SL {cfg.sl_atr}×ATR, TP {cfg.tp_atr}×ATR from entry")
    return d
