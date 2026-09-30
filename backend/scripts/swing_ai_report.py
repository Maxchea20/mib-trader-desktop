"""Swing AI paper-trading report: does the AI have an edge after fees?  Read-only.

  python scripts/swing_ai_report.py
  python scripts/swing_ai_report.py --db path/to/market_data_clean.db
"""
import argparse
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.swing_ai import paper, store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
    dec = store.decisions(100000)
    entry = [d for d in dec if d["kind"] == "ENTRY"]
    print(f"decisions: {len(entry)} entry reviews, {sum(1 for d in dec if d['kind'] == 'MANAGE')} management reviews")
    c = Counter(d["decision"] for d in entry)
    print("  entry outcomes: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    rej = [d for d in entry if d["decision"] in ("LONG", "SHORT") and not d["risk_ok"]]
    print(f"  proposals rejected by the risk layer: {len(rej)}")
    for reason, n in Counter(r.strip() for d in rej for r in (d["risk_reasons"] or "").split(";") if r.strip()).most_common(6):
        print(f"      {n:>4}  {reason}")
    errs = [d for d in dec if d["error"]]
    print(f"  AI/schema errors (treated as NO_TRADE / HOLD): {len(errs)}")
    pos = store.positions()
    perf = paper.performance(pos)
    print(f"\npositions: {len(pos)} total ({sum(1 for p in pos if p['status'] == 'CLOSED')} closed, "
          f"{sum(1 for p in pos if p['status'] in ('OPEN', 'PENDING'))} active, "
          f"{sum(1 for p in pos if p['status'] in ('CANCELLED', 'EXPIRED'))} never filled)")
    if not perf["trades"]:
        print("no closed trades yet")
        return
    print(f"  trades {perf['trades']}   win rate {perf['win_rate']:.1%}   profit factor {perf['profit_factor']:.2f}")
    print(f"  net R {perf['net_r']:+.2f} (gross {perf['gross_r']:+.2f})   expectancy {perf['expectancy_r']:+.3f} R/trade   t-stat {perf['t_stat']:+.2f}")
    print(f"  avg MFE {perf['avg_mfe_r']:.2f} R   avg MAE {perf['avg_mae_r']:.2f} R   fees ${perf['fees_usd']:.2f}   exits {perf['exits']}")
    n = perf["trades"]
    sd = abs(perf["expectancy_r"]) / abs(perf["t_stat"]) * math.sqrt(n) if perf["t_stat"] else 1.3
    need = int(((1.96 * sd) / 0.2) ** 2) + 1
    print(f"\nEvidence: about {need} closed trades are needed to detect +0.20 R/trade at this variance; you have {n}. "
          "Below that, treat any result as noise.")


if __name__ == "__main__":
    main()
