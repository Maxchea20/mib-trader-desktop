"""Path diagnostics for fired trades (analysis only; never affects entries/exits).

Each trade is replayed candle by candle on the same 1M bars the backtest uses,
starting with the candle AFTER the entry candle, exactly like the simulator.

Assumptions (identical to the backtest's execution rule):
  * A candle that touches the SL exits at the SL, even if it also touches the
    TP or a milestone ("SL first" on same-candle ambiguity).
  * A candle that touches the TP (and not the SL) exits at the TP.
  * Nothing that happens inside an SL candle is credited as a milestone.
  * Inside a non-exit candle the adverse extreme is assumed to occur BEFORE the
    favourable extreme, so a milestone reached in a candle is not charged with
    that same candle's adverse move.
R is measured against the fixed stop: LONG (p-entry)/(entry-SL), SHORT (entry-p)/(SL-entry).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

MILESTONES = (0.5, 1.0, 1.25, 1.5, 1.75, 1.9, 2.0)


def diagnose_trade(t: dict, candles: Sequence[dict], tp_r: float = 2.0) -> Dict:
    s = 1 if t["side"] == "LONG" else -1
    entry, risk = float(t["entry"]), float(t["_risk"])
    reach: Dict[float, dict] = {}
    peak, trough = 0.0, 0.0
    outcome, exit_n, exit_fav, exit_ts = "OTHER", None, 0.0, None
    last_close_r = 0.0
    n = 0
    for n, c in enumerate(candles[t["i"] + 1:], start=1):
        h, l = float(c["high"]), float(c["low"])
        fav = ((h - entry) if s > 0 else (entry - l)) / risk
        adv = ((l - entry) if s > 0 else (entry - h)) / risk
        last_close_r = (((float(c["close"]) - entry) if s > 0 else (entry - float(c["close"]))) / risk)
        if adv <= -1.0:
            outcome, exit_n, exit_fav, exit_ts = "SL", n, fav, int(c["ts"])
            trough = min(trough, adv)
            for m in reach.values():
                m["min_after"] = -1.0
            break
        for m in reach.values():
            m["min_after"] = min(m["min_after"], adv)
            m["max_after"] = max(m["max_after"], fav)
        trough = min(trough, adv)
        peak = max(peak, fav)
        for m in MILESTONES:
            if m not in reach and fav >= m:
                reach[m] = {"n": n, "ts": int(c["ts"]), "max_after": fav, "min_after": float("inf")}
        if fav >= tp_r:
            outcome, exit_n, exit_ts = "TP", n, int(c["ts"])
            break
    if outcome == "OTHER":
        exit_n = n
    final_r = -1.0 if outcome == "SL" else tp_r if outcome == "TP" else last_close_r
    ms: Dict[float, Optional[dict]] = {}
    for m in MILESTONES:
        r = reach.get(m)
        if r is None:
            ms[m] = None
            continue
        min_after = r["min_after"] if r["min_after"] != float("inf") else m
        ms[m] = {"reach_ts": r["ts"], "reach_bar": r["n"], "max_after": r["max_after"],
                 "min_after": min_after, "drawdown": max(0.0, m - min_after),
                 "bars_to_exit": exit_n - r["n"], "eventual": outcome}
    return {"outcome": outcome, "final_r": final_r, "mfe": peak, "mfe_incl_exit_candle": max(peak, exit_fav),
            "mae": -trough, "exit_ts": exit_ts, "exit_bar": exit_n, "sl_candle_touched_tp": outcome == "SL" and exit_fav >= tp_r,
            "milestones": ms}


def _avg(x: List[float]) -> float:
    return sum(x) / len(x) if x else 0.0


def _med(x: List[float]) -> float:
    if not x:
        return 0.0
    y = sorted(x)
    k = len(y) // 2
    return float(y[k]) if len(y) % 2 else (y[k - 1] + y[k]) / 2.0


def report(trades: List[dict], candles: Sequence[dict], csv_path: Optional[str] = None) -> str:
    rows = []
    for t in trades:
        d = diagnose_trade(t, candles)
        rows.append((t, d))
    N = len(rows)
    sl = [(t, d) for t, d in rows if d["outcome"] == "SL"]
    tp = [(t, d) for t, d in rows if d["outcome"] == "TP"]
    ot = [(t, d) for t, d in rows if d["outcome"] == "OTHER"]
    mism = sum(1 for t, d in rows if t.get("exit") in ("TP", "SL") and t["exit"] != d["outcome"])
    out: List[str] = []
    p = out.append
    p("=" * 78)
    p("PATH DIAGNOSTIC (same entries / SL 1.5 ATR / TP 3.0 ATR = +2.0R; nothing changed)")
    p(f"trades={N}  TP={len(tp)}  SL={len(sl)}  OTHER(still open at end of data)={len(ot)}")
    p(f"replay vs backtest exit mismatches: {mism}  (must be 0)")
    p("Path rule: same as the backtest -- SL wins any same-candle tie with TP; adverse extreme assumed")
    p("before favourable extreme inside a candle; nothing in an SL candle is credited as a milestone.")
    p("=" * 78)

    p("\nTABLE 1 -- by R threshold (touched on the chronological path)")
    hdr = f"{'Threshold':>9} {'Reached':>8} {'->TP':>6} {'->SL':>6} {'Other':>6} {'SL rate':>8} {'AvgPeakR':>9} {'AvgDD':>7}"
    p(hdr)
    for m in MILESTONES:
        g = [(t, d) for t, d in rows if d["milestones"][m]]
        if not g:
            p(f"{m:>8.2f}R {0:>8d}")
            continue
        n_tp = sum(1 for _, d in g if d["outcome"] == "TP")
        n_sl = sum(1 for _, d in g if d["outcome"] == "SL")
        n_ot = len(g) - n_tp - n_sl
        pk = _avg([d["milestones"][m]["max_after"] for _, d in g])
        dd = _avg([d["milestones"][m]["drawdown"] for _, d in g])
        p(f"{m:>8.2f}R {len(g):>8d} {n_tp:>6d} {n_sl:>6d} {n_ot:>6d} {100*n_sl/len(g):>7.1f}% {pk:>9.2f} {dd:>7.2f}")

    p("\nTABLE 2 -- COHORTS: reached the threshold, then still ended at SL")
    p(f"{'Reached':>8} {'Count':>6} {'%allTrades':>10} {'%allSL':>7} {'avgMFE':>7} {'avgMAE':>7} {'avgDD thr->SL':>13} {'avgDD peak->SL':>14} {'medBars':>8} {'avgBars':>8}")
    for m in (1.0, 1.25, 1.5, 1.75, 1.9):
        g = [(t, d) for t, d in sl if d["milestones"][m]]
        if not g:
            p(f"{m:>7.2f}R {0:>6d}")
            continue
        bars = [d["milestones"][m]["bars_to_exit"] for _, d in g]
        p(f"{m:>7.2f}R {len(g):>6d} {100*len(g)/N:>9.1f}% {100*len(g)/max(1,len(sl)):>6.1f}% "
          f"{_avg([d['mfe'] for _, d in g]):>7.2f} {_avg([d['mae'] for _, d in g]):>7.2f} "
          f"{m + 1.0:>13.2f} {_avg([d['mfe'] + 1.0 for _, d in g]):>14.2f} {_med(bars):>8.1f} {_avg(bars):>8.1f}")
    near = [x for x in sl if x[1]["milestones"][1.75]]
    vnear = [x for x in sl if x[1]["milestones"][1.9]]
    p(f"\nNEAR TP THEN SL      (>=1.75R then SL, never +2.0R): {len(near)}  = {100*len(near)/N:.1f}% of all trades, {100*len(near)/max(1,len(sl)):.1f}% of SL trades")
    p(f"VERY NEAR TP THEN SL (>=1.90R then SL, never +2.0R): {len(vnear)}  = {100*len(vnear)/N:.1f}% of all trades, {100*len(vnear)/max(1,len(sl)):.1f}% of SL trades")

    p("\nTABLE 3 -- SL trades by peak R reached BEFORE the SL (path-based)")
    edges = [(-9, .5, "<0.5R"), (.5, 1.0, "0.5-<1.0R"), (1.0, 1.25, "1.0-<1.25R"), (1.25, 1.5, "1.25-<1.5R"),
             (1.5, 1.75, "1.5-<1.75R"), (1.75, 1.9, "1.75-<1.9R"), (1.9, 2.0, "1.9-<2.0R"), (2.0, 99, ">=2.0R")]
    p(f"{'MFE bucket':>12} {'Count':>6} {'%allSL':>7}   {'Count incl. SL-candle high':>27}")
    for lo, hi, name in edges:
        c1 = sum(1 for _, d in sl if lo <= d["mfe"] < hi)
        c2 = sum(1 for _, d in sl if lo <= d["mfe_incl_exit_candle"] < hi)
        p(f"{name:>12} {c1:>6d} {100*c1/max(1,len(sl)):>6.1f}%   {c2:>27d}")
    amb = sum(1 for _, d in sl if d["sl_candle_touched_tp"])
    p(f">=2.0R check: {amb} SL trade(s) had an SL candle whose range ALSO reached +2.0R. Under the")
    p("current execution rule (SL first on a same-candle tie) they are correctly SL, not TP. A pre-exit")
    p("path can never reach +2.0R without already having exited at TP, so that bucket is 0 on the path.")

    p("\nFINAL FACTS")
    def cnt(m): return sum(1 for _, d in sl if d["milestones"][m])
    p(f" 1. SL trades that reached +1.0R first:  {cnt(1.0)}")
    p(f" 2. SL trades that reached +1.5R first:  {cnt(1.5)}")
    p(f" 3. SL trades that reached +1.75R first: {cnt(1.75)}")
    p(f" 4. SL trades that reached +1.9R first:  {cnt(1.9)}")
    p(f" 5. Reached +1.75R then reversed to SL:  {len(near)}")
    p(f" 6. Reached +1.9R then reversed to SL:   {len(vnear)}")
    p(f" 7. As % of all SL trades:  +1.75R: {100*len(near)/max(1,len(sl)):.1f}%   +1.9R: {100*len(vnear)/max(1,len(sl)):.1f}%")
    p(f" 8. As % of all trades:     +1.75R: {100*len(near)/N:.1f}%   +1.9R: {100*len(vnear)/N:.1f}%")
    for m, g in ((1.75, near), (1.9, vnear)):
        if g:
            p(f" 9. Drawdown from +{m}R back to SL: {m + 1.0:.2f}R (fixed: threshold to -1R); "
              f"from the trade's own peak to SL: avg {_avg([d['mfe'] + 1 for _, d in g]):.2f}R / median {_med([d['mfe'] + 1 for _, d in g]):.2f}R; "
              f"median bars threshold->SL: {_med([d['milestones'][m]['bars_to_exit'] for _, d in g]):.1f}")
        else:
            p(f" 9. Drawdown from +{m}R: no such trades")
    p(f"    SL trades that never got past +0.5R: {sum(1 for _, d in sl if d['mfe'] < 0.5)} "
      f"({100*sum(1 for _, d in sl if d['mfe'] < 0.5)/max(1,len(sl)):.1f}% of SL); "
      f"never past +1.0R: {sum(1 for _, d in sl if d['mfe'] < 1.0)} ({100*sum(1 for _, d in sl if d['mfe'] < 1.0)/max(1,len(sl)):.1f}% of SL)")

    if csv_path:
        import csv, datetime
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            head = ["entry_time_utc", "side", "entry", "atr", "sl", "tp", "outcome", "final_r", "mfe_r", "mae_r"]
            for m in MILESTONES:
                head += [f"m{m}_time_utc", f"m{m}_bar", f"m{m}_max_after", f"m{m}_min_after", f"m{m}_drawdown",
                         f"m{m}_bars_to_exit", f"m{m}_eventual"]
            w.writerow(head)
            for t, d in rows:
                row = [datetime.datetime.utcfromtimestamp(t["ts"]).isoformat(), t["side"], t["entry"], t["atr"],
                       t["sl"], t["tp"], d["outcome"], round(d["final_r"], 4), round(d["mfe"], 4), round(d["mae"], 4)]
                for m in MILESTONES:
                    x = d["milestones"][m]
                    if x:
                        row += [datetime.datetime.utcfromtimestamp(x["reach_ts"]).isoformat(), x["reach_bar"],
                                round(x["max_after"], 4), round(x["min_after"], 4), round(x["drawdown"], 4),
                                x["bars_to_exit"], x["eventual"]]
                    else:
                        row += [""] * 7
                w.writerow(row)
        p(f"\nper-trade rows written to {csv_path}")
    return "\n".join(out)
