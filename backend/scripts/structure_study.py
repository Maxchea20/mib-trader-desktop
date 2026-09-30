"""'Trade with the trend on a CHoCH / BOS' study (research only; no orders, no engine).

  python scripts/structure_study.py --db research_2022_25.db
  python scripts/structure_study.py --db research_2019_21.db     # hold-out
Events: 15m and 1h, swing length 5 and 10 (fixed in advance).  'WITH' = the event direction
equals the 4H swing-structure trend.  Only the BOS-WITH and CHoCH-WITH groups are gated
(2 types x 2 timeframes x 2 swing lengths = 8 tests, Bonferroni).  A group passes when its TP-first
(SL 1.5 / TP 3.0 ATR from the event close) beats the same-direction baseline AND both sides and both
halves of the data beat it.  Other groups are printed for information only."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break import structure as S, sweep as W  # noqa: E402

TF_SEC = {"15m": 900, "1h": 3600}


def load(tf):
    cur = db._connect().execute(
        "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts", (SYMBOL, tf))
    return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur]


def show(name, res, gated, n_variants, hz):
    j = W.judge(res, n_variants=n_variants)
    fee = sorted(res["fee_atr"])[len(res["fee_atr"]) // 2] if res["fee_atr"] else 0.0
    w, n, p0, z, p = j["tp"]
    tag = "GATED" if gated else "info "
    print(f"  [{tag}] {name:<18} {j['n_raw']:>5} events, {j['n_used']:>4} used | TP-first {w}/{n} = {w / max(1, n):.1%} "
          f"vs baseline {p0:.1%} (z={z:+.2f}, p={p:.4f}) | fee {fee:.2f} ATR")
    for lab, d in (("LONG", 1), ("SHORT", -1)):
        r = j["full"][d]
        print(f"           {lab:<5} n={r['n']:<4} TP {r['tp_rate']:.1%} vs {r['tp_base']:.1%}   "
              + "  ".join(f"ex{h}={r[f'ex{h}'][0]:+.2f}(t={r[f'ex{h}'][1]:+.1f})" for h in hz))
    print(f"           halves: " + "   ".join(f"{nm} {lab} {half[d]['tp_rate']:.1%} vs {half[d]['tp_base']:.1%}"
          for nm, half in (("H1", j["h1"]), ("H2", j["h2"])) for lab, d in (("L", 1), ("S", -1))))
    return j["pass"] and gated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--tfs", default="15m,1h")
    ap.add_argument("--lrs", default="5,10")
    ap.add_argument("--horizons", default="1,4,16,64")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
        print(f"[db] using {db._DB_PATH}")
    hz = [int(x) for x in a.horizons.split(",")]
    c4 = load("4h")
    tfs = a.tfs.split(",")
    lrs = [int(x) for x in a.lrs.split(",")]
    n_var = 2 * len(tfs) * len(lrs)
    passed = []
    for tf in tfs:
        c = load(tf)
        print(f"\n[data] {tf}: {len(c)} candles, 4h: {len(c4)}", flush=True)
        for lr in lrs:
            res = S.tag_structure(c, c4, lr, hz, tf_sec=TF_SEC[tf])
            print(f"\n=== {tf}, swing length {lr}: {len(res['events'])} CHoCH/BOS events")
            for typ in ("BOS", "CHoCH"):
                for trend, gated in (("WITH", True), ("AGAINST", False), (None, False)):
                    name = f"{typ}-{trend or 'ALL'}"
                    g = S.group(res, lambda e, typ=typ, trend=trend: e["type"] == typ and (trend is None or e["trend"] == trend))
                    if show(name, g, gated, n_var, hz):
                        passed.append(f"{tf}/lr{lr}/{typ}-WITH")
    print(f"\nRESULT: {'PASS: ' + ', '.join(passed) if passed else 'no gated group passes - trading with the trend on CHoCH/BOS shows no edge here.'}")


if __name__ == "__main__":
    main()
