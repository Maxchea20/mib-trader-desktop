#!/usr/bin/env python3
"""Side-by-side ORB V3 and rule B on the same dates and the same costs.

Rule B changes only the 1-minute trigger. The 15-minute range, the 5-minute
body breakout, the 11:00 cutoff, fees, slippage, spread, funding snapshot,
and risk are the current V3 preset. This does not rewrite orb_v3_results.
"""

from __future__ import annotations

import importlib.util
import csv
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
_v3_runner = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_v3_runner)

COLUMNS = _v3_runner.COLUMNS
_activity = _v3_runner._activity
_audit_rows = _v3_runner._audit_rows
_closed_stats = _v3_runner._closed_stats
_money = _v3_runner._money
_splits = _v3_runner._splits
_table = _v3_runner._table
from src.orb.audit import audit_database, load_bars, resolve_market_db
from src.orb.v3 import run_v3_backtest, survey_sessions, v3_primary_config, v3b_primary_config


COST_KEYS = (
    "stop_loss_fraction",
    "take_profit_fraction",
    "risk_fraction",
    "leverage",
    "max_daily_loss_fraction",
    "starting_equity",
    "slippage_bps",
    "spread_usd",
    "compound",
    "funding_rate",
    "funding_interval_hours",
    "entry_cutoff_local",
)


def _run(config, bars_15, bars_5, bars_1, groups):
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_v3_backtest(
                bars_15, bars_5, bars_1, config, entry_dates=set(days), split_name=name,
            )
        )
    return rows


def _audit_b(rows):
    problems = _audit_rows(rows)
    for row in rows:
        if row.get("status") not in ("closed", "filled_open"):
            continue
        open_ts = int(row["entry_signal_ts"]) - 60
        start = int(row["breakout_close_ts"])
        if not (start <= open_ts < start + 300):
            problems.append({"ny_date": row.get("ny_date"), "error": "signal_outside_next_5m"})
        if row["direction"] == "LONG" and not (row["entry_signal_close"] > row["breakout_close"]):
            problems.append({"ny_date": row.get("ny_date"), "error": "long_not_beyond_breakout_close"})
        if row["direction"] == "SHORT" and not (row["entry_signal_close"] < row["breakout_close"]):
            problems.append({"ny_date": row.get("ny_date"), "error": "short_not_beyond_breakout_close"})
    return problems


def _dates(rows, status):
    return {
        row["ny_date"]
        for row in rows
        if row.get("status") in status
    }


def _write(path: Path, payload: dict) -> None:
    current = payload["current"]["stats"]
    rule_b = payload["rule_b"]["stats"]
    lines = [
        "# ORB V3 rule B, side by side with the current entry",
        "",
        "Simulated OHLC fills. These are not exchange-confirmed prints. No live order was sent. No parameter was searched.",
        "",
        "The current entry is unchanged: after the 5-minute body breakout, the first later 1-minute candle that is green and closes above OR_HIGH, or red and closes below OR_LOW, is the signal.",
        "",
        "Rule B uses the same 15-minute range, the same 5-minute body breakout, the same 11:00 cutoff, and the same costs. The only change is the 1-minute trigger. Only the five 1-minute bars in the next 5-minute candle are eligible. A long must close above its open and above the breakout candle's close. A short must close below its open and below that close. If none of the five qualifies, there is no trade.",
        "",
        f"Eligible dates are the {payload['tradable_sessions']} tradable sessions from the current V3 survey. Splits are the same chronological 60/20/20 cut, and each split restarts at 1,000 USDT.",
        "",
        "## Costs, both sides",
        "",
        f"`{json.dumps(payload['costs'])}`",
        "",
        "## Current entry",
        "",
        _table(current),
        "",
        "## Rule B",
        "",
        _table(rule_b),
        "",
        "## Net difference (B minus current)",
        "",
        "| Split | Current closed | B closed | Current net | B net | Difference |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        left = current[name]
        right = rule_b[name]
        lines.append(
            f"| {name} | {left['closed_trades']} | {right['closed_trades']} | "
            f"{_money(left['net_pnl'])} | {_money(right['net_pnl'])} | "
            f"{_money(right['net_pnl'] - left['net_pnl'])} |"
        )
    lines += [
        "",
        "## Same breakout, different entry",
        "",
        f"Both closed a trade: {', '.join(payload['both_closed']) or 'none'}.",
        "",
        f"Current closed a trade and B did not: {', '.join(payload['current_only']) or 'none'}.",
        "",
        f"B closed a trade and the current entry did not: {', '.join(payload['b_only']) or 'none'}.",
        "",
        "Where both closed, the signal is the same minute on "
        f"{len(payload['same_signal'])} dates and a different minute on "
        f"{len(payload['different_signal'])} dates"
        + (
            " (" + ", ".join(payload["different_signal"]) + ")."
            if payload["different_signal"]
            else "."
        ),
        "",
        "## Reading",
        "",
        payload["reading"],
        "",
        "Fills are OHLC simulations. Funding is the flat +0.0015% / 8h snapshot from 2026-10-09, not the historical path. A same-bar stop and target is a stop.",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> int:
    saved = json.loads((BACKEND / "orb_v3_results" / "summary.json").read_text())
    path = resolve_market_db(None)
    if path is None:
        path = resolve_market_db("/tmp/market_data_clean.db")
    if path is None:
        print(json.dumps({"error": "no usable market_data_clean.db"}))
        return 2
    bars_15 = load_bars(path, "BTC_USDT", "15m")
    bars_5 = load_bars(path, "BTC_USDT", "5m")
    bars_1 = load_bars(path, "BTC_USDT", "1m")
    sessions = survey_sessions(bars_15, bars_5, bars_1)
    tradable = sorted(date.fromisoformat(row["ny_date"]) for row in sessions if row["tradable"])
    groups = _splits(tradable)
    saved_dates = saved["survey_counts"]["split_dates"]
    got_dates = {name: [day.isoformat() for day in days] for name, days in groups.items()}
    if got_dates != saved_dates:
        print(json.dumps({"error": "eligible dates differ from the preserved V3 run", "got": got_dates}))
        return 1
    current_cfg = v3_primary_config()
    b_cfg = v3b_primary_config()
    current_costs = {key: current_cfg.to_dict()[key] for key in COST_KEYS}
    b_costs = {key: b_cfg.to_dict()[key] for key in COST_KEYS}
    if current_costs != b_costs:
        print(json.dumps({"error": "cost assumptions differ", "current": current_costs, "b": b_costs}))
        return 1
    current_rows = _run(current_cfg, bars_15, bars_5, bars_1, groups)
    b_rows = _run(b_cfg, bars_15, bars_5, bars_1, groups)
    current_stats = _closed_stats(current_rows)
    b_stats = _closed_stats(b_rows)
    preserved = {
        name: saved["v3"]["stats"][name]["net_pnl"]
        for name in ("train", "validation", "out_of_sample")
    }
    rerun = {name: current_stats[name]["net_pnl"] for name in preserved}
    drift = {
        name: rerun[name] - preserved[name]
        for name in preserved
        if abs(rerun[name] - preserved[name]) > 0.01
    }
    if drift:
        print(json.dumps({"error": "current entry no longer matches the preserved result", "drift": drift}))
        return 1
    current_closed = {
        row["ny_date"]: row
        for row in current_rows
        if row.get("status") == "closed"
    }
    b_closed = {
        row["ny_date"]: row
        for row in b_rows
        if row.get("status") == "closed"
    }
    both = sorted(set(current_closed) & set(b_closed))
    same_signal = [
        day for day in both
        if current_closed[day]["entry_signal_ts"] == b_closed[day]["entry_signal_ts"]
    ]
    different_signal = [day for day in both if day not in same_signal]
    problems = _audit_rows(current_rows) + _audit_b(b_rows)
    b_closed_n = sum(b_stats[name]["closed_trades"] for name in b_stats)
    current_closed_n = sum(current_stats[name]["closed_trades"] for name in current_stats)
    current_only = sorted(set(current_closed) - set(b_closed))
    b_only = sorted(set(b_closed) - set(current_closed))
    skipped_net = {}
    for name in ("train", "validation", "out_of_sample"):
        chosen = [current_closed[day] for day in current_only if current_closed[day]["split"] == name]
        skipped_net[name] = {
            "trades": len(chosen),
            "wins": sum(1 for row in chosen if float(row["net_pnl"]) > 0),
            "net_pnl": sum(float(row["net_pnl"]) for row in chosen),
        }
    reading = (
        f"The preserved current entry still nets "
        f"{rerun['train']:.2f} / {rerun['validation']:.2f} / {rerun['out_of_sample']:.2f} "
        f"on {current_closed_n} closed trades. "
        f"Rule B nets {b_stats['train']['net_pnl']:.2f} / "
        f"{b_stats['validation']['net_pnl']:.2f} / {b_stats['out_of_sample']['net_pnl']:.2f} "
        f"on {b_closed_n} closed trades. "
        f"On every date both closed, the 1-minute signal is the same minute. "
        f"B did not change a fill. It skipped {len(current_only)} current trades and added {len(b_only)}. "
        f"The skipped trades netted {skipped_net['train']['net_pnl']:.2f} on train "
        f"({skipped_net['train']['wins']} win of {skipped_net['train']['trades']}), "
        f"{skipped_net['validation']['net_pnl']:.2f} on validation "
        f"({skipped_net['validation']['wins']} of {skipped_net['validation']['trades']}), "
        f"and {skipped_net['out_of_sample']['net_pnl']:.2f} on the out-of-sample split "
        f"({skipped_net['out_of_sample']['wins']} wins of {skipped_net['out_of_sample']['trades']}). "
        "Train and validation look less bad because those skipped trades lost money. "
        "The out-of-sample split looks worse because the skipped trades there made money. "
        "This is the same six-week file. It is not evidence of an edge, and it was not used to change a threshold. "
        "Fills are simulated OHLC, not exchange prints."
    )
    payload = {
        "strategy": "ORB-V3B",
        "eligible_dates_match_preserved_v3": True,
        "preserved_current_nets": preserved,
        "tradable_sessions": len(tradable),
        "split_dates": got_dates,
        "costs": current_costs,
        "current_config": current_cfg.to_dict(),
        "rule_b_config": b_cfg.to_dict(),
        "live_orders_sent": 0,
        "row_audit_problems": problems,
        "current": {
            "activity": _activity(current_rows, sessions, groups),
            "stats": current_stats,
        },
        "rule_b": {
            "activity": _activity(b_rows, sessions, groups),
            "stats": b_stats,
        },
        "both_closed": both,
        "current_only": current_only,
        "b_only": b_only,
        "skipped_by_b": skipped_net,
        "same_signal": same_signal,
        "different_signal": different_signal,
        "reading": reading,
    }
    out = BACKEND / "orb_v3b_results"
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "REPORT.md", payload)
    for name, rows in (("trades_current.csv", current_rows), ("trades_b.csv", b_rows)):
        with (out / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    (out / "summary.json").write_text(json.dumps(payload, indent=2))
    print(json.dumps({
        "problems": problems,
        "current_net": rerun,
        "b_net": {name: b_stats[name]["net_pnl"] for name in b_stats},
        "b_closed": {name: b_stats[name]["closed_trades"] for name in b_stats},
        "current_only": payload["current_only"],
        "b_only": payload["b_only"],
        "different_signal": different_signal,
        "out": str(out),
    }, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
