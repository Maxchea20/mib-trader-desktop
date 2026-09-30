"""Scalping study (research only; no orders, no engine): 9/21 EMA cross + RSI, and VWAP cross, on 1m / 5m.

  python scripts/scalp_study.py --db research_2022_25.db
  python scripts/scalp_study.py --db research_2019_21.db          # hold-out
Targets 0.1 / 0.2 / 0.3 %, stop = target, max hold 30 bars (1m) / 12 bars (5m); fixed in advance.
Fees: taker 0.020% per side (0.04% round trip) and the maker-both-sides upper bound (0%).  12 tests
(2 signals x 2 timeframes x 3 targets), Bonferroni.  A group passes when its edge over entering on ANY
bar in the same direction is significant, positive for both sides and both halves of the data, and still
positive after the fee."""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break import scalp as S  # noqa: E402


def load(tf):
    cur = db._connect().execute(
        "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts", (SYMBOL, tf))
    return pd.DataFrame(cur.fetchall(), columns=["ts", "open", "high", "low", "close", "volume"]).astype(float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--tfs", default="1m,5m")
    ap.add_argument("--taker-rt", type=float, default=S.TAKER_RT)
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
        print(f"[db] using {db._DB_PATH}")
    tfs = a.tfs.split(",")
    n_tests = 2 * len(tfs) * len(S.TARGETS)
    tp_pass, mk_pass = [], []
    for tf in tfs:
        df = load(tf)
        print(f"\n[data] {tf}: {len(df)} candles", flush=True)
        H = S.HOLD[tf]
        hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
        sigs = S.signals(df)
        for T in S.TARGETS:
            R = {1: S.race_r(hi, lo, cl, T, H, 1), -1: S.race_r(hi, lo, cl, T, H, -1)}
            print(f"\n=== {tf}, target/stop {T:.1%}, max hold {H} bars | fee = {a.taker_rt / T:.2f} R (taker round trip)  "
                  f"| any-bar baseline: LONG {np.nanmean(R[1]):+.3f}R  SHORT {np.nanmean(R[-1]):+.3f}R")
            for name, sg in sigs.items():
                j = S.judge(sg, R, T, H, n_tests, a.taker_rt)
                if j["n"] < 30:
                    print(f"  {name:<12} too few events ({j['n']})")
                    continue
                p = j["parts"]
                print(f"  {name:<12} {j['n']:>6} trades | win {j['win']:.1%} | gross {j['gross']:+.3f}R  edge over baseline {j['excess']:+.3f}R "
                      f"(t={j['t']:+.2f}, p={j['p']:.4f}) | NET taker {j['net_taker']:+.3f}R  maker-upper-bound {j['net_maker']:+.3f}R")
                print(f"               sides/halves edge: LONG {p['LONG']['excess']:+.3f} SHORT {p['SHORT']['excess']:+.3f} "
                      f"H1 {p['H1']['excess']:+.3f} H2 {p['H2']['excess']:+.3f}  -> "
                      f"{'TAKER PASS' if j['pass_taker'] else ('MAKER-ONLY PASS' if j['pass_maker'] else 'no edge')}")
                if j["pass_taker"]:
                    tp_pass.append(f"{tf}/{name}/{T:.1%}")
                elif j["pass_maker"]:
                    mk_pass.append(f"{tf}/{name}/{T:.1%}")
    print("\nRESULT: " + ("TAKER PASS: " + ", ".join(tp_pass) if tp_pass else "no group is profitable with taker fees")
          + ("   |   maker-only (needs guaranteed limit fills): " + ", ".join(mk_pass) if mk_pass else ""))


if __name__ == "__main__":
    main()
