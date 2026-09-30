"""Do trendline breaks carry information?  Tag every break with volume / squeeze /
momentum / space / structure features and measure the price move AFTER it, with
no entry, SL or TP logic.  Research only - never touches the engine.

  python scripts/feature_study.py --db research_binance.db --setup-tf 15m
  python scripts/feature_study.py --db research_binance.db --setup-tf 1h --csv breaks_1h.csv
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break.engine import TF_SEC  # noqa: E402
from src.trend_break import features as F  # noqa: E402


def load(tf):
    cur = db._connect().execute(
        "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
        (SYMBOL, tf))
    return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--setup-tf", default="15m", choices=["15m", "1h"])
    ap.add_argument("--horizons", default="1,4,16,64", help="forward horizons in setup-tf bars")
    ap.add_argument("--csv")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
        print(f"[db] using {db._DB_PATH}")
    hz = [int(x) for x in a.horizons.split(",")]
    c = load(a.setup_tf)
    c4 = load("4h")
    print(f"[data] {a.setup_tf}: {len(c)} candles, 4h: {len(c4)}", flush=True)
    rows = F.tag_breaks(c, TF_SEC[a.setup_tf], c4, horizons=hz)
    n = len(rows)
    print(f"\n=== {n} breaks on {a.setup_tf}  (SL 1.5 / TP 3.0 ATR race from the break close; coin flip = 33.3%)")
    tp = [r["tp_first"] for r in rows if r["tp_first"] is not None]
    print(f"    TP-first {sum(tp)}/{len(tp)} = {sum(tp) / max(1, len(tp)):.1%}")
    for h in hz:
        m, t = F.mean_t([r[f"fwd{h}"] for r in rows if r.get(f"fwd{h}") is not None])
        print(f"    mean move in break direction after {h:>3} bars: {m:+.3f} ATR  (t={t:+.2f})")

    print("\n=== Quantile tables: mean forward move in break direction (ATR, t) and TP-first rate")
    for f in F.FEATURES:
        tb = F.quantile_table(rows, f, hz)
        if not tb:
            continue
        print(f"\n-- {f}")
        print(f"   {'range':<22}{'n':>5}" + "".join(f"{'fwd' + str(h):>16}" for h in hz) + f"{'TP-first':>10}")
        for r in tb:
            cells = "".join(f"{r[f'fwd{h}'][0]:>+10.3f}({r[f'fwd{h}'][1]:+.1f})" for h in hz)
            print(f"   {r['lo']:>9.3f}..{r['hi']:<9.3f}{r['n']:>5}{cells}{r['tp'][0]:>10.1%}")

    if any(r.get("master4h") for r in rows):
        print("\n-- master4h (4H swing structure vs break direction)")
        for k in ("AGREE", "NEUTRAL", "OPPOSE"):
            g = [r for r in rows if r.get("master4h") == k]
            if not g:
                continue
            cells = "".join(f"  fwd{h}={F.mean_t([r[f'fwd{h}'] for r in g if r.get(f'fwd{h}') is not None])[0]:+.3f}" for h in hz)
            tpg = [r["tp_first"] for r in g if r["tp_first"] is not None]
            print(f"   {k:<8} n={len(g):<5}{cells}  TP-first={sum(tpg) / max(1, len(tpg)):.1%}")

    st = F.study(rows, hz)
    print(f"\n=== Significance gate: {st['tests']} tests, Bonferroni alpha = {st['alpha']:.5f}; "
          f"a feature only counts if p < alpha AND rho has the same sign in both halves of the year")
    print(f"   {'feature':<16}{'target':<9}{'n':>6}{'rho':>8}{'p':>10}{'rho_H1':>8}{'rho_H2':>8}  flag")
    for r in sorted(st["results"], key=lambda r: r["p"])[:15]:
        print(f"   {r['feature']:<16}{r['target']:<9}{r['n']:>6}{r['rho']:>+8.3f}{r['p']:>10.4f}"
              f"{r['rho_h1']:>+8.3f}{r['rho_h2']:>+8.3f}  {'PASS' if r['flag'] else '-'}")
    passed = [r for r in st["results"] if r["flag"]]
    print(f"\nRESULT: {len(passed)} of {len(st['results'])} feature/target pairs pass the gate.")
    if not passed:
        print("        No feature predicts what happens after a break beyond chance.")
    if a.csv:
        cols = ["ts", "side", "atr"] + F.FEATURES + ["master4h", "tp_first"] + [f"fwd{h}" for h in hz]
        with open(a.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow([r.get(k) for k in cols])
        print(f"[csv] {a.csv}")


if __name__ == "__main__":
    main()
