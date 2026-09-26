#!/usr/bin/env python3
"""READ-ONLY study: do consolidation and late entry explain the losing Hunt FIREs?

Uses the replay FIRE population written by tp_research.py and the local
candle database. Optionally cross-checks against the real MEXC ledger
from mexc_history_collect.py (the only source with lifecycle exits).
Imports no trading code, writes nothing except the --out report.

  python scripts/consolidation_study.py --replay tp_research_result.json
  python scripts/consolidation_study.py --replay tp_research_result.json --ledger data/mexc_history/ledger.json

CAUSAL / NO LOOKAHEAD
  Every market measurement for a FIRE at time T uses only candles that had
  CLOSED by T (15m: ts+900 <= T, 5m: ts+300 <= T). The FIRE's own entry
  price is known at T. Outcomes are only used after classification.

MEASUREMENTS (all fixed before looking at outcomes; nothing is fitted)
  ER16      Kaufman efficiency ratio of the last 16 closed 15m closes (4h):
            |close[-1] - close[-17]| / sum(|close[i] - close[i-1]|).
            1.0 = price went straight one way; ~0 = went nowhere while moving
            a lot (chop). A standard, parameter-light trend/chop measure.
  RANGE16   (highest high - lowest low) of those 16 bars / ATR15.
  RUN15     How far price already travelled in the trade direction before
            the FIRE: entry - lowest low of the last 8 closed 15m (LONG),
            highest high - entry (SHORT), in ATR15.
  RUN5      Same over the last 6 closed 5m bars (30 min), in ATR15.
  OFFSET    Entry distance beyond the Hunt thesis level in ATR, recovered
            exactly from the FIRE's own SL/TP (SL = level -/+ 1.5 ATR,
            TP = level +/- 2.5 ATR  =>  offset = (sl_dist - tp_dist + ATR) / 2).
            > 0 means the entry is already past the level in the trade direction.
  POS16     Where the entry sits inside the 16-bar range, in trade direction:
            0 = bottom of the range for a LONG (top for a SHORT), 1 = the other end.

GROUPS
  Regime (primary): terciles of ER16 over this FIRE population
    lowest third = CONSOLIDATION, highest third = TREND, middle = UNCLEAR.
  Regime (check):   fixed ER16 cut-offs 0.25 / 0.45, declared up front.
  Timing (primary): RUN15 above the population median = LATE, else EARLY.
  Timing (check):   OFFSET > 0.25 ATR = LATE vs the Hunt level.
  Terciles / medians use only the pre-trade values, never outcomes.

OUTCOMES
  Replay: bracket-only (TP 2.5 ATR / SL 1.5 ATR) from tp_research; fees are
  re-applied at the REAL MEXC rate (--fee-pct, default 0.08% per side).
  Lifecycle exits are not simulated in the replay. They exist only in the
  real ledger (--ledger), which is shown separately and is small.
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
CURRENT = "atr2.5 (current)"
ER_FIXED = (0.25, 0.45)
OFFSET_LATE = 0.25


def to_ts(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    raise ValueError(s)


def load(db, tf, symbol="BTC_USDT"):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("SELECT ts,open,high,low,close FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
                     (symbol, tf)).fetchall()
    c.close()
    return [{"ts": int(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in rows]


class Closed:
    def __init__(self, rows, step):
        self.rows, self.step, self.ts = rows, step, [r["ts"] for r in rows]

    def upto(self, t, n):
        k = bisect_right(self.ts, t - self.step)  # only bars whose close time <= t
        return self.rows[max(0, k - n):k]


def atr14(bars):
    if len(bars) < 15:
        return None
    tr = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
          for a, b in zip(bars[-15:-1], bars[-14:])]
    return sum(tr) / 14


def measure(t, side, entry, atr, c15, c5, sl_dist=None, tp_dist=None):
    b15 = c15.upto(t, 40)
    b5 = c5.upto(t, 6)
    if len(b15) < 17 or len(b5) < 6:
        return None
    atr = atr or atr14(b15)
    if not atr:
        return None
    last16 = b15[-16:]
    closes = [b["close"] for b in b15[-17:]]
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    er = abs(closes[-1] - closes[0]) / path if path else 0.0
    hi, lo = max(b["high"] for b in last16), min(b["low"] for b in last16)
    long_ = side == "LONG"
    w8 = b15[-8:]
    run15 = (entry - min(b["low"] for b in w8)) if long_ else (max(b["high"] for b in w8) - entry)
    run5 = (entry - min(b["low"] for b in b5)) if long_ else (max(b["high"] for b in b5) - entry)
    pos = ((entry - lo) / (hi - lo) if long_ else (hi - entry) / (hi - lo)) if hi > lo else 0.5
    offset = None
    if sl_dist is not None and tp_dist is not None:
        offset = (sl_dist - tp_dist + atr) / 2 / atr
    return {"er16": er, "range16": (hi - lo) / atr, "run15": run15 / atr, "run5": run5 / atr,
            "pos16": pos, "offset": offset, "atr": atr}


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


def boot_diff(a, b, n=2000, seed=11):
    if len(a) < 5 or len(b) < 5:
        return None
    r = random.Random(seed)
    d = sorted(st.mean(r.choices(a, k=len(a))) - st.mean(r.choices(b, k=len(b))) for _ in range(n))
    return d[int(.025 * n)], d[int(.975 * n)]


# ------------------------------------------------------------------ replay

def replay_rows(path, c15, c5, fee_pct):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    old_fee = float((data.get("args") or {}).get("fee_pct") or 0.02) / 100
    new_fee = fee_pct / 100
    out, skipped = [], 0
    for tr in data.get("trades") or []:
        res = (tr.get("results") or {}).get(CURRENT)
        if not res:
            skipped += 1
            continue
        t = to_ts(tr["time"])
        m = measure(t, tr["side"], tr["fill"], tr.get("atr"), c15, c5, tr.get("sl_dist"), tr.get("tp_dist"))
        if m is None:
            skipped += 1
            continue
        gross = res["pnl"] + 2 * old_fee * tr["fill"]
        net = gross - 2 * new_fee * tr["fill"]
        out.append({**m, "t": t, "side": tr["side"], "fill": tr["fill"], "out": res["out"],
                    "net_pct": 100 * net / tr["fill"], "net_atr": net / m["atr"],
                    "mfe": tr.get("mfe_before_sl"), "mfe_atr": (tr.get("mfe_before_sl") or 0) / m["atr"],
                    "sl_min": res["bars"] if res["out"] == "SL" else None,
                    "live_like": bool(tr.get("live_like")), "gate": tr.get("gate"), "timing": tr.get("timing")})
    return out, skipped, data.get("args") or {}


def label(rows, er_cut=None, late_key="run15", late_cut=None):
    ers = [r["er16"] for r in rows]
    lo_er, hi_er = er_cut if er_cut else (q(ers, 1 / 3), q(ers, 2 / 3))
    lc = late_cut if late_cut is not None else q([r[late_key] for r in rows if r[late_key] is not None], .5)
    for r in rows:
        r["regime"] = "CONSOLIDATION" if r["er16"] <= lo_er else ("TREND" if r["er16"] >= hi_er else "UNCLEAR")
        r["timing_grp"] = None if r[late_key] is None else ("LATE" if r[late_key] > lc else "EARLY")
    return lo_er, hi_er, lc


def table(rows, title, L):
    groups = [("EARLY", "TREND"), ("LATE", "TREND"), ("EARLY", "CONSOLIDATION"), ("LATE", "CONSOLIDATION"),
              ("EARLY", "UNCLEAR"), ("LATE", "UNCLEAR")]
    all_sl = sum(1 for r in rows if r["out"] == "SL") or 1
    all_loss = sum(1 for r in rows if r["net_pct"] <= 0) or 1
    L.append(f"\n{title}")
    h = (f"{'group':<22}{'n':>5}{'%FIRE':>7}{'TP':>5}{'SL':>5}{'T/O':>5}{'win%':>7}{'net%':>8}{'exp%':>8}"
         f"{'netATR':>8}{'medMFE':>8}{'MFE/ATR':>8}{'%ofSL':>7}{'%ofLoss':>8}{'SLmin':>6}")
    L.append(h)
    L.append("-" * len(h))
    for tg, rg in groups:
        g = [r for r in rows if r["timing_grp"] == tg and r["regime"] == rg]
        if not g:
            L.append(f"{tg + ' + ' + rg:<22}{0:>5}")
            continue
        net = [r["net_pct"] for r in g]
        L.append(f"{tg + ' + ' + rg:<22}{len(g):>5}{100 * len(g) / len(rows):>6.1f}%"
                 f"{sum(r['out'] == 'TP' for r in g):>5}{sum(r['out'] == 'SL' for r in g):>5}"
                 f"{sum(r['out'] == 'TIMEOUT' for r in g):>5}{100 * sum(x > 0 for x in net) / len(g):>7.1f}"
                 f"{sum(net):>8.2f}{st.mean(net):>8.3f}{sum(r['net_atr'] for r in g):>8.1f}"
                 f"{fmt(med([r['mfe'] for r in g]), 0):>8}{fmt(med([r['mfe_atr'] for r in g])):>8}"
                 f"{100 * sum(r['out'] == 'SL' for r in g) / all_sl:>6.1f}%"
                 f"{100 * sum(r['net_pct'] <= 0 for r in g) / all_loss:>7.1f}%"
                 f"{fmt(med([r['sl_min'] for r in g]), 0):>6}")
    lc = [r["net_pct"] for r in rows if r["timing_grp"] == "LATE" and r["regime"] == "CONSOLIDATION"]
    et = [r["net_pct"] for r in rows if r["timing_grp"] == "EARLY" and r["regime"] == "TREND"]
    ci = boot_diff(lc, et)
    if ci:
        L.append(f"  expectancy LATE+CONSOLIDATION minus EARLY+TREND: {st.mean(lc) - st.mean(et):+.3f}% "
                 f"(95% bootstrap {ci[0]:+.3f} .. {ci[1]:+.3f})")


def by_quantile(rows, key, title, L, k=5):
    v = [r for r in rows if r[key] is not None]
    if len(v) < k * 5:
        return
    v.sort(key=lambda r: r[key])
    L.append(f"\n{title}  (quintiles of {key}; no thresholds chosen)")
    L.append(f"{'bucket':<8}{key + ' range':>20}{'n':>6}{'win%':>7}{'exp%':>8}{'SL%':>7}{'MFE/ATR':>9}")
    for i in range(k):
        g = v[i * len(v) // k:(i + 1) * len(v) // k]
        net = [r["net_pct"] for r in g]
        L.append(f"Q{i + 1:<7}{fmt(g[0][key]) + ' .. ' + fmt(g[-1][key]):>20}{len(g):>6}"
                 f"{100 * sum(x > 0 for x in net) / len(g):>7.1f}{st.mean(net):>8.3f}"
                 f"{100 * sum(r['out'] == 'SL' for r in g) / len(g):>7.1f}{fmt(med([r['mfe_atr'] for r in g])):>9}")


# ------------------------------------------------------------------ real ledger (lifecycle)

def ledger_rows(path, c15, c5):
    out = []
    for r in json.loads(Path(path).read_text(encoding="utf-8")):
        if r.get("kind") != "MIB_SUBMITTED" or r.get("realised_pnl") is None or not r.get("fill_price"):
            continue
        t = to_ts(r["submit_time"])
        m = measure(t, r["side"], r["fill_price"], None, c15, c5)
        if m is None:
            continue
        lvl = r.get("thesis_level")
        if lvl:
            d = (r["fill_price"] - float(lvl)) if r["side"] == "LONG" else (float(lvl) - r["fill_price"])
            m["offset"] = d / m["atr"]
        notional = r.get("open_notional") or 0
        out.append({**m, "t": t, "side": r["side"], "exit": r.get("exit_group"),
                    "net_usdt": r["realised_pnl"], "net_pct": 100 * r["realised_pnl"] / notional if notional else 0.0})
    return out


def ledger_table(rows, lo_er, hi_er, lc, L):
    for r in rows:
        r["regime"] = "CONSOLIDATION" if r["er16"] <= lo_er else ("TREND" if r["er16"] >= hi_er else "UNCLEAR")
        r["timing_grp"] = "LATE" if r["run15"] > lc else "EARLY"
    L.append("\nREAL MEXC TRADES (same cut-offs as the replay; includes lifecycle exits; SMALL sample)")
    h = f"{'group':<22}{'n':>4}{'TP':>4}{'SL':>4}{'LIFE':>5}{'MAN':>4}{'win%':>7}{'net USDT':>10}{'avg USDT':>10}"
    L.append(h)
    L.append("-" * len(h))
    for tg in ("EARLY", "LATE"):
        for rg in ("TREND", "UNCLEAR", "CONSOLIDATION"):
            g = [r for r in rows if r["timing_grp"] == tg and r["regime"] == rg]
            if not g:
                continue
            L.append(f"{tg + ' + ' + rg:<22}{len(g):>4}{sum(r['exit'] == 'TP' for r in g):>4}"
                     f"{sum(r['exit'] == 'SL' for r in g):>4}{sum(r['exit'] == 'LIFECYCLE' for r in g):>5}"
                     f"{sum(r['exit'] == 'MANUAL' for r in g):>4}"
                     f"{100 * sum(r['net_usdt'] > 0 for r in g) / len(g):>7.1f}{sum(r['net_usdt'] for r in g):>10.3f}"
                     f"{st.mean(r['net_usdt'] for r in g):>10.3f}")


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True, help="tp_research output JSON")
    ap.add_argument("--db", default=str(BACKEND / "market_data_clean.db"))
    ap.add_argument("--ledger", help="data/mexc_history/ledger.json (optional)")
    ap.add_argument("--fee-pct", type=float, default=0.08, help="real MEXC fee per side, %")
    ap.add_argument("--out", default="consolidation_study.txt")
    a = ap.parse_args()

    c15 = Closed(load(a.db, "15m"), 900)
    c5 = Closed(load(a.db, "5m"), 300)
    rows, skipped, rargs = replay_rows(a.replay, c15, c5, a.fee_pct)
    if not rows:
        sys.exit("no usable FIREs in replay file")
    L = [f"Replay {a.replay}: {len(rows)} FIREs ({skipped} skipped: no result or not enough closed candles)",
         f"  {datetime.fromtimestamp(rows[0]['t'], timezone.utc):%Y-%m-%d} -> "
         f"{datetime.fromtimestamp(rows[-1]['t'], timezone.utc):%Y-%m-%d}   replay horizon "
         f"{rargs.get('horizon_hours')}h   fees re-applied at {a.fee_pct}%/side   outcomes: bracket TP/SL only",
         "  NOTE: replay FIREs overlap in time (many per thesis); the LIVE-like subset is the tradable view."]

    L.append("\n== DISTRIBUTION OF THE PRE-TRADE MEASURES ==")
    for k in ("er16", "range16", "run15", "run5", "offset", "pos16"):
        v = [r[k] for r in rows if r[k] is not None]
        L.append(f"  {k:<8} p10 {q(v, .1):6.2f}  p25 {q(v, .25):6.2f}  med {q(v, .5):6.2f}  p75 {q(v, .75):6.2f}  p90 {q(v, .9):6.2f}")

    lo_er, hi_er, lc = label(rows)
    L.append(f"\nPRIMARY cut-offs: ER16 terciles {lo_er:.3f} / {hi_er:.3f}; LATE = RUN15 > median {lc:.2f} ATR")
    table(rows, "== PRIMARY: ALL FIREs ==", L)
    ll = [r for r in rows if r["live_like"]]
    if ll:
        table(ll, f"== PRIMARY: LIVE-like FIREs only (n={len(ll)}) ==", L)

    label(rows, er_cut=ER_FIXED)
    table(rows, f"== CHECK 1: fixed ER cut-offs {ER_FIXED[0]}/{ER_FIXED[1]} (declared in advance) ==", L)
    label(rows, late_key="offset", late_cut=OFFSET_LATE)
    table(rows, f"== CHECK 2: LATE = entry > {OFFSET_LATE} ATR past the Hunt level ==", L)
    label(rows)  # restore primary

    by_quantile(rows, "er16", "== WIN RATE / EXPECTANCY BY ER16 ==", L)
    by_quantile(rows, "run15", "== BY RUN15 (move already made before FIRE) ==", L)
    by_quantile(rows, "offset", "== BY OFFSET (entry past the Hunt level) ==", L)
    by_quantile(rows, "pos16", "== BY POS16 (entry position in the 4h range) ==", L)

    tg = {}
    for r in rows:
        tg.setdefault(f"{r['gate']}/{r['timing']}", []).append(r)
    L.append("\n== BY HUNT GATE / TIMING PATH ==")
    L.append(f"{'gate/timing':<30}{'n':>5}{'win%':>7}{'exp%':>8}{'med OFFSET':>11}{'med RUN15':>10}{'%CONSOL':>9}{'%LATE':>7}")
    for k, g in sorted(tg.items(), key=lambda x: -len(x[1])):
        net = [r["net_pct"] for r in g]
        L.append(f"{k:<30}{len(g):>5}{100 * sum(x > 0 for x in net) / len(g):>7.1f}{st.mean(net):>8.3f}"
                 f"{fmt(med([r['offset'] for r in g])):>11}{fmt(med([r['run15'] for r in g])):>10}"
                 f"{100 * sum(r['regime'] == 'CONSOLIDATION' for r in g) / len(g):>8.1f}%"
                 f"{100 * sum(r['timing_grp'] == 'LATE' for r in g) / len(g):>6.1f}%")

    if a.ledger:
        lr = ledger_rows(a.ledger, c15, c5)
        if lr:
            ledger_table(lr, lo_er, hi_er, lc, L)
    rep = "\n".join(L)
    Path(a.out).write_text(rep, encoding="utf-8")
    print(rep)
    print(f"\nWritten to {a.out}")


if __name__ == "__main__":
    main()
