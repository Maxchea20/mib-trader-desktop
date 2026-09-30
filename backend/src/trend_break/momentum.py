"""Research-only: long-horizon momentum (Donchian close-breakout) study.

Event at bar i: close[i] is above the highest close (LONG) / below the lowest close (SHORT)
of the previous `lookback` bars.  Measured from close[i]: the move in the signal direction
after `horizons` bars, minus what price does after ANY bar in that direction (drift control),
and the fee cost in ATR.  No orders, no engine."""
import math
from typing import Dict, List, Sequence

from . import trendline as tl
from .sweep import _race, decluster, FEE_RT
from .features import mean_t, _p_two_sided

GAP_BARS = 48
PRIMARY = 64


def tag_momentum(candles: Sequence[dict], lookback: int = 48, horizons: Sequence[int] = (16, 64, 128),
                 sl_atr: float = 3.0, tp_atr: float = 6.0, max_bars: int = 400) -> Dict:
    n = len(candles)
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    base = {s: {"tp_n": 0, "tp_w": 0, **{f"sum{h}": 0.0 for h in horizons}, **{f"cnt{h}": 0 for h in horizons}}
            for s in (1, -1)}
    events: List[Dict] = []
    fee_atr: List[float] = []
    for i in range(max(lookback, 110), n):
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
        top, bot = max(cl[i - lookback:i]), min(cl[i - lookback:i])
        up, dn = cl[i] > top, cl[i] < bot
        if not (up or dn):
            continue
        s = 1 if up else -1
        ev = {"ts": int(candles[i]["ts"]), "i": i, "s": s, "atr": a,
              "tp_first": _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars)}
        for h in horizons:
            ev[f"fwd{h}"] = s * (cl[i + h] - cl[i]) / a if i + h < n else None
        events.append(ev)
        fee_atr.append(FEE_RT * cl[i] / a)
    return {"events": events, "base": base, "fee_atr": fee_atr, "lookback": lookback, "horizons": list(horizons)}


def _ex(evs, base, h):
    """Per-event excess over the same-direction baseline -> (mean, t, n)."""
    xs = []
    for e in evs:
        v = e.get(f"fwd{h}")
        b = base[e["s"]]
        if v is None or not b[f"cnt{h}"]:
            continue
        xs.append(v - b[f"sum{h}"] / b[f"cnt{h}"])
    if len(xs) < 2:
        return 0.0, 0.0, len(xs)
    m, t = mean_t(xs)
    return m, t, len(xs)


def judge(res: Dict, n_variants: int = 3) -> Dict:
    ev = decluster(res["events"], GAP_BARS)
    base, hz = res["base"], res["horizons"]
    half = len(ev) // 2
    fee = sorted(res["fee_atr"])[len(res["fee_atr"]) // 2] if res["fee_atr"] else 0.0
    out = {"n_raw": len(res["events"]), "n_used": len(ev), "fee_atr": fee, "rows": {}}
    groups = {"all": ev, "LONG": [e for e in ev if e["s"] == 1], "SHORT": [e for e in ev if e["s"] == -1],
              "half1": ev[:half], "half2": ev[half:]}
    for name, g in groups.items():
        out["rows"][name] = {h: _ex(g, base, h) for h in hz}
    p_all = _p_two_sided(out["rows"]["all"][PRIMARY][1])
    ok = lambda k: out["rows"][k][PRIMARY][0] > 0
    out["primary"] = {"ex": out["rows"]["all"][PRIMARY][0], "t": out["rows"]["all"][PRIMARY][1], "p": p_all,
                      "net": out["rows"]["all"][PRIMARY][0] - fee}
    out["consistent"] = all(ok(k) for k in ("LONG", "SHORT", "half1", "half2"))
    out["pass"] = bool(p_all < 0.05 / n_variants and out["primary"]["ex"] > 0 and out["consistent"]
                       and out["primary"]["net"] > 0)
    tp = [e["tp_first"] for e in ev if e["tp_first"] is not None]
    bw = sum(base[s]["tp_w"] for s in (1, -1)) / max(1, sum(base[s]["tp_n"] for s in (1, -1)))
    out["tp"] = (sum(tp), len(tp), bw)
    return out
