"""Small, self-contained, causal market primitives (no imports from any other MIB engine)."""
from typing import Dict, List, Optional, Sequence, Tuple


def atr_series(c: Sequence[dict], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(c)
    a = None
    for i in range(len(c)):
        h, l = float(c[i]["high"]), float(c[i]["low"])
        tr = h - l if i == 0 else max(h - l, abs(h - float(c[i - 1]["close"])), abs(l - float(c[i - 1]["close"])))
        if i < n:
            a = tr if a is None else a + tr
            if i == n - 1:
                a = a / n
                out[i] = a
        else:
            a = (a * (n - 1) + tr) / n
            out[i] = a
    return out


def ema(values: Sequence[float], n: int) -> List[float]:
    k = 2.0 / (n + 1)
    out, e = [], None
    for v in values:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def rsi(closes: Sequence[float], n: int = 14) -> Optional[float]:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def pivots(c: Sequence[dict], lr: int) -> Tuple[List[dict], List[dict]]:
    """Confirmed pivots: bar i is a pivot only once `lr` later bars have closed (they are in `c`)."""
    hi = [float(x["high"]) for x in c]
    lo = [float(x["low"]) for x in c]
    highs, lows = [], []
    for i in range(lr, len(c) - lr):
        if all(hi[i] > hi[i - k] and hi[i] >= hi[i + k] for k in range(1, lr + 1)):
            highs.append({"i": i, "ts": int(c[i]["ts"]), "price": hi[i], "confirm_i": i + lr,
                          "confirm_ts": int(c[i + lr]["ts"])})
        if all(lo[i] < lo[i - k] and lo[i] <= lo[i + k] for k in range(1, lr + 1)):
            lows.append({"i": i, "ts": int(c[i]["ts"]), "price": lo[i], "confirm_i": i + lr,
                         "confirm_ts": int(c[i + lr]["ts"])})
    return highs, lows


def structure(c: Sequence[dict], highs: List[dict], lows: List[dict]) -> Dict:
    """Trend label from the last two swings, plus BOS / CHoCH events (a close through the last unused
    confirmed swing; CHoCH when it goes against the running trend, BOS when with it)."""
    trend_lbl = "MIXED"
    if len(highs) >= 2 and len(lows) >= 2:
        hh, hl = highs[-1]["price"] > highs[-2]["price"], lows[-1]["price"] > lows[-2]["price"]
        lh, ll = highs[-1]["price"] < highs[-2]["price"], lows[-1]["price"] < lows[-2]["price"]
        trend_lbl = "UP" if hh and hl else "DOWN" if lh and ll else "MIXED"
    events: List[dict] = []
    run = None
    used_h = used_l = -1
    cur_h = cur_l = None
    by_h = {p["confirm_i"]: p for p in highs}
    by_l = {p["confirm_i"]: p for p in lows}
    for t in range(len(c)):
        if t in by_h and by_h[t]["i"] > used_h:
            cur_h = by_h[t]
        if t in by_l and by_l[t]["i"] > used_l:
            cur_l = by_l[t]
        cl = float(c[t]["close"])
        if cur_h is not None and cl > cur_h["price"]:
            events.append({"type": "BOS" if run == "UP" else "CHoCH", "direction": "UP", "ts": int(c[t]["ts"]),
                           "level": cur_h["price"], "swing_ts": cur_h["ts"]})
            used_h, cur_h, run = cur_h["i"], None, "UP"
        elif cur_l is not None and cl < cur_l["price"]:
            events.append({"type": "BOS" if run == "DOWN" else "CHoCH", "direction": "DOWN", "ts": int(c[t]["ts"]),
                           "level": cur_l["price"], "swing_ts": cur_l["ts"]})
            used_l, cur_l, run = cur_l["i"], None, "DOWN"
    return {"trend": trend_lbl, "running": run, "events": events}


def cluster_levels(highs: List[dict], lows: List[dict], tol: float, max_levels: int = 40) -> List[dict]:
    """Horizontal levels: swing prices within `tol` of each other merge; touches = swings in the cluster."""
    pts = sorted([(p["price"], p["ts"], "H") for p in highs[-max_levels:]] +
                 [(p["price"], p["ts"], "L") for p in lows[-max_levels:]])
    out: List[dict] = []
    for price, ts, kind in pts:
        if out and abs(price - out[-1]["price"]) <= tol:
            g = out[-1]
            g["price"] = (g["price"] * g["touches"] + price) / (g["touches"] + 1)
            g["touches"] += 1
            g["last_ts"] = max(g["last_ts"], ts)
        else:
            out.append({"price": price, "touches": 1, "last_ts": ts})
    return out


def sweeps(c: Sequence[dict], lookback: int, atr: Sequence[Optional[float]], last_n_bars: int = 40) -> List[dict]:
    """Liquidity sweeps: a bar wicks beyond the prior `lookback`-bar extreme and closes back inside."""
    out = []
    for i in range(max(lookback, len(c) - last_n_bars), len(c)):
        a = atr[i] or 0.0
        if not a:
            continue
        ph = max(float(x["high"]) for x in c[i - lookback:i])
        pl = min(float(x["low"]) for x in c[i - lookback:i])
        hi, lo, cl = float(c[i]["high"]), float(c[i]["low"]), float(c[i]["close"])
        if hi > ph and cl < ph:
            out.append({"type": "SWEEP_HIGH", "ts": int(c[i]["ts"]), "level": ph, "wick_atr": round((hi - ph) / a, 2),
                        "close_back_atr": round((ph - cl) / a, 2)})
        elif lo < pl and cl > pl:
            out.append({"type": "SWEEP_LOW", "ts": int(c[i]["ts"]), "level": pl, "wick_atr": round((pl - lo) / a, 2),
                        "close_back_atr": round((cl - pl) / a, 2)})
    return out
