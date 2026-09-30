"""Research-only: 'trade with the trend on a CHoCH / BOS' study.

Events on a lower timeframe (15m / 1h), all decided on the closing bar:
  BOS   - a close through the last confirmed swing in the direction of the running swing trend
  CHoCH - a close through the last confirmed swing AGAINST the running swing trend
(same definitions as gauges.structure_events, computed in linear time).
'With the trend' = the event direction equals the 4H swing-structure direction at that moment.
Measured from the event bar's close against the same-direction baseline (drift control).
No orders, no engine."""
import bisect
from typing import Dict, List, Sequence

from . import trendline as tl
from .sweep import _race, FEE_RT

LONG, SHORT = tl.LONG, tl.SHORT


def swing_events(candles: Sequence[dict], lr: int) -> List[Dict]:
    n = len(candles)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    conf_h: Dict[int, tuple] = {}
    conf_l: Dict[int, tuple] = {}
    for i in range(lr, n - lr):
        if all(hi[i] > hi[i - k] and hi[i] >= hi[i + k] for k in range(1, lr + 1)):
            conf_h[i + lr] = (i, hi[i])
        if all(lo[i] < lo[i - k] and lo[i] <= lo[i + k] for k in range(1, lr + 1)):
            conf_l[i + lr] = (i, lo[i])
    cur_h = cur_l = None
    used_h = used_l = -1
    trend = None
    out: List[Dict] = []
    for t in range(n):
        if t in conf_h and conf_h[t][0] > used_h:
            cur_h = conf_h[t]
        if t in conf_l and conf_l[t][0] > used_l:
            cur_l = conf_l[t]
        if cur_h and cl[t] > cur_h[1]:
            out.append({"i": t, "ts": int(candles[t]["ts"]), "type": "BOS" if trend == LONG else "CHoCH",
                        "direction": LONG, "level": cur_h[1]})
            used_h, cur_h, trend = cur_h[0], None, LONG
        elif cur_l and cl[t] < cur_l[1]:
            out.append({"i": t, "ts": int(candles[t]["ts"]), "type": "BOS" if trend == SHORT else "CHoCH",
                        "direction": SHORT, "level": cur_l[1]})
            used_l, cur_l, trend = cur_l[0], None, SHORT
    return out


def baseline(candles, horizons, sl_atr, tp_atr, max_bars, start=110):
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    n = len(cl)
    base = {s: {"tp_n": 0, "tp_w": 0, **{f"sum{h}": 0.0 for h in horizons}, **{f"cnt{h}": 0 for h in horizons}}
            for s in (1, -1)}
    for i in range(start, n):
        a = atrs[i]
        if not a:
            continue
        for s in (1, -1):
            b = base[s]
            for h in horizons:
                if i + h < n:
                    b[f"sum{h}"] += s * (cl[i + h] - cl[i]) / a
                    b[f"cnt{h}"] += 1
            r = _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars)
            if r is not None:
                b["tp_n"] += 1
                b["tp_w"] += r
    return base


def tag_structure(candles: Sequence[dict], candles_4h: Sequence[dict], lr: int,
                  horizons: Sequence[int] = (1, 4, 16, 64), sl_atr: float = 1.5, tp_atr: float = 3.0,
                  max_bars: int = 200, master_length: int = 8, tf_sec: int = 900) -> Dict:
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    n = len(cl)
    hist: List[tuple] = []
    if candles_4h:
        tl.structure_direction(candles_4h, master_length, history=hist)
    hist_t = [h[0] + 14400 for h in hist]                  # a 4H state is known once that 4H bar has closed
    events = []
    fee_atr = []
    for e in swing_events(candles, lr):
        i = e["i"]
        a = atrs[i]
        if i < 110 or not a:
            continue
        s = 1 if e["direction"] == LONG else -1
        close_t = e["ts"] + tf_sec
        k = bisect.bisect_right(hist_t, close_t) - 1
        d4 = hist[k][1] if k >= 0 else "NEUTRAL"
        ev = {"i": i, "ts": e["ts"], "s": s, "type": e["type"], "atr": a,
              "trend": "WITH" if d4 == e["direction"] else ("AGAINST" if d4 != "NEUTRAL" else "NEUTRAL"),
              "tp_first": _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars)}
        for h in horizons:
            ev[f"fwd{h}"] = s * (cl[i + h] - cl[i]) / a if i + h < n else None
        events.append(ev)
        fee_atr.append(FEE_RT * cl[i] / a)
    base = baseline(candles, horizons, sl_atr, tp_atr, max_bars)
    return {"events": events, "base": base, "fee_atr": fee_atr, "lookback": lr, "horizons": list(horizons)}


def group(res: Dict, pred) -> Dict:
    return {**res, "events": [e for e in res["events"] if pred(e)]}
