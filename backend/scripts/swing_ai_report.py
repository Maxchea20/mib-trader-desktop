"""Swing AI paper-trading report: can GPT analyse the live MEXC data and produce useful swing positions?  Read-only.

  python scripts/swing_ai_report.py
  python scripts/swing_ai_report.py --db path/to/market_data_clean.db
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.swing_ai import analytics  # noqa: E402


def line(name, p):
    if not p.get("trades"):
        return f"  {name:<16} no closed trades"
    return (f"  {name:<16} trades {p['trades']:>4}  win {p['win_rate']:.0%}  net {p['net_r']:+.2f}R  avg {p['expectancy_r']:+.3f}R  "
            f"PF {p['profit_factor']:.2f}  MFE {p['avg_mfe_r']:.2f}  MAE {p['avg_mae_r']:.2f}  fees ${p['fees_usd']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    a = ap.parse_args()
    if a.db:
        db._DB_PATH = os.path.abspath(a.db)
        os.environ["MARKET_DB_PATH"] = db._DB_PATH
    r = analytics.report()
    f, t = r["frequency"], r["trades"]
    print(f"decision frequency: {f['entry_reviews']} entry reviews  LONG {f['LONG']}  SHORT {f['SHORT']}  NO_TRADE {f['NO_TRADE']}"
          + (f"  (NO_TRADE {f['no_trade_share']:.0%})" if f["no_trade_share"] is not None else ""))
    print(f"  rejected by the safety layer: {f['proposals_rejected_by_safety']}   AI/schema errors: {f['ai_errors']}"
          + (f"   proposals/day {f['proposals_per_day']:.2f}" if f["proposals_per_day"] else ""))
    u = r["usage"]
    for k, name in (("today", "today"), ("all_time", "all time")):
        x = u[k]
        cost = f"  ≈ ${x['cost_usd']:.2f}" if x["cost_usd"] is not None else ""
        print(f"AI usage {name}: {x['calls']} calls, {x['input_tokens']:,} input tokens ({x['cached_tokens']:,} cached), {x['output_tokens']:,} output{cost}")
    if not u["prices_set"]:
        print("  (set SWING_AI_PRICE_IN / SWING_AI_PRICE_OUT [/ SWING_AI_PRICE_CACHED] in .env, USD per 1M tokens, to see dollars)")
    print(f"trades: {t['total']} total, {t['closed']} closed, {t['active']} active, {t['never_filled']} never filled\n")
    print("performance (net of MEXC fees):")
    print(line("ALL", r["overall"]))
    for k, v in r["by_side"].items():
        print(line(k, v))
    if r["by_market_state"]:
        print("by the AI's own market_state:")
        for k, v in r["by_market_state"].items():
            print(line(k, v))
    if r["confidence_calibration"]:
        print("confidence calibration (does higher confidence win more?):")
        for c in r["confidence_calibration"]:
            print(f"  {c['confidence_bin']}  trades {c['trades']:>3}  mean conf {c['mean_confidence']:.2f}  win {c['win_rate']:.0%}  mean net R {c['mean_net_r']:+.2f}")
    o = r["overall"]
    if o.get("trades"):
        sd = abs(o["expectancy_r"]) / abs(o["t_stat"]) * math.sqrt(o["trades"]) if o["t_stat"] else 1.3
        need = int(((1.96 * sd) / 0.2) ** 2) + 1
        print(f"\nt-stat {o['t_stat']:+.2f}. About {need} closed trades are needed to detect +0.20 R/trade at this variance; you have {o['trades']}. "
              "Below that, treat any result as noise.")


if __name__ == "__main__":
    main()
