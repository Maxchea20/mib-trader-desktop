"""Research-only: liquidity sweep-and-reject study.

Event at bar i (all causal, decided on bar i's close):
  * high sweep : high[i] > highest high of the previous `lookback` bars, but close[i] is back below it  -> SHORT
  * low sweep  : low[i]  < lowest  low  of the previous `lookback` bars, but close[i] is back above it  -> LONG
What price does next is measured from close[i], compared with what price does after ANY
bar in the same direction (the drift control), so BTC's own trend cannot pass for edge.
No orders, no engine - the live path is untouched."""
import math
from typing import Dict, List, Sequence

from . import trendline as tl
from .features import mean_t, _p_two_sided

FEE_RT = 0.0009          # 0.045% taker each side, round trip, as a fraction of price


def _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars):
    n = len(cl)
    for k in range(i + 1, min(n, i + 1 + max_bars)):
        adv = (cl[i] - lo[k]) if s > 0 else (hi[k] - cl[i])
        fav = (hi[k] - cl[i]) if s > 0 else (cl[i] - lo[k])
        if adv >= sl_atr * a:
            return 0
        if fav >= tp_atr * a:
            return 1
    return None


def tag_sweeps(candles: Sequence[dict], lookback: int = 48, horizons: Sequence[int] = (1, 4, 16, 64),
               sl_atr: float = 1.5, tp_atr: float = 3.0, max_bars: int = 200) -> Dict:
    n = len(candles)
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    vo = [float(c.get("volume") or 0.0) for c in candles]
    start = max(lookback, 110)
    events: List[Dict] = []
    base = {s: {"n": 0, "tp_n": 0, "tp_w": 0, **{f"sum{h}": 0.0 for h in horizons}, **{f"cnt{h}": 0 for h in horizons}}
            for s in (1, -1)}
    fee_atr = []
    for i in range(start, n):
        a = atrs[i]
        if not a:
            continue
        for s in (1, -1):                                              # drift control: every bar, both directions
            b = base[s]
            b["n"] += 1
            for h in horizons:
                if i + h < n:
                    b[f"sum{h}"] += s * (cl[i + h] - cl[i]) / a
                    b[f"cnt{h}"] += 1
            r = _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars)
            if r is not None:
                b["tp_n"] += 1
                b["tp_w"] += r
        ph = max(hi[i - lookback:i])
        pl = min(lo[i - lookback:i])
        up = hi[i] > ph and cl[i] < ph
        dn = lo[i] < pl and cl[i] > pl
        if up == dn:
            continue
        s = -1 if up else 1
        level = ph if up else pl
        wick = ((hi[i] - ph) if up else (pl - lo[i])) / a
        vavg = sum(vo[i - 20:i]) / 20.0
        ev = {"ts": int(candles[i]["ts"]), "i": i, "s": s, "side": "SHORT" if up else "LONG", "atr": a,
              "wick_atr": wick, "reject_atr": s * (cl[i] - level) / a,
              "vol_ratio": vo[i] / vavg if vavg > 0 else None,
              "tp_first": _race(hi, lo, cl, i, s, a, sl_atr, tp_atr, max_bars)}
        for h in horizons:
            ev[f"fwd{h}"] = s * (cl[i + h] - cl[i]) / a if i + h < n else None
        events.append(ev)
        fee_atr.append(FEE_RT * cl[i] / a)
    return {"events": events, "base": base, "fee_atr": fee_atr, "lookback": lookback, "horizons": list(horizons)}


def _z_prop(w1, n1, p0):
    if n1 == 0:
        return 0.0, 1.0
    se = math.sqrt(max(p0 * (1 - p0), 1e-12) / n1)
    z = (w1 / n1 - p0) / se
    return z, _p_two_sided(z)


DECLUSTER_BARS = 24      # events closer than this share most of their price path; keep one


def decluster(events: List[Dict], gap: int = DECLUSTER_BARS) -> List[Dict]:
    out, last = [], -10 ** 9
    for e in events:
        if e["i"] - last >= gap:
            out.append(e)
            last = e["i"]
    return out


def judge(res: Dict, sides=(1, -1)) -> Dict:
    """Excess over the same-direction baseline, split by side and by time half.
    Events are de-clustered first so overlapping outcomes are not counted as independent."""
    ev, base, hz = decluster(res["events"]), res["base"], res["horizons"]

    def excess(evs):
        out = {}
        for s in sides:
            e = [x for x in evs if x["s"] == s]
            b = base[s]
            tp = [x["tp_first"] for x in e if x["tp_first"] is not None]
            p0 = b["tp_w"] / b["tp_n"] if b["tp_n"] else 1 / 3
            z, p = _z_prop(sum(tp), len(tp), p0)
            row = {"n": len(e), "tp_rate": (sum(tp) / len(tp)) if tp else 0.0, "tp_base": p0, "tp_z": z, "tp_p": p}
            for h in hz:
                v = [x[f"fwd{h}"] for x in e if x.get(f"fwd{h}") is not None]
                b0 = b[f"sum{h}"] / b[f"cnt{h}"] if b[f"cnt{h}"] else 0.0
                m, _ = mean_t(v) if v else (0.0, 0.0)
                sd = math.sqrt(sum((a - m) ** 2 for a in v) / (len(v) - 1)) if len(v) > 1 else 0.0
                t = (m - b0) / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
                row[f"ex{h}"] = (m - b0, t, len(v))
            out[s] = row
        return out

    half = len(ev) // 2
    full = excess(ev)
    h1, h2 = excess(ev[:half]), excess(ev[half:])
    tot_w = sum(x["tp_first"] for x in ev if x["tp_first"] is not None)
    tot_n = sum(1 for x in ev if x["tp_first"] is not None)
    p0 = (sum(base[s]["tp_w"] for s in sides) / max(1, sum(base[s]["tp_n"] for s in sides)))
    z, p = _z_prop(tot_w, tot_n, p0)
    same_half = (h1[-1]["tp_rate"] - h1[-1]["tp_base"]) * (h2[-1]["tp_rate"] - h2[-1]["tp_base"]) > 0 if -1 in sides else True
    edge_h = [(x["tp_rate"] - x["tp_base"]) for x in (h1[1], h2[1], h1[-1], h2[-1])] if set(sides) == {1, -1} else []
    consistent = bool(edge_h) and all(d > 0 for d in edge_h)      # both sides, both halves beat baseline
    return {"n_raw": len(res["events"]), "n_used": len(ev), "full": full, "h1": h1, "h2": h2, "tp": (tot_w, tot_n, p0, z, p), "consistent": consistent,
            "pass": bool(p < 0.05 / 3 and consistent)}                # 3 look-back variants -> Bonferroni
