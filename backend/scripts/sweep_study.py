"""Sweep-and-reject study (research only; no orders, no engine).

  python scripts/sweep_study.py --db research_2022_25.db --tf 1h
  python scripts/sweep_study.py --db research_2022_25.db --tf 15m
Look-backs 24/48/96 bars are fixed in advance; nothing is tuned.  PASS needs
TP-first above the same-direction baseline (Bonferroni over the 3 look-backs)
AND both sides AND both halves of the data beating that baseline."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break import sweep as W  # noqa: E402
from src.trend_break.features import mean_t  # noqa: E402


def load(tf):
    cur = db._connect().execute(
        "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts", (SYMBOL, tf))
    return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--tf", default="1h", choices=["15m", "1h", "4h"])
    ap.add_argument("--lookbacks", default="24,48,96")
    ap.add_argument("--horizons", default="1,4,16,64")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
        print(f"[db] using {db._DB_PATH}")
    hz = [int(x) for x in a.horizons.split(",")]
    c = load(a.tf)
    print(f"[data] {a.tf}: {len(c)} candles", flush=True)
    passed = []
    for lb in [int(x) for x in a.lookbacks.split(",")]:
        res = W.tag_sweeps(c, lb, hz)
        j = W.judge(res)
        ev = res["events"]
        fee = sorted(res["fee_atr"])[len(res["fee_atr"]) // 2] if res["fee_atr"] else 0.0
        w, n, p0, z, p = j["tp"]
        print(f"\n=== {a.tf} sweep-and-reject, look-back {lb} bars: {len(ev)} events, {j["n_used"]} after de-clustering (median round-trip fee = {fee:.2f} ATR)")
        print(f"    TP-first {w}/{n} = {w / max(1, n):.1%}   same-direction baseline {p0:.1%}   z={z:+.2f}  p={p:.4f}")
        for name, d in (("LONG (low swept)", 1), ("SHORT (high swept)", -1)):
            r = j["full"][d]
            print(f"    {name:<20} n={r['n']:<5} TP-first {r['tp_rate']:.1%} vs base {r['tp_base']:.1%} (z={r['tp_z']:+.2f})   "
                  + "  ".join(f"ex{h}={r[f'ex{h}'][0]:+.3f}(t={r[f'ex{h}'][1]:+.1f})" for h in hz))
        for nm, half in (("first half", j["h1"]), ("second half", j["h2"])):
            print(f"    {nm:<12} " + "   ".join(f"{'LONG' if d == 1 else 'SHORT'} n={half[d]['n']} TP {half[d]['tp_rate']:.1%} vs {half[d]['tp_base']:.1%}" for d in (1, -1)))
        print(f"    RESULT look-back {lb}: {'PASS' if j['pass'] else 'no edge'}"
              f"  (both sides and both halves beat baseline: {j['consistent']})")
        if j["pass"]:
            passed.append(lb)
        # exploratory (not gated): does a bigger wick or more volume help?
        for key in ("wick_atr", "vol_ratio"):
            pts = sorted([e for e in ev if e.get(key) is not None and e["tp_first"] is not None], key=lambda e: e[key])
            q = 5 if len(pts) >= 200 else 3
            cells = []
            for k in range(q):
                g = pts[k * len(pts) // q:(k + 1) * len(pts) // q]
                if g:
                    cells.append(f"{g[0][key]:.2f}..{g[-1][key]:.2f}: {sum(e['tp_first'] for e in g) / len(g):.1%}")
            print(f"    [exploratory] TP-first by {key}: " + " | ".join(cells))
    print(f"\nRESULT: {'PASS for look-back ' + str(passed) if passed else 'no look-back passes the gate - sweep-and-reject shows no edge here.'}")


if __name__ == "__main__":
    main()
