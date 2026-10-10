#!/usr/bin/env python3
"""Fade a failed 5-minute retest, and take the same signal with the break.

Same 31 dates, same V3 costs. Does not change the V3 or rule B results.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_spec = importlib.util.spec_from_file_location(
    "run_orb_v3_backtest",
    Path(__file__).resolve().parent / "run_orb_v3_backtest.py",
)
_runner = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_runner)

from src.orb.audit import load_bars, resolve_market_db
from src.orb.retest_fail import run_retest_fail_backtest
from src.orb.v3 import survey_sessions, v3_primary_config


def _run(direction, bars_15, bars_5, bars_1, groups, config):
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_retest_fail_backtest(
                bars_15, bars_5, bars_1, config,
                direction=direction, entry_dates=set(days), split_name=name,
            )
        )
    return rows


def _line(stats, name):
    row = stats[name]
    net = row["net_pnl"]
    trades = row["closed_trades"]
    win = row["win_rate"]
    win_s = "n/a" if win is None else f"{100.0 * win:.1f}%"
    return f"| {name} | {trades} | {win_s} | {net:,.2f} | {row['fees']:,.2f} | {row['average_r'] if row['average_r'] is not None else float('nan'):.2f} |"


def main() -> int:
    saved = json.loads((BACKEND / "orb_v3_results" / "summary.json").read_text())
    path = resolve_market_db(None) or resolve_market_db("/tmp/market_data_clean.db")
    if path is None:
        print(json.dumps({"error": "no db"}))
        return 2
    bars_15 = load_bars(path, "BTC_USDT", "15m")
    bars_5 = load_bars(path, "BTC_USDT", "5m")
    bars_1 = load_bars(path, "BTC_USDT", "1m")
    sessions = survey_sessions(bars_15, bars_5, bars_1)
    tradable = sorted(date.fromisoformat(row["ny_date"]) for row in sessions if row["tradable"])
    groups = _runner._splits(tradable)
    got = {name: [day.isoformat() for day in days] for name, days in groups.items()}
    if got != saved["survey_counts"]["split_dates"]:
        print(json.dumps({"error": "dates differ"}))
        return 1
    config = v3_primary_config()
    fade_rows = _run("fade", bars_15, bars_5, bars_1, groups, config)
    with_rows = _run("with", bars_15, bars_5, bars_1, groups, config)
    fade_stats = _runner._closed_stats(fade_rows)
    with_stats = _runner._closed_stats(with_rows)
    def reasons(rows):
        out = {}
        for row in rows:
            if row.get("status") == "unfilled":
                key = row.get("unfilled_reason")
                out[key] = out.get(key, 0) + 1
        return out
    lines = [
        "# Failed 5-minute retest, both directions",
        "",
        "Simulated OHLC fills. Not exchange prints. No live order. No parameter was searched.",
        "",
        "Same 15-minute range and same first 5-minute body breakout as V3. A failure is the first later 5-minute candle that closes back through that level before 11:00. A wick is not a failure. The fill is the next 1-minute open. Stop, target, fees, slippage, spread, and funding are the V3 preset.",
        "",
        "Fade trades against the break: failed upside break is a short, failed downside break is a long. With takes the original break direction on that same candle.",
        "",
        f"Eligible sessions: {len(tradable)}. Same dates as the preserved V3 run.",
        "",
        "## Short the failed retest (fade)",
        "",
        "| Split | Closed | Win rate | Net | Fees | Avg R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        lines.append(_line(fade_stats, name))
    lines += [
        "",
        "## Long the original break anyway (with)",
        "",
        "| Split | Closed | Win rate | Net | Fees | Avg R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        lines.append(_line(with_stats, name))
    fade_n = sum(fade_stats[name]["closed_trades"] for name in fade_stats)
    with_n = sum(with_stats[name]["closed_trades"] for name in with_stats)
    reading = (
        f"Fade closed {fade_n} trades and netted "
        f"{fade_stats['train']['net_pnl']:.2f} / {fade_stats['validation']['net_pnl']:.2f} / {fade_stats['out_of_sample']['net_pnl']:.2f}. "
        f"Taking the original direction on the same failure candles closed {with_n} trades and netted "
        f"{with_stats['train']['net_pnl']:.2f} / {with_stats['validation']['net_pnl']:.2f} / {with_stats['out_of_sample']['net_pnl']:.2f}. "
        "These are the same signals with the side flipped. A gain on one side of a 7-session slice is not an edge. "
        "Fills are simulated OHLC, not exchange prints."
    )
    lines += ["", "## Reading", "", reading, ""]
    out = BACKEND / "orb_retest_fail_results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "REPORT.md").write_text("\n".join(lines))
    payload = {
        "dates_match_preserved_v3": True,
        "fade_unfilled": reasons(fade_rows),
        "with_unfilled": reasons(with_rows),
        "fade": fade_stats,
        "with": with_stats,
        "reading": reading,
        "live_orders_sent": 0,
    }
    (out / "summary.json").write_text(json.dumps(payload, indent=2))
    print(json.dumps({
        "fade_net": {name: fade_stats[name]["net_pnl"] for name in fade_stats},
        "fade_n": {name: fade_stats[name]["closed_trades"] for name in fade_stats},
        "with_net": {name: with_stats[name]["net_pnl"] for name in with_stats},
        "with_n": {name: with_stats[name]["closed_trades"] for name in with_stats},
        "fade_unfilled": reasons(fade_rows),
        "reading": reading,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
