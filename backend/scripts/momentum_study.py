"""Long-horizon momentum study (research only; no orders, no engine).

  python scripts/momentum_study.py --db research_2022_25.db --tf 1h
  python scripts/momentum_study.py --db research_2022_25.db --tf 4h
Look-backs 24/48/96 bars and horizons 16/64/128 bars are fixed in advance.  Every number is
the move in the signal direction MINUS what price does after any bar in that direction (BTC's
own drift).  PASS = 64-bar excess > 0 with p < 0.05/3, positive for LONG and SHORT and both
halves of the data, and still positive after the round-trip fee."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break import momentum as M  # noqa: E402


def load(tf):
    cur = db._connect().execute(
        "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts", (SYMBOL, tf))
    return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in cur]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--tf", default="1h", choices=["1h", "4h"])
    ap.add_argument("--lookbacks", default="24,48,96")
    ap.add_argument("--horizons", default="16,64,128")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
        print(f"[db] using {db._DB_PATH}")
    hz = [int(x) for x in a.horizons.split(",")]
    c = load(a.tf)
    print(f"[data] {a.tf}: {len(c)} candles", flush=True)
    passed = []
    lbs = [int(x) for x in a.lookbacks.split(",")]
    for lb in lbs:
        j = M.judge(M.tag_momentum(c, lb, hz), n_variants=len(lbs))
        w, n, bw = j["tp"]
        print(f"\n=== {a.tf} momentum breakout, look-back {lb} bars: {j['n_raw']} events, {j['n_used']} after de-clustering"
              f"  (median round-trip fee = {j['fee_atr']:.2f} ATR)")
        print(f"    excess move in signal direction over the same-direction baseline, ATR (t) - horizons {hz}")
        for name in ("all", "LONG", "SHORT", "half1", "half2"):
            r = j["rows"][name]
            print(f"    {name:<6}" + "  ".join(f"h{h}: {r[h][0]:+.3f} (t={r[h][1]:+.1f}, n={r[h][2]})" for h in hz))
        p = j["primary"]
        print(f"    64-bar excess {p['ex']:+.3f} ATR  t={p['t']:+.2f}  p={p['p']:.4f}   after fee {p['net']:+.3f} ATR")
        print(f"    [secondary] wide-stop race (SL 3 / TP 6 ATR): TP-first {w}/{n} = {w / max(1, n):.1%} vs baseline {bw:.1%}")
        print(f"    RESULT look-back {lb}: {'PASS' if j['pass'] else 'no edge'}   (LONG, SHORT and both halves positive: {j['consistent']})")
        if j["pass"]:
            passed.append(lb)
    print(f"\nRESULT: {'PASS for look-back ' + str(passed) if passed else 'no look-back passes the gate - no momentum edge here.'}")


if __name__ == "__main__":
    main()
