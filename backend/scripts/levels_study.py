"""Support / resistance study (research only; no orders, no engine).

  python scripts/levels_study.py --db research_2022_25.db
  python scripts/levels_study.py --db research_2019_21.db      # hold-out
Levels = clusters of confirmed swing pivots (5-bar, tolerance 0.3 ATR, at least 2 touches).  Events:
REJECT (touch and close back away, as a rejection candle) and RETEST (a broken level touched again from
the far side and holding), on 15m and 1h.  4 gated tests (2 types x 2 timeframes, Bonferroni); a group passes
when TP-first (SL 1.5 / TP 3.0 ATR from the event close) beats the same-direction baseline AND both sides
and both halves of the data beat it.  The 3+ touches subset is printed for information only."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break import levels as L, sweep as W  # noqa: E402
from src.trend_break.structure import group  # noqa: E402


def load(tf):
    cur = db._connect().execute(
        "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts", (SYMBOL, tf))
    return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur]


def show(name, res, gated, n_var, hz):
    j = W.judge(res, n_variants=n_var)
    fee = sorted(res["fee_atr"])[len(res["fee_atr"]) // 2] if res["fee_atr"] else 0.0
    w, n, p0, z, p = j["tp"]
    print(f"  [{'GATED' if gated else 'info '}] {name:<14} {j['n_raw']:>5} events, {j['n_used']:>4} used | TP-first {w}/{n} = "
          f"{w / max(1, n):.1%} vs baseline {p0:.1%} (z={z:+.2f}, p={p:.4f}) | fee {fee:.2f} ATR")
    for lab, d in (("LONG", 1), ("SHORT", -1)):
        r = j["full"][d]
        print(f"           {lab:<5} n={r['n']:<4} TP {r['tp_rate']:.1%} vs {r['tp_base']:.1%}   "
              + "  ".join(f"ex{h}={r[f'ex{h}'][0]:+.2f}(t={r[f'ex{h}'][1]:+.1f})" for h in hz))
    print("           halves: " + "   ".join(f"{nm} {lab} {half[d]['tp_rate']:.1%} vs {half[d]['tp_base']:.1%}"
          for nm, half in (("H1", j["h1"]), ("H2", j["h2"])) for lab, d in (("L", 1), ("S", -1))))
    return gated and j["pass"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--tfs", default="15m,1h")
    ap.add_argument("--horizons", default="1,4,16,64")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
        print(f"[db] using {db._DB_PATH}")
    hz = [int(x) for x in a.horizons.split(",")]
    tfs = a.tfs.split(",")
    n_var = 2 * len(tfs)
    passed = []
    for tf in tfs:
        c = load(tf)
        res = L.tag_levels(c, hz)
        print(f"\n=== {tf}: {len(c)} candles, {len(res['events'])} level events")
        for typ in ("REJECT", "RETEST"):
            if show(typ, group(res, lambda e, typ=typ: e["type"] == typ), True, n_var, hz):
                passed.append(f"{tf}/{typ}")
            show(typ + " 3+touches", group(res, lambda e, typ=typ: e["type"] == typ and e["touches"] >= 3), False, n_var, hz)
    print(f"\nRESULT: {'PASS: ' + ', '.join(passed) if passed else 'no gated group passes - support/resistance shows no edge here.'}")


if __name__ == "__main__":
    main()
