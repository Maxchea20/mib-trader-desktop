"""Research-only: tag every setup-timeframe trendline break with causal
features (volume, squeeze, momentum, space, structure ...) and measure what
price does afterwards, with no entry/exit logic at all.  Does not touch the
engine.  A feature at break bar i uses only candles <= i."""
import math
from statistics import median
from typing import Dict, List, Sequence

from . import trendline as tl

FEATURES = ["vol_ratio", "squeeze", "range_compress", "runup", "break_atr", "body_ratio",
            "body_atr", "room_atr", "line_age"]


def _ranks(v: Sequence[float]) -> List[float]:
    order = sorted(range(len(v)), key=lambda k: v[k])
    r = [0.0] * len(v)
    k = 0
    while k < len(order):
        j = k
        while j + 1 < len(order) and v[order[j + 1]] == v[order[k]]:
            j += 1
        for m in range(k, j + 1):
            r[order[m]] = (k + j) / 2.0
        k = j + 1
    return r


def _corr(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def _p_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def spearman(x: Sequence[float], y: Sequence[float]):
    """(rho, two-sided p) — normal approximation of the t statistic (n is large)."""
    n = len(x)
    if n < 10:
        return 0.0, 1.0
    rho = _corr(_ranks(x), _ranks(y))
    if abs(rho) >= 1.0:
        return rho, 0.0
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    return rho, _p_two_sided(t)


def mean_t(v: Sequence[float]):
    n = len(v)
    if n < 2:
        return (v[0] if v else 0.0), 0.0
    m = sum(v) / n
    sd = math.sqrt(sum((a - m) ** 2 for a in v) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def tag_breaks(candles: Sequence[dict], tf_sec: int, candles_4h: Sequence[dict] = (),
               length: int = 14, horizons: Sequence[int] = (1, 4, 16, 64),
               sl_atr: float = 1.5, tp_atr: float = 3.0, max_bars: int = 200,
               master_length: int = 8, quiet: bool = True) -> List[Dict]:
    n = len(candles)
    res = tl.compute(candles, length=length)
    atrs = tl.atr_series(candles, 14)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    op = [float(c["open"]) for c in candles]
    vo = [float(c.get("volume") or 0.0) for c in candles]
    rows: List[Dict] = []
    for b in res.events:
        i = b.index
        a = atrs[i]
        if not a or i < 110:
            continue
        s = 1.0 if b.direction == tl.LONG else -1.0
        rng = hi[i] - lo[i]
        vavg = sum(vo[i - 20:i]) / 20.0
        past_atr = [x for x in atrs[i - 100:i] if x]
        prior_ext = max(hi[i - 96:i]) if s > 0 else min(lo[i - 96:i])
        f = {
            "vol_ratio": vo[i] / vavg if vavg > 0 else None,
            "squeeze": a / median(past_atr) if past_atr else None,
            "range_compress": (sum(hi[k] - lo[k] for k in range(i - 10, i)) / 10.0) / a,
            "runup": s * (cl[i - 1] - cl[i - 11]) / a,
            "break_atr": s * (cl[i] - b.line_value) / a,
            "body_ratio": s * (cl[i] - op[i]) / rng if rng > 0 else 0.0,
            "body_atr": s * (cl[i] - op[i]) / a,
            "room_atr": s * (prior_ext - cl[i]) / a,
            "line_age": float(i - b.line.pivot_index),
        }
        r = {"ts": b.ts, "side": b.direction, "i": i, "atr": a, **f}
        for h in horizons:
            r[f"fwd{h}"] = s * (cl[i + h] - cl[i]) / a if i + h < n else None
        # SL/TP race from the break close on setup-tf bars; SL wins a same-bar tie
        r["tp_first"] = None
        for k in range(i + 1, min(n, i + 1 + max_bars)):
            adv = (cl[i] - lo[k]) if s > 0 else (hi[k] - cl[i])
            fav = (hi[k] - cl[i]) if s > 0 else (cl[i] - lo[k])
            if adv >= sl_atr * a:
                r["tp_first"] = 0
                break
            if fav >= tp_atr * a:
                r["tp_first"] = 1
                break
        if candles_4h:
            closed = [c for c in candles_4h if int(c["ts"]) + 14400 <= b.ts + tf_sec]
            if len(closed) >= 2 * master_length + 2:
                d4 = tl.structure_direction(closed, master_length)["direction"]
                r["master4h"] = "AGREE" if d4 == b.direction else ("NEUTRAL" if d4 == "NEUTRAL" else "OPPOSE")
            else:
                r["master4h"] = None
        rows.append(r)
    return rows


def study(rows: List[Dict], horizons: Sequence[int]) -> Dict:
    """Per feature x horizon: Spearman rho/p, split-half stability, Bonferroni flag."""
    feats = [f for f in FEATURES if any(r.get(f) is not None for r in rows)]
    tests = len(feats) * (len(horizons) + 1)
    alpha = 0.05 / max(1, tests)
    half = len(rows) // 2
    out = []
    for f in feats:
        for h in list(horizons) + ["tp"]:
            key = "tp_first" if h == "tp" else f"fwd{h}"
            def sel(rs):
                pts = [(r[f], r[key]) for r in rs if r.get(f) is not None and r.get(key) is not None]
                return [p[0] for p in pts], [float(p[1]) for p in pts]
            x, y = sel(rows)
            if len(x) < 30:
                continue
            rho, p = spearman(x, y)
            r1 = spearman(*sel(rows[:half]))[0]
            r2 = spearman(*sel(rows[half:]))[0]
            stable = r1 * r2 > 0 and abs(r1) > 0 and abs(r2) > 0
            out.append({"feature": f, "target": key, "n": len(x), "rho": rho, "p": p,
                        "rho_h1": r1, "rho_h2": r2,
                        "flag": bool(p < alpha and stable)})
    return {"tests": tests, "alpha": alpha, "results": out}


def quantile_table(rows: List[Dict], feature: str, horizons: Sequence[int], q: int = 5):
    pts = [r for r in rows if r.get(feature) is not None]
    if len(pts) < q * 20:
        q = 3
    pts.sort(key=lambda r: r[feature])
    n = len(pts)
    table = []
    for k in range(q):
        g = pts[k * n // q:(k + 1) * n // q]
        if not g:
            continue
        row = {"lo": g[0][feature], "hi": g[-1][feature], "n": len(g)}
        for h in horizons:
            v = [r[f"fwd{h}"] for r in g if r.get(f"fwd{h}") is not None]
            row[f"fwd{h}"] = mean_t(v) if v else (0.0, 0.0)
        tp = [r["tp_first"] for r in g if r["tp_first"] is not None]
        row["tp"] = (sum(tp) / len(tp), len(tp)) if tp else (0.0, 0)
        table.append(row)
    return table
