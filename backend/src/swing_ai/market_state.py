"""Multi-timeframe market snapshot for the AI.  Pure function of closed candles at `now` plus the live quote.

Causality rules enforced here:
  * only candles with ts + tf_seconds <= now are used (the forming bar never enters);
  * swings / BOS / CHoCH use confirmed pivots only (a pivot needs `lr` closed bars to its right);
  * every statistic is computed from those candles alone - nothing about what happens next.
"""
import statistics
import time
from typing import Any, Dict, List, Optional

from . import primitives as P
from .source import TF_SEC, closed_only

LR = {"4h": 3, "1h": 4, "15m": 5, "5m": 3, "1m": 3}
READ = {"4h": 220, "1h": 420, "15m": 320, "5m": 130, "1m": 100}
TAIL = {"4h": 30, "1h": 48, "15m": 64, "5m": 24, "1m": 20}
PRIMARY = ("4h", "1h", "15m")


def _r(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(float(x), n)


def _rows(c: List[dict], n: int) -> List[list]:
    return [[int(x["ts"]), _r(x["open"], 1), _r(x["high"], 1), _r(x["low"], 1), _r(x["close"], 1), _r(x["volume"], 1)]
            for x in c[-n:]]


def _pctile(vals: List[float], v: float) -> float:
    return round(100.0 * sum(1 for x in vals if x <= v) / len(vals), 1) if vals else 50.0


def _tf_block(tf: str, c: List[dict], price: float, atr1h: Optional[float], full: bool) -> Optional[Dict[str, Any]]:
    if len(c) < 60:
        return None
    atrs = P.atr_series(c, 14)
    a = atrs[-1]
    if not a:
        return None
    cl = [float(x["close"]) for x in c]
    vo = [float(x["volume"]) for x in c]
    hi, lo, op = [float(x["high"]) for x in c], [float(x["low"]) for x in c], [float(x["open"]) for x in c]
    highs, lows = P.pivots(c, LR[tf])
    st = P.structure(c, highs, lows)
    e20, e50, e9 = P.ema(cl, 20)[-1], P.ema(cl, 50)[-1], P.ema(cl, 9)[-1]
    last_rng = hi[-1] - lo[-1]
    valid_atrs = [x for x in atrs[-200:] if x]
    block: Dict[str, Any] = {
        "last_closed_ts": int(c[-1]["ts"]),
        "candles": _rows(c, TAIL[tf]),
        "atr": _r(a, 1), "atr_pct": _r(100 * a / cl[-1], 3),
        "trend": st["trend"], "running_direction": st["running"],
        "structure_events": [{**e, "level": _r(e["level"], 1)} for e in st["events"][-3:]],
        "swing_highs": [{"ts": p["ts"], "price": _r(p["price"], 1), "confirmed_ts": p["confirm_ts"]} for p in highs[-4:]],
        "swing_lows": [{"ts": p["ts"], "price": _r(p["price"], 1), "confirmed_ts": p["confirm_ts"]} for p in lows[-4:]],
    }
    prev20 = vo[-21:-1]
    block["volume"] = {"last": _r(vo[-1], 1), "ratio_vs_20": _r(vo[-1] / (sum(prev20) / 20), 2) if prev20 and sum(prev20) else None,
                       "trend_5_vs_20": _r((sum(vo[-5:]) / 5) / (sum(vo[-25:-5]) / 20), 2) if sum(vo[-25:-5]) else None}
    block["volatility"] = {"atr_ratio_vs_median100": _r(a / statistics.median([x for x in atrs[-100:] if x]), 2),
                           "atr_percentile_200": _pctile(valid_atrs, a),
                           "range_expansion_5_vs_20": _r((sum(hi[i] - lo[i] for i in range(-5, 0)) / 5) /
                                                        (sum(hi[i] - lo[i] for i in range(-25, -5)) / 20), 2)}
    block["momentum"] = {"rsi14": _r(P.rsi(cl), 1),
                         "ret3_atr": _r((cl[-1] - cl[-4]) / a, 2), "ret6_atr": _r((cl[-1] - cl[-7]) / a, 2),
                         "ret12_atr": _r((cl[-1] - cl[-13]) / a, 2),
                         "last_body_atr": _r((cl[-1] - op[-1]) / a, 2), "last_range_atr": _r(last_rng / a, 2)}
    lo96, hi96 = min(lo[-96:]), max(hi[-96:])
    block["extension"] = {"dist_ema20_atr": _r((price - e20) / a, 2), "dist_ema50_atr": _r((price - e50) / a, 2),
                          "pos_in_96_range": _r((price - lo96) / (hi96 - lo96), 3) if hi96 > lo96 else None,
                          "dist_96_high_atr": _r((hi96 - price) / a, 2), "dist_96_low_atr": _r((price - lo96) / a, 2)}
    if not full:                                          # 5m / 1m: entry-refinement facts only
        block["entry_refinement"] = {
            "close_vs_ema9_atr": _r((cl[-1] - e9) / a, 2),
            "reclaimed_ema9_up": bool(cl[-2] < P.ema(cl[:-1], 9)[-1] and cl[-1] > e9),
            "reclaimed_ema9_down": bool(cl[-2] > P.ema(cl[:-1], 9)[-1] and cl[-1] < e9),
            "last_upper_wick_atr": _r((hi[-1] - max(op[-1], cl[-1])) / a, 2),
            "last_lower_wick_atr": _r((min(op[-1], cl[-1]) - lo[-1]) / a, 2)}
        for k in ("volume", "volatility", "extension"):
            block.pop(k, None)
        return block
    tol = 0.5 * a
    lv = P.cluster_levels(highs, lows, tol)
    ref = atr1h or a
    above = sorted([v for v in lv if v["price"] > price], key=lambda v: v["price"])[:3]
    below = sorted([v for v in lv if v["price"] <= price], key=lambda v: -v["price"])[:3]
    fmt = lambda v: {"price": _r(v["price"], 1), "touches": v["touches"], "last_touch_ts": v["last_ts"],
                     "dist_atr_own_tf": _r(abs(v["price"] - price) / a, 2), "dist_atr_1h": _r(abs(v["price"] - price) / ref, 2)}
    block["levels"] = {"resistance_above": [fmt(v) for v in above], "support_below": [fmt(v) for v in below]}
    if tf == "15m":
        block["liquidity_sweeps"] = P.sweeps(c, 24, atrs)[-3:]
        rej = []
        for i in range(len(c) - 12, len(c)):
            ai = atrs[i] or 0
            if ai and (hi[i] - max(op[i], cl[i])) > 1.0 * ai:
                rej.append({"type": "UPPER_WICK_REJECTION", "ts": int(c[i]["ts"]), "wick_atr": _r((hi[i] - max(op[i], cl[i])) / ai, 2)})
            if ai and (min(op[i], cl[i]) - lo[i]) > 1.0 * ai:
                rej.append({"type": "LOWER_WICK_REJECTION", "ts": int(c[i]["ts"]), "wick_atr": _r((min(op[i], cl[i]) - lo[i]) / ai, 2)})
        block["rejections"] = rej[-3:]
    return block


def build_snapshot(source, now: float, live_price: Optional[float] = None, ticker: Optional[Dict] = None,
                   position: Optional[Dict] = None, symbol: str = "BTC_USDT") -> Optional[Dict[str, Any]]:
    """Snapshot of everything knowable at `now`.  Returns None when there is not enough closed history."""
    raw = {tf: closed_only(source.closed(tf, READ[tf], now), tf, now) for tf in READ}
    if any(len(raw[tf]) < 60 for tf in PRIMARY) or len(raw["1m"]) < 30 or len(raw["5m"]) < 30:
        return None
    price = float(live_price) if live_price else float(raw["1m"][-1]["close"])
    atr1h = P.atr_series(raw["1h"], 14)[-1]
    tfs: Dict[str, Any] = {}
    for tf in READ:
        b = _tf_block(tf, raw[tf], price, atr1h, tf in PRIMARY)
        if b is None:
            return None
        tfs[tf] = b
    trends = {tf: tfs[tf]["trend"] for tf in PRIMARY}
    agree_up = sum(1 for v in trends.values() if v == "UP")
    agree_dn = sum(1 for v in trends.values() if v == "DOWN")
    a1h = tfs["1h"]["atr"]
    r_above = [lv for tf in ("4h", "1h") for lv in tfs[tf]["levels"]["resistance_above"]]
    s_below = [lv for tf in ("4h", "1h") for lv in tfs[tf]["levels"]["support_below"]]
    h1 = raw["1h"]
    cl1h = [float(x["close"]) for x in h1]
    atrs1h = P.atr_series(h1, 14)
    spans = [(max(float(x["high"]) for x in h1[i - 24:i]) - min(float(x["low"]) for x in h1[i - 24:i])) / atrs1h[i - 1]
             for i in range(max(25, len(h1) - 300), len(h1)) if atrs1h[i - 1]]
    mom_flip = (tfs["15m"]["momentum"]["ret3_atr"] or 0) * (tfs["15m"]["momentum"]["ret12_atr"] or 0) < 0
    derived = {
        "trend_alignment": {"4h": trends["4h"], "1h": trends["1h"], "15m": trends["15m"],
                            "label": "ALIGNED_UP" if agree_up == 3 else "ALIGNED_DOWN" if agree_dn == 3 else
                            "MIXED" if agree_up or agree_dn else "NO_TREND",
                            "lower_tf_against_4h": bool(trends["4h"] in ("UP", "DOWN") and
                                                        any(trends[t] not in (trends["4h"], "MIXED") for t in ("1h", "15m")))},
        "volatility_regime_1h": ("LOW" if tfs["1h"]["volatility"]["atr_percentile_200"] < 25 else
                                 "HIGH" if tfs["1h"]["volatility"]["atr_percentile_200"] > 75 else "NORMAL"),
        "price_extended": {"1h_above_ema20_gt_2atr": bool((tfs["1h"]["extension"]["dist_ema20_atr"] or 0) > 2),
                           "1h_below_ema20_gt_2atr": bool((tfs["1h"]["extension"]["dist_ema20_atr"] or 0) < -2),
                           "4h_pos_in_96_range": tfs["4h"]["extension"]["pos_in_96_range"]},
        "room_in_1h_atr": {"to_nearest_resistance": min([lv["dist_atr_1h"] for lv in r_above], default=None),
                           "to_nearest_support": min([lv["dist_atr_1h"] for lv in s_below], default=None)},
        "momentum_15m_flipped_vs_12bar": bool(mom_flip),
        "recent_expansion_1h": tfs["1h"]["volatility"]["range_expansion_5_vs_20"],
        "context_stats": {"median_24bar_range_1h_atr": _r(statistics.median(spans), 2) if spans else None,
                          "note": "computed only from the closed 1h bars above"},
    }
    q = ticker or {}
    bid, ask = q.get("bid"), q.get("ask")
    market = {"price": _r(price, 1), "bid": bid, "ask": ask,
              "spread_pct": _r(100 * (ask - bid) / price, 4) if bid and ask and price else None,
              "ticker_age_seconds": q.get("age_seconds"), "volume24": q.get("volume24")}
    return {"symbol": symbol, "as_of": int(now), "market": market, "timeframes": tfs, "derived": derived,
            "position": position}
