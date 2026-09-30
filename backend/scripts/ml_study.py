"""Let a model search ALL the data together for a setup (research only; no orders, no engine).

  python scripts/ml_study.py --db research_2019_21.db research_2022_25.db research_binance.db
  python scripts/ml_study.py --db research_2019_21.db research_2022_25.db research_binance.db --final

Every 15m bar is a decision point; features come from all timeframes (5m, 15m, 30m, 1h, 4h, 1d) at that
bar's close.  Labels: would a LONG / SHORT entered there hit +3 ATR before -1.5 ATR.  A gradient-boosted
model is trained walk-forward (only on the past, with an embargo) and scored on the future it never saw.
GATE (fixed in advance), on the walk-forward period: >= 200 trades, win rate above the fee-adjusted
break-even with one-sided p < 0.01, positive net R after fees, and >= 60% of quarters net positive.
The last --sealed-days stay sealed; --final opens them ONCE, and only if the gate passed."""
import argparse
import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import SYMBOL  # noqa: E402
from src.trend_break import ml  # noqa: E402


def line(s):
    print(s, flush=True)


def fmt(sm):
    if not sm.get("n"):
        return "no trades"
    return (f"n={sm['n']}  win {sm['rate']:.1%} (break-even {sm['breakeven']:.1%}, p={sm['p']:.4f})  "
            f"net {sm['net_r']:+.1f}R ({sm['net_per_trade']:+.3f}R/trade)  PF {sm['pf']:.2f}  fee {sm['fee_r']:.2f}R")


def by_quarter(trades):
    q = {}
    for t in trades:
        k = pd.to_datetime(t["ts"], unit="s").to_period("Q").strftime("%Y-Q%q")
        q.setdefault(k, []).append(t)
    return {k: ml.summarize(v) for k, v in sorted(q.items())}


def gate(trades):
    sm = ml.summarize(trades)
    qs = by_quarter(trades)
    pos = sum(1 for v in qs.values() if v["n"] and v["net_r"] > 0)
    frac = pos / len(qs) if qs else 0.0
    ok = bool(sm["n"] >= 200 and sm["rate"] > sm["breakeven"] and sm["p"] < 0.01 and sm["net_r"] > 0 and frac >= 0.6)
    return ok, sm, qs, frac


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", nargs="+", required=True)
    ap.add_argument("--sealed-days", type=int, default=365)
    ap.add_argument("--first-test-days", type=int, default=365)
    ap.add_argument("--fold-days", type=int, default=91)
    ap.add_argument("--inputs", choices=["features", "raw"], default="features",
                    help="features = multi-timeframe indicators; raw = only raw normalised candles, the model builds its own")
    ap.add_argument("--model", choices=["gbm", "tree"], default="gbm",
                    help="gbm = black-box booster; tree = small decision tree whose leaves are readable IF-THEN rules (the setup)")
    ap.add_argument("--stride", type=int, default=1, help="use every Nth 15m bar as a decision point (saves memory)")
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    frames = {tf: ml.load_tf(a.db, SYMBOL, tf) for tf in ml.TFS}
    line("[data] " + "  ".join(f"{tf}:{len(d)}" for tf, d in frames.items()))
    data = ml.build_dataset(frames, inputs=a.inputs, stride=a.stride)
    ts = data["ts"]
    n_feat = data["X"].shape[1]
    line(f"[dataset] {len(ts)} decision bars x {n_feat} features, built in {time.time() - t0:.0f}s "
         f"({pd.to_datetime(ts[0], unit='s').date()} -> {pd.to_datetime(ts[-1], unit='s').date()})")
    sealed_start = int(ts[-1]) - a.sealed_days * 86400
    first_test = int(ts[0]) + a.first_test_days * 86400
    line(f"[plan] walk-forward tests {pd.to_datetime(first_test, unit='s').date()} -> "
         f"{pd.to_datetime(sealed_start, unit='s').date()}; sealed from {pd.to_datetime(sealed_start, unit='s').date()}")
    oos = ml.walk_forward(data, first_test, sealed_start, a.fold_days, log=line, kind=a.model)

    m = (ts >= first_test) & (ts < sealed_start)
    base_rate = {s: float(np.nanmean(np.where(m, data["y"][s], np.nan))) for s in (1, -1)}
    line(f"\n=== Out-of-sample (walk-forward) results")
    line(f"    unconditional win rate: LONG {base_rate[1]:.1%}  SHORT {base_rate[-1]:.1%}   (fee-free coin flip = 33.3%)")
    for s, nm in ((1, "LONG"), (-1, "SHORT")):
        line(f"    AUC {nm}: {ml.auc(np.where(m, data['y'][s], np.nan), oos[s]):.4f}   (0.500 = no information)")
    line("\n    Do the model's probabilities sort the outcomes? (both sides pooled, deciles of predicted p)")
    ps, ys = [], []
    for s in (1, -1):
        k = m & ~np.isnan(oos[s]) & ~np.isnan(data["y"][s])
        ps.append(oos[s][k])
        ys.append(data["y"][s][k])
    ps, ys = np.concatenate(ps), np.concatenate(ys)
    if len(ps) >= 1000:
        qs_ = np.quantile(ps, np.linspace(0, 1, 11))
        for d in range(10):
            sel = (ps >= qs_[d]) & (ps <= qs_[d + 1] if d == 9 else ps < qs_[d + 1])
            line(f"      decile {d + 1:>2}: predicted p {ps[sel].mean():.3f}  realised win rate {ys[sel].mean():.1%}  (n={sel.sum()})")
    line(f"\n    Trading rule (fixed in advance): take the most confident side when p >= {ml.THRESH_P}, one position at a time")
    trades = ml.simulate(data, oos, first_test, sealed_start)
    ok, sm, qs, frac = gate(trades)
    line("    " + fmt(sm))
    for thr in (0.36, 0.38, 0.42, 0.45):
        line(f"      [info] p >= {thr}: " + fmt(ml.summarize(ml.simulate(data, oos, first_test, sealed_start, thr))))
    line("    per quarter (net R): " + "  ".join(f"{k}:{v['net_r']:+.1f}({v['n']})" for k, v in qs.items()))
    line(f"    quarters net positive: {frac:.0%}")
    if oos.get("imp") is not None:
        top = oos["imp"].sort_values(ascending=False).head(12)
        line("\n    most used features (last fold): " + ", ".join(f"{k}" for k in top.index))
    if a.model == "tree":
        line("\n=== THE SETUP THE AI LEARNED (last fold, trained on all data before it; leaves with training win rate >= 40%)")
        for s_, nm in ((1, "LONG"), (-1, "SHORT")):
            for r in oos["rules"][s_][:6]:
                line(f"    {nm}: IF {r['rule']}  ->  trained win rate {r['p_train']:.1%} on {r['n_train']} bars")
    line(f"\nWALK-FORWARD RESULT: {'PASS' if ok else 'no edge found'}")
    if not a.final:
        line("[sealed] the last period was not opened." + (" Run again with --final to open it once." if ok else ""))
        return
    if not ok:
        line("[sealed] --final ignored: the walk-forward gate did not pass, so the sealed period stays sealed.")
        return
    tr = np.where(ts < sealed_start - ml.EMBARGO)[0]
    te = np.where(ts >= sealed_start)[0]
    fin = {1: np.full(len(ts), np.nan), -1: np.full(len(ts), np.nan)}
    for s in (1, -1):
        lab = ~np.isnan(data["y"][s][tr])
        mdl = ml._model(a.model).fit(data["X"].iloc[tr[lab]], data["y"][s][tr][lab].astype(int))
        fin[s][te] = mdl.predict_proba(data["X"].iloc[te])[:, 1]
    ft = ml.simulate(data, fin, sealed_start, int(ts[-1]) + 1)
    fsm = ml.summarize(ft)
    line("\n=== SEALED PERIOD (opened once): " + fmt(fsm))
    okf = bool(fsm.get("n", 0) >= 30 and fsm["rate"] > fsm["breakeven"] and fsm["p"] < 0.05 and fsm["net_r"] > 0)
    line(f"SEALED RESULT: {'PASS' if okf else 'FAIL - the walk-forward result did not hold up'}")


if __name__ == "__main__":
    main()
