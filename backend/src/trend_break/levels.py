"""Research-only: horizontal support / resistance study.

A level is a cluster of confirmed swing pivots (highs -> resistance, lows -> support) that lie within
`tol_atr` x ATR of each other; it qualifies from its `min_touches`-th pivot on.  Two event types,
both decided on the closing bar:
  REJECT - price touches a qualifying level from the open side and the bar closes back on that side
           as a rejection candle (support -> LONG, resistance -> SHORT).
  RETEST - a level closed through (by more than the tolerance) is touched again from the far side
           within `retest_window` bars and holds (broken resistance -> LONG, broken support -> SHORT).
Measured against the same-direction baseline (drift control).  No orders, no engine."""
from typing import Dict, List, Sequence

from . import trendline as tl
from .structure import baseline
from .sweep import _race, FEE_RT

LONG, SHORT = 1, -1


def level_events(candles: Sequence[dict], lr: int = 5, tol_atr: float = 0.3, min_touches: int = 2,
                 retest_window: int = 24, cooldown: int = 12, max_levels: int = 30) -> List[Dict]:
    n = len(candles)
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    op = [float(c["open"]) for c in candles]
    piv: Dict[int, List[tuple]] = {}
    for i in range(lr, n - lr):
        if all(hi[i] > hi[i - k] and hi[i] >= hi[i + k] for k in range(1, lr + 1)):
            piv.setdefault(i + lr, []).append(("R", hi[i]))
        if all(lo[i] < lo[i - k] and lo[i] <= lo[i + k] for k in range(1, lr + 1)):
            piv.setdefault(i + lr, []).append(("S", lo[i]))
    levels: List[Dict] = []
    out: List[Dict] = []
    for t in range(n):
        a = atrs[t]
        if not a:
            continue
        tol = tol_atr * a
        for kind, price in piv.get(t, ()):                       # pivot confirmed on this bar
            for lv in levels:
                if lv["kind"] == kind and lv["state"] == "live" and abs(lv["price"] - price) <= tol:
                    lv["price"] = (lv["price"] * lv["touches"] + price) / (lv["touches"] + 1)
                    lv["touches"] += 1
                    break
            else:
                levels.append({"kind": kind, "price": price, "touches": 1, "state": "live",
                               "cool": -10 ** 9, "broke_i": None})
        if len(levels) > max_levels:
            levels = sorted(levels, key=lambda v: v.get("last", 0))[-max_levels:]
        if t < 1:
            continue
        for lv in levels:
            lv["last"] = t
            p = lv["price"]
            if lv["state"] == "live" and lv["touches"] >= min_touches:
                if lv["kind"] == "S":
                    touched = lo[t] <= p + tol
                    if touched and cl[t] > p and cl[t] > op[t] and cl[t - 1] > p and t - lv["cool"] >= cooldown:
                        out.append({"i": t, "ts": int(candles[t]["ts"]), "s": LONG, "type": "REJECT", "touches": lv["touches"]})
                        lv["cool"] = t
                    elif cl[t] < p - tol:
                        lv["state"], lv["broke_i"] = "broken", t
                else:
                    touched = hi[t] >= p - tol
                    if touched and cl[t] < p and cl[t] < op[t] and cl[t - 1] < p and t - lv["cool"] >= cooldown:
                        out.append({"i": t, "ts": int(candles[t]["ts"]), "s": SHORT, "type": "REJECT", "touches": lv["touches"]})
                        lv["cool"] = t
                    elif cl[t] > p + tol:
                        lv["state"], lv["broke_i"] = "broken", t
            elif lv["state"] == "broken":
                age = t - lv["broke_i"]
                if age > retest_window:
                    lv["state"] = "dead"
                elif lv["kind"] == "R":                           # broken resistance acts as support
                    if cl[t] < p - tol:
                        lv["state"] = "dead"
                    elif age >= 1 and lo[t] <= p + tol and cl[t] > p and cl[t] > op[t]:
                        out.append({"i": t, "ts": int(candles[t]["ts"]), "s": LONG, "type": "RETEST", "touches": lv["touches"]})
                        lv["state"] = "dead"
                else:                                             # broken support acts as resistance
                    if cl[t] > p + tol:
                        lv["state"] = "dead"
                    elif age >= 1 and hi[t] >= p - tol and cl[t] < p and cl[t] < op[t]:
                        out.append({"i": t, "ts": int(candles[t]["ts"]), "s": SHORT, "type": "RETEST", "touches": lv["touches"]})
                        lv["state"] = "dead"
        levels = [v for v in levels if v["state"] != "dead"]
    return out


def tag_levels(candles: Sequence[dict], horizons: Sequence[int] = (1, 4, 16, 64), sl_atr: float = 1.5,
               tp_atr: float = 3.0, max_bars: int = 200, **kw) -> Dict:
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    n = len(cl)
    events, fee_atr = [], []
    for e in level_events(candles, **kw):
        i, s, a = e["i"], e["s"], atrs[e["i"]]
        if i < 110 or not a:
            continue
        ev = {**e, "atr": a, "tp_first": _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars)}
        for h in horizons:
            ev[f"fwd{h}"] = s * (cl[i + h] - cl[i]) / a if i + h < n else None
        events.append(ev)
        fee_atr.append(FEE_RT * cl[i] / a)
    return {"events": events, "base": baseline(candles, horizons, sl_atr, tp_atr, max_bars),
            "fee_atr": fee_atr, "lookback": kw.get("lr", 5), "horizons": list(horizons)}
