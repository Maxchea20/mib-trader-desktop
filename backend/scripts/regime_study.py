#!/usr/bin/env python3
"""READ-ONLY study: why is the middle / unclear regime bad for Hunt, and why
does rearm_cfast/S1 differ from internal/S1 and internal/SLOT3?

Inputs: the replay FIRE file from tp_research.py and the local candle DB.
Uses production structure detection read-only (structure.observe) for the
BOS/CHoCH features; imports no trading/decision code, changes nothing.

  python scripts/regime_study.py --replay tp_research_result.json

CAUSAL: every feature for a FIRE at time T uses only candles CLOSED by T
(15m ts+900 <= T, 5m ts+300 <= T) plus the FIRE's own entry/SL/TP, all
known at T. Outcomes are joined only after the features are computed.

REGIMES (same as consolidation_study.py): terciles of the 16-bar 15m
efficiency ratio (ER16): CHOP (lowest third), MIDDLE, TREND (highest third).

ANTI-OVERFITTING
  * Features are a fixed list, written before looking at outcomes.
  * Filter thresholds are either natural (0, 1, "agrees") or the median of
    the FIRST half of the data only, then tested on the SECOND half.
  * A filter only counts as supported if it improves expectancy in BOTH
    halves AND in the live-like subset AND under both regime definitions.
  * ~20 features are tested; at 95% about one will look significant by
    chance. Treat single hits as "interesting", not proven.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics as st
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
CURRENT = "atr2.5 (current)"
PATHS = ("internal/SLOT3", "internal/S1", "rearm_cfast/S1")


# ------------------------------------------------------------------ data

def to_ts(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    raise ValueError(s)


def load(db, tf, symbol="BTC_USDT"):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
                     (symbol, tf)).fetchall()
    c.close()
    return [{"ts": int(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5] or 0.0}
            for r in rows]


class Closed:
    def __init__(self, rows, step):
        self.rows, self.step, self.ts = rows, step, [r["ts"] for r in rows]

    def upto(self, t, n):
        k = bisect_right(self.ts, t - self.step)  # bars whose close time <= t
        return self.rows[max(0, k - n):k]


def tr_list(bars):
    return [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
            for a, b in zip(bars, bars[1:])]


def er(closes):
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path else 0.0


def sgn(x):
    return (x > 0) - (x < 0)


# ------------------------------------------------------------------ structure (production, read-only)

_OBS = None


def structure_events(bars, pivot):
    """BOS/CHoCH history from the production structure observer on these
    closed bars. Returns [] if numpy/production code is unavailable."""
    global _OBS
    if _OBS is None:
        try:
            from src.structure.observe import observe
            _OBS = observe
        except Exception as e:  # pragma: no cover - reported once
            print(f"  (structure features disabled: {e})")
            _OBS = False
    if not _OBS or len(bars) < 30:
        return []
    try:
        obs = _OBS(bars, "15m", pivot_window_override=pivot)
    except Exception:
        return []
    out = []
    for e in obs.history or []:
        if (e.event_type or "").upper() in ("BOS", "CHOCH") and e.timestamp:
            d = 1 if e.direction == "BULLISH" else (-1 if e.direction == "BEARISH" else 0)
            out.append((int(e.timestamp), e.event_type.upper(), d))
    return out


# ------------------------------------------------------------------ features

FEATURES = [
    # name, description
    ("er16", "15m efficiency ratio, 16 bars (the regime definition itself)"),
    ("move16_dir", "15m net move over 16 bars in trade direction, ATR (+ = with the 4h move)"),
    ("agree8", "share of last 8 15m closes that moved in trade direction"),
    ("atr_ratio", "15m ATR14 / mean true range of the 50 bars before (vol expanding > 1)"),
    ("range16", "16-bar 15m high-low range, ATR"),
    ("break_clear", "entry beyond the prior 16-bar range edge, ATR (+ = outside/expanding, - = still inside)"),
    ("pos16", "entry position inside the 16-bar range in trade direction (1 = at the far edge)"),
    ("body15_dir", "last closed 15m body / range, signed by trade direction"),
    ("bar15_atr", "last closed 15m bar range, ATR"),
    ("vol_ratio", "last closed 15m volume / median of the 32 before"),
    ("er12_5m", "5m efficiency ratio, 12 bars (1h)"),
    ("move12_5m_dir", "5m net move over 12 bars in trade direction, ATR15"),
    ("flips12_5m", "direction changes among the last 12 5m closes"),
    ("agree_5_15", "1 if the 1h 5m move and the 4h 15m move point the same way as the trade"),
    ("atr5_ratio", "5m mean TR last 12 / mean TR of the 48 before"),
    ("n_ev_p2", "15m BOS+CHoCH count, pivot 2, last 32 bars (8h)"),
    ("n_choch_p2", "15m CHoCH count (direction flips), pivot 2, last 32 bars"),
    ("n_choch_p5", "15m CHoCH count, pivot 5, last 64 bars (16h)"),
    ("p5_agree", "1 if the latest pivot-5 BOS/CHoCH points in trade direction"),
    ("since_choch_p5", "15m bars since the latest pivot-5 CHoCH"),
    ("thesis_age_h", "hours since the Hunt thesis event (thesis_ts)"),
    ("offset", "entry past the Hunt level, ATR (from the FIRE's own SL/TP)"),
    ("tp_atr", "TP distance from entry, ATR"),
    ("sl_atr", "SL distance from entry, ATR"),
]


def features(t, side, entry, A, sl_dist, tp_dist, thesis_ts, c15, c5, with_structure):
    d = 1 if side == "LONG" else -1
    b = c15.upto(t, 120)
    b5 = c5.upto(t, 60)
    if len(b) < 70 or len(b5) < 60 or not A:
        return None
    closes = [x["close"] for x in b]
    last16 = b[-16:]
    prior16 = b[-17:-1]
    hi, lo = max(x["high"] for x in last16), min(x["low"] for x in last16)
    trs = tr_list(b[-66:])
    atr_now, atr_base = sum(trs[-14:]) / 14, st.mean(trs[-64:-14])
    ch8 = [closes[i] - closes[i - 1] for i in range(len(closes) - 8, len(closes))]
    lastbar = b[-1]
    rng = lastbar["high"] - lastbar["low"]
    vols = [x["volume"] for x in b[-33:-1]]
    c5c = [x["close"] for x in b5]
    ch5 = [c5c[i] - c5c[i - 1] for i in range(len(c5c) - 12, len(c5c))]
    tr5 = tr_list(b5)
    move16 = (closes[-1] - closes[-17]) * d / A
    move5 = (c5c[-1] - c5c[-13]) * d / A
    f = {
        "er16": er(closes[-17:]),
        "move16_dir": move16,
        "agree8": sum(1 for x in ch8 if sgn(x) == d) / 8,
        "atr_ratio": atr_now / atr_base if atr_base else None,
        "range16": (hi - lo) / A,
        "break_clear": ((entry - max(x["high"] for x in prior16)) if d > 0
                        else (min(x["low"] for x in prior16) - entry)) / A,
        "pos16": ((entry - lo) / (hi - lo) if d > 0 else (hi - entry) / (hi - lo)) if hi > lo else 0.5,
        "body15_dir": (lastbar["close"] - lastbar["open"]) * d / rng if rng else 0.0,
        "bar15_atr": rng / A,
        "vol_ratio": (lastbar["volume"] / st.median(vols)) if vols and st.median(vols) else None,
        "er12_5m": er(c5c[-13:]),
        "move12_5m_dir": move5,
        "flips12_5m": sum(1 for a, c in zip(ch5, ch5[1:]) if sgn(a) and sgn(c) and sgn(a) != sgn(c)),
        "agree_5_15": 1 if (sgn(move16) == 1 and sgn(move5) == 1) else 0,
        "atr5_ratio": (st.mean(tr5[-12:]) / st.mean(tr5[-60:-12])) if len(tr5) >= 59 and st.mean(tr5[-60:-12]) else None,
        "thesis_age_h": (t - int(thesis_ts)) / 3600 if thesis_ts else None,
        "offset": (sl_dist - tp_dist + A) / 2 / A,
        "tp_atr": tp_dist / A,
        "sl_atr": sl_dist / A,
    }
    for k in ("n_ev_p2", "n_choch_p2", "n_choch_p5", "p5_agree", "since_choch_p5"):
        f[k] = None
    if with_structure:
        last_ts = b[-1]["ts"]
        ev2 = structure_events(b[-100:], 2)
        ev5 = structure_events(b[-120:], 5)
        w32, w64 = last_ts - 31 * 900, last_ts - 63 * 900
        f["n_ev_p2"] = sum(1 for e in ev2 if e[0] >= w32)
        f["n_choch_p2"] = sum(1 for e in ev2 if e[0] >= w32 and e[1] == "CHOCH")
        f["n_choch_p5"] = sum(1 for e in ev5 if e[0] >= w64 and e[1] == "CHOCH")
        if ev5:
            f["p5_agree"] = 1 if ev5[-1][2] == d else 0
            ch = [e for e in ev5 if e[1] == "CHOCH"]
            f["since_choch_p5"] = (last_ts - ch[-1][0]) / 900 if ch else None
    return f


def rows_from_replay(path, c15, c5, fee_pct, with_structure):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    old_fee = float((data.get("args") or {}).get("fee_pct") or 0.02) / 100
    new_fee = fee_pct / 100
    out, skipped = [], 0
    trades = data.get("trades") or []
    for i, tr in enumerate(trades):
        if with_structure and i and i % 200 == 0:
            print(f"  features {i}/{len(trades)}", flush=True)
        res = (tr.get("results") or {}).get(CURRENT)
        if not res or not tr.get("atr"):
            skipped += 1
            continue
        t = to_ts(tr["time"])
        f = features(t, tr["side"], tr["fill"], tr["atr"], tr["sl_dist"], tr["tp_dist"], tr.get("thesis_ts"),
                     c15, c5, with_structure)
        if f is None:
            skipped += 1
            continue
        gross = res["pnl"] + 2 * old_fee * tr["fill"]
        net = gross - 2 * new_fee * tr["fill"]
        out.append({**f, "t": t, "side": tr["side"], "out": res["out"], "net_pct": 100 * net / tr["fill"],
                    "net_r": net / tr["sl_dist"] if tr["sl_dist"] else 0.0,
                    "mfe_atr": (tr.get("mfe_before_sl") or 0) / tr["atr"], "live_like": bool(tr.get("live_like")),
                    "path": f"{tr.get('gate')}/{tr.get('timing')}"})
    out.sort(key=lambda r: r["t"])
    return out, skipped


# ------------------------------------------------------------------ stats

def q(v, p):
    v = sorted(v)
    k = (len(v) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def med(v):
    v = [x for x in v if x is not None]
    return st.median(v) if v else None


def fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def summ(g):
    if not g:
        return None
    net = [r["net_pct"] for r in g]
    gp, gl = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    return {"n": len(g), "win": 100 * sum(x > 0 for x in net) / len(g), "exp": st.mean(net), "net": sum(net),
            "exp_r": st.mean(r["net_r"] for r in g),
            "pf": gp / gl if gl else float("inf"), "tp": sum(r["out"] == "TP" for r in g),
            "sl": sum(r["out"] == "SL" for r in g), "mfe": med([r["mfe_atr"] for r in g])}


def shift_p(rows, mask, key="net_r", n=400, seed=3):
    """Circular-shift null test. rows are time-sorted; mask[i] marks group A.
    Statistic = mean(key | A) - mean(key | not A). The outcome sequence is
    rotated in time against the (fixed) mask, which keeps the clustering of
    overlapping FIREs intact -- unlike a bootstrap that treats them as
    independent. One-sided p = share of rotations with a statistic >= the
    real one (for a positive real statistic; mirrored for negative)."""
    m = len(rows)
    ya = [r[key] for r in rows]
    na = sum(mask)
    if na < 10 or m - na < 10:
        return None, None
    tot = sum(ya)

    def stat(y):
        sa = sum(v for v, k in zip(y, mask) if k)
        return sa / na - (tot - sa) / (m - na)

    real = stat(ya)
    r = random.Random(seed)
    hits = 0
    for _ in range(n):
        k = r.randrange(m // 10, m - m // 10)
        s_ = stat(ya[k:] + ya[:k])
        hits += (s_ >= real) if real >= 0 else (s_ <= real)
    return real, (hits + 1) / (n + 1)


def set_regime(rows, cuts=None):
    ers = [r["er16"] for r in rows]
    lo, hi = cuts if cuts else (q(ers, 1 / 3), q(ers, 2 / 3))
    for r in rows:
        r["regime"] = "CHOP" if r["er16"] <= lo else ("TREND" if r["er16"] >= hi else "MIDDLE")
    return lo, hi


# ------------------------------------------------------------------ sections

def section_regimes(rows, L):
    L.append("\n================ 1. WHAT DISTINGUISHES CHOP / MIDDLE / TREND ================")
    L.append("Outcomes by regime:")
    L.append(f"{'regime':<8}{'n':>6}{'win%':>7}{'exp%':>8}{'expR':>7}{'PF':>6}{'TP%':>6}{'SL%':>6}{'MFE/ATR':>9}")
    for rg in ("TREND", "MIDDLE", "CHOP"):
        s = summ([r for r in rows if r["regime"] == rg])
        L.append(f"{rg:<8}{s['n']:>6}{s['win']:>7.1f}{s['exp']:>8.3f}{s['exp_r']:>7.3f}{s['pf']:>6.2f}"
                 f"{100 * s['tp'] / s['n']:>6.1f}{100 * s['sl'] / s['n']:>6.1f}{fmt(s['mfe']):>9}")
    L.append("\nMedian feature value per regime (pre-trade only):")
    L.append(f"{'feature':<16}{'TREND':>9}{'MIDDLE':>9}{'CHOP':>9}   description")
    for k, desc in FEATURES:
        vals = [med([r[k] for r in rows if r["regime"] == rg]) for rg in ("TREND", "MIDDLE", "CHOP")]
        if all(v is None for v in vals):
            continue
        L.append(f"{k:<16}" + "".join(f"{fmt(v):>9}" for v in vals) + f"   {desc}")


def section_within(rows, L, half_t):
    """Inside each regime: does a feature separate winners from losers?
    Split each feature at its FIRST-HALF median; compare expectancy of the
    two sides; report both halves separately."""
    L.append("\n================ 1b. INSIDE THE MIDDLE REGIME: WHAT SEPARATES GOOD FROM BAD FIREs ================")
    L.append("Each feature split at its FIRST-HALF median (all regimes pooled), so the cut is not fitted to outcomes.")
    L.append("'diff' = expectancy(above) - expectancy(below) in %; p = time-shift null test in risk units (MIDDLE regime).")
    L.append(f"{'feature':<16}{'cut':>7}{'n>':>5}{'n<=':>5}{'exp>':>8}{'exp<=':>8}{'diff':>8}"
             f"{'p':>8}{'1st half':>9}{'2nd half':>9}{'  also in TREND/CHOP':>21}{'diff R':>8}")
    mid = [r for r in rows if r["regime"] == "MIDDLE"]
    first = [r for r in rows if r["t"] < half_t]
    res = []
    for k, _ in FEATURES:
        if k == "er16":
            continue
        fv = [r[k] for r in first if r[k] is not None]
        if len(fv) < 30:
            continue
        cut = q(fv, .5)
        a = [r["net_pct"] for r in mid if r[k] is not None and r[k] > cut]
        b = [r["net_pct"] for r in mid if r[k] is not None and r[k] <= cut]
        if len(a) < 15 or len(b) < 15:
            continue
        diff = st.mean(a) - st.mean(b)
        _, pval = shift_p(mid, [r[k] is not None and r[k] > cut for r in mid])

        def half_diff(sub):
            a2 = [r["net_pct"] for r in sub if r[k] is not None and r[k] > cut]
            b2 = [r["net_pct"] for r in sub if r[k] is not None and r[k] <= cut]
            return (st.mean(a2) - st.mean(b2)) if len(a2) >= 8 and len(b2) >= 8 else None

        h1 = half_diff([r for r in mid if r["t"] < half_t])
        h2 = half_diff([r for r in mid if r["t"] >= half_t])
        other = half_diff([r for r in rows if r["regime"] != "MIDDLE"])
        ar = [r["net_r"] for r in mid if r[k] is not None and r[k] > cut]
        br = [r["net_r"] for r in mid if r[k] is not None and r[k] <= cut]
        diff_r = st.mean(ar) - st.mean(br)
        res.append((k, cut, len(a), len(b), st.mean(a), st.mean(b), diff, pval, h1, h2, other, diff_r))
    res.sort(key=lambda x: -abs(x[6]))
    for k, cut, na, nb, ea, eb, diff, pval, h1, h2, other, diff_r in res:
        sig = "*" if pval is not None and pval < 0.05 and sgn(diff) == sgn(diff_r) else " "
        both = "=" if h1 is not None and h2 is not None and sgn(h1) == sgn(h2) == sgn(diff) else " "
        L.append(f"{k:<16}{cut:>7.2f}{na:>5}{nb:>5}{ea:>8.3f}{eb:>8.3f}{diff:>+8.3f}"
                 f"{fmt(pval, 3):>8}{sig}"
                 f"{fmt(h1, 3):>8}{fmt(h2, 3):>9}{both}{fmt(other, 3):>20}{diff_r:>+8.3f}")
    L.append("  * = p < 0.05 and same sign in % and R units (with ~20 features, ~1 false '*' is expected by chance;")
    L.append("      Bonferroni for 20 features would need p < 0.0025)")
    L.append("  = = same sign in both halves of the data;  diff R = same comparison in risk units (net / SL distance)")
    return res


def section_paths(rows, L):
    L.append("\n================ 2. HUNT PATHS ================")
    h = (f"{'path':<24}{'n':>5}{'win%':>7}{'exp%':>8}{'expR':>7}{'net%':>8}{'PF':>6}{'TP%':>6}{'SL%':>6}{'MFE/ATR':>8}"
         f"{'TREND':>7}{'MIDDLE':>7}{'CHOP':>6}")
    L.append(h)
    L.append("-" * len(h))
    paths = {}
    for r in rows:
        paths.setdefault(r["path"], []).append(r)
    for p, g in sorted(paths.items(), key=lambda x: -len(x[1])):
        s = summ(g)
        mix = [100 * sum(r["regime"] == rg for r in g) / len(g) for rg in ("TREND", "MIDDLE", "CHOP")]
        L.append(f"{p:<24}{s['n']:>5}{s['win']:>7.1f}{s['exp']:>8.3f}{s['exp_r']:>7.3f}{s['net']:>8.2f}{s['pf']:>6.2f}"
                 f"{100 * s['tp'] / s['n']:>6.1f}{100 * s['sl'] / s['n']:>6.1f}{fmt(s['mfe']):>8}"
                 + "".join(f"{m:>6.0f}%" for m in mix))
    L.append("\nGeometry and structure of the three paths (medians):")
    keys = ["offset", "tp_atr", "sl_atr", "thesis_age_h", "move16_dir", "er16", "break_clear", "pos16",
            "agree_5_15", "p5_agree", "n_choch_p2", "n_choch_p5", "since_choch_p5", "atr_ratio", "mfe_atr"]
    L.append(f"{'path':<18}" + "".join(f"{k[:11]:>12}" for k in keys))
    for p in PATHS:
        g = paths.get(p, [])
        if g:
            L.append(f"{p:<18}" + "".join(f"{fmt(med([r[k] for r in g])):>12}" for k in keys))
    L.append("\nBreak-even win rate implied by each FIRE's own TP/SL (sl/(tp+sl), before fees) vs actual:")
    for p in PATHS:
        g = paths.get(p, [])
        if not g:
            continue
        be = st.mean(r["sl_atr"] / (r["sl_atr"] + r["tp_atr"]) for r in g if r["sl_atr"] + r["tp_atr"] > 0)
        s = summ(g)
        L.append(f"  {p:<18} break-even {100 * be:5.1f}%   actual win {s['win']:5.1f}%   "
                 f"TP-hit {100 * s['tp'] / s['n']:5.1f}%   edge {s['win'] - 100 * be:+5.1f} pts")
    L.append("\nSame path, split by regime (expectancy % / n):")
    for p in PATHS:
        g = paths.get(p, [])
        if g:
            cells = []
            for rg in ("TREND", "MIDDLE", "CHOP"):
                s = summ([r for r in g if r["regime"] == rg])
                cells.append(f"{rg} {fmt(s['exp'], 3) if s else '-'} / {s['n'] if s else 0}")
            L.append(f"  {p:<18} " + "   ".join(cells))
    L.append("\nPath difference that survives controlling for geometry: expectancy when offset <= 0.25 ATR only")
    for p in PATHS:
        g = [r for r in paths.get(p, []) if r["offset"] <= 0.25]
        s = summ(g)
        L.append(f"  {p:<18} n={s['n'] if s else 0:<4} exp {fmt(s['exp'], 3) if s else '-'}   "
                 f"win {fmt(s['win'], 1) if s else '-'}")


CANDIDATES = [
    # name, test(r, cuts) -> True = KEEP the FIRE. Natural thresholds or first-half medians only.
    ("with 4h move (move16_dir > 0)", lambda r, c: r["move16_dir"] > 0),
    ("5m and 15m agree with trade", lambda r, c: r["agree_5_15"] == 1),
    ("entry outside prior 4h range (break_clear > 0)", lambda r, c: r["break_clear"] > 0),
    ("volatility expanding (atr_ratio >= 1)", lambda r, c: r["atr_ratio"] is not None and r["atr_ratio"] >= 1),
    ("latest pivot-5 event agrees (p5_agree == 1)", lambda r, c: r["p5_agree"] is None or r["p5_agree"] == 1),
    ("few pivot-2 flips (n_choch_p2 <= 1st-half median)", lambda r, c: r["n_choch_p2"] is None or r["n_choch_p2"] <= c["n_choch_p2"]),
    ("5m calm direction (flips12_5m <= 1st-half median)", lambda r, c: r["flips12_5m"] <= c["flips12_5m"]),
    ("not MIDDLE regime (reference only)", lambda r, c: r["regime"] != "MIDDLE"),
]


def eval_filter(rows, keep):
    k = [r for r in rows if keep(r)]
    x = [r for r in rows if not keep(r)]
    return summ(k), summ(x)


def section_filters(rows, L, half_t):
    L.append("\n================ 3. CANDIDATE FILTERS (keep FIRE only if condition holds) ================")
    first = [r for r in rows if r["t"] < half_t]
    cuts = {k: q([r[k] for r in first if r[k] is not None], .5) for k in ("n_choch_p2", "flips12_5m")
            if any(r[k] is not None for r in first)}
    L.append("A filter is SUPPORTED only if it improves every subset in both units AND its time-shift p < 0.05/8 = 0.006")
    L.append("on ALL (8 candidates tested) AND p < 0.05 on live-like. Anything weaker is 'interesting', not proven.")
    L.append(f"First-half medians used as cuts: {', '.join(f'{k}={v:.2f}' for k, v in cuts.items())}")
    base = summ(rows)
    L.append(f"Baseline ALL: n={base['n']} exp {base['exp']:.3f}% PF {base['pf']:.2f} TP {base['tp']} SL {base['sl']}")
    subsets = [("ALL", rows), ("1st half", [r for r in rows if r["t"] < half_t]),
               ("2nd half", [r for r in rows if r["t"] >= half_t]), ("live-like", [r for r in rows if r["live_like"]])]
    for name, fn in CANDIDATES:
        try:
            keep = lambda r, fn=fn: fn(r, cuts)
            L.append(f"\n  {name}")
            ok = []
            for sname, sub in subsets:
                if not sub:
                    continue
                kept, removed = eval_filter(sub, keep)
                b = summ(sub)
                if not kept:
                    continue
                rm_n = removed["n"] if removed else 0
                L.append(f"    {sname:<10} keep {kept['n']:>4}/{b['n']:<4} removed SL {removed['sl'] if removed else 0:>4}"
                         f" TP {removed['tp'] if removed else 0:>4} net {removed['net'] if removed else 0:>+7.2f}%   "
                         f"exp {b['exp']:+.3f} -> {kept['exp']:+.3f}   PF {b['pf']:.2f} -> {kept['pf']:.2f}"
                         f"   removed exp {fmt(removed['exp'], 3) if removed else '-'}"
                         f"   expR {b['exp_r']:+.3f} -> {kept['exp_r']:+.3f}")
                ok.append(kept["exp"] > b["exp"] and kept["exp_r"] > b["exp_r"] and rm_n > 0)
            p_all = shift_p(rows, [keep(r) for r in rows])[1]
            ll = [r for r in rows if r["live_like"]]
            p_ll = shift_p(ll, [keep(r) for r in ll])[1] if ll else None
            L.append(f"    improves in every subset (both % and R units): {'YES' if ok and all(ok) else 'no'}"
                     f"   time-shift p (kept better than removed, R units): ALL {fmt(p_all, 3)}  live-like {fmt(p_ll, 3)}")
            sup = bool(ok and all(ok) and p_all is not None and p_all < 0.05 / 8 and p_ll is not None and p_ll < 0.05)
            L.append(f"    SUPPORTED: {'YES' if sup else 'no'}")
        except Exception as e:  # a feature missing entirely
            L.append(f"    skipped ({e})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True)
    ap.add_argument("--db", default=str(BACKEND / "market_data_clean.db"))
    ap.add_argument("--fee-pct", type=float, default=0.08)
    ap.add_argument("--no-structure", action="store_true", help="skip BOS/CHoCH features (no numpy needed)")
    ap.add_argument("--out", default="regime_study.txt")
    a = ap.parse_args()

    print("loading candles ...")
    c15 = Closed(load(a.db, "15m"), 900)
    c5 = Closed(load(a.db, "5m"), 300)
    rows, skipped = rows_from_replay(a.replay, c15, c5, a.fee_pct, not a.no_structure)
    if not rows:
        sys.exit("no usable FIREs")
    half_t = rows[len(rows) // 2]["t"]
    lo, hi = set_regime(rows)
    L = [f"{len(rows)} FIREs ({skipped} skipped)  "
         f"{datetime.fromtimestamp(rows[0]['t'], timezone.utc):%Y-%m-%d} -> "
         f"{datetime.fromtimestamp(rows[-1]['t'], timezone.utc):%Y-%m-%d}   halves split at "
         f"{datetime.fromtimestamp(half_t, timezone.utc):%Y-%m-%d %H:%M}   fee {a.fee_pct}%/side   "
         f"regime = ER16 terciles {lo:.3f}/{hi:.3f}",
         "Outcomes are replay TP/SL brackets (no lifecycle exits). FIREs overlap in time; live-like is the honest sample."]
    section_regimes(rows, L)
    section_within(rows, L, half_t)
    section_paths(rows, L)
    section_filters(rows, L, half_t)
    set_regime(rows, (0.25, 0.45))
    L.append("\n================ ROBUSTNESS: regime = fixed ER16 cut-offs 0.25 / 0.45 ================")
    section_regimes(rows, L)
    section_filters(rows, L, half_t)
    rep = "\n".join(L)
    Path(a.out).write_text(rep, encoding="utf-8")
    print(rep)
    print(f"\nWritten to {a.out}")


if __name__ == "__main__":
    main()
