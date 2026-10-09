#!/usr/bin/env python3
"""Run the pre-declared New York ORB comparison and write an audit bundle.

This script does not search parameters and does not submit orders.
The primary preset was fixed before the results were inspected.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.orb.audit import audit_database, database_status, load_bars, resolve_market_db, session_coverage
from src.orb.engine import comparison_grid, chronological_splits, primary_config, run_backtest
from src.orb.market import build_opening_ranges
from src.orb.metrics import summarize


COLUMNS = [
    "config_id", "split", "ny_date", "symbol", "timeframe", "orb", "entry_model", "sides",
    "risk_profile", "slippage_profile", "direction", "status", "unfilled_reason",
    "signal_ts", "signal_price", "order_price", "order_type", "info_known_ts",
    "fill_ts", "fill_price", "raw_entry", "fill_outside_bar", "ohlc_proxy", "qty",
    "notional", "leverage", "stop_price", "target_price", "exit_ts", "exit_price",
    "raw_exit", "exit_reason", "path_ambiguous", "fee_entry", "fee_exit", "gross_pnl",
    "net_pnl", "execution_drag", "r_multiple", "or_high", "or_low", "equity_after",
    "mtm_pnl", "same_bar_exit",
]


def _run_timeframe(bars, timeframe: str, bar_seconds: int, grid):
    ranges_by_orb = {
        orb: build_opening_ranges(bars, orb, bar_seconds, require_nyse_day=True)
        for orb in ("ORB-5", "ORB-15", "ORB-30")
    }
    complete_sets = []
    for ranges in ranges_by_orb.values():
        complete_sets.append({day for day, rng in ranges.items() if rng.complete})
    shared = set.intersection(*complete_sets) if complete_sets else set()
    splits = chronological_splits(sorted(shared))
    split_dates = {
        "train": [d for d, name in splits.items() if name == "train"],
        "validation": [d for d, name in splits.items() if name == "validation"],
        "out_of_sample": [d for d, name in splits.items() if name == "out_of_sample"],
    }
    records = []
    for split_name, days in (
        ("train", split_dates["train"]),
        ("validation", split_dates["validation"]),
        ("out_of_sample", split_dates["out_of_sample"]),
    ):
        allow = set(days)
        for cfg in grid:
            records.extend(
                run_backtest(
                    bars,
                    cfg,
                    ranges=ranges_by_orb[cfg.orb],
                    entry_dates=allow,
                    split_name=split_name,
                )
            )
    return records, {
        "shared_complete_nyse_sessions": len(shared),
        "split_counts": {k: len(v) for k, v in split_dates.items()},
        "split_first_last": {
            k: [v[0].isoformat(), v[-1].isoformat()] if v else None for k, v in split_dates.items()
        },
        "independent_equity": (
            "Each split starts at the configured equity. A split does not "
            "inherit position size from an earlier split. Entries are allowed "
            "only on that split's New York dates. An open trade may exit on a "
            "later date; that later path is not used to choose the configuration."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="NY-session ORB backtest")
    parser.add_argument("--db", default=None)
    parser.add_argument("--out", default=str(BACKEND / "orb_results"))
    args = parser.parse_args()
    path = resolve_market_db(args.db)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if path is None:
        report = {
            "error": "no usable market_data_clean.db",
            "status": database_status(args.db),
            "note": (
                "backend/market_data_clean.db is a Git LFS pointer until `git lfs pull`. "
                "A pointer is not a price history. market_data.db was not substituted. "
                "No fills were fabricated."
            ),
        }
        (out / "data_audit.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 2
    audit = audit_database(path)
    bars_5m = load_bars(path, "BTC_USDT", "5m")
    bars_1m = load_bars(path, "BTC_USDT", "1m")
    audit["session_coverage_5m"] = session_coverage(bars_5m, 300)
    audit["session_coverage_1m"] = session_coverage(bars_1m, 60)
    audit["primary_timeframe"] = "5m"
    audit["why_5m"] = (
        "5m candles from 2025-09-15 through 2026-09-27 have no gaps and divide "
        "5, 15 and 30 minute opening ranges exactly. 1m history is about six weeks "
        "and contains a multi-thousand-bar gap, so it is only a resolution check. "
        "15m candles cannot build ORB-5 without including prices from outside the "
        "five-minute window. No bid/ask, trade, or order-book history is stored."
    )
    (out / "data_audit.json").write_text(json.dumps(audit, indent=2))

    grid = comparison_grid("5m", 300)
    # The primary preset is one cell of this grid, not a separately tuned run.
    assert any(cfg.config_id() == primary_config().config_id() for cfg in grid)
    records, split_meta = _run_timeframe(bars_5m, "5m", 300, grid)
    summary = summarize(records)
    primary_id = primary_config().config_id()
    primary_rows = [row for row in summary["by_config_and_split"] if row["config_id"] == primary_id]

    one_minute = comparison_grid("1m", 60)
    primary_1m = [cfg for cfg in one_minute if cfg.orb == "ORB-15" and cfg.entry_model == "close"
                  and cfg.sides == "both" and cfg.risk_profile == "baseline_25bps_2R"
                  and cfg.slippage_profile == "normal"]
    records_1m, split_1m = _run_timeframe(bars_1m, "1m", 60, primary_1m)
    summary_1m = summarize(records_1m)

    bundle = {
        "primary_config_id": primary_id,
        "primary_was_chosen_before_results": True,
        "profitability_claim": None,
        "grid_size": len(grid),
        "predeclared_configs": [cfg.to_dict() for cfg in grid],
        "split": split_meta,
        "summary": summary,
        "primary_rows": primary_rows,
        "resolution_check_1m": {
            "note": "Separate sample. Not mixed into the 5m train/validation/out-of-sample split.",
            "split": split_1m,
            "summary": summary_1m,
        },
    }
    (out / "summary.json").write_text(json.dumps(bundle, indent=2))
    with (out / "trades.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    with (out / "trades_1m_primary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records_1m)
    compact = []
    for row in summary["by_config_and_split"]:
        compact.append({k: row[k] for k in (
            "config_id", "split", "signals", "filled_trades", "fill_rate", "closed_trades",
            "unresolved_trades", "win_rate", "gross_pnl", "net_pnl", "profit_factor",
            "expectancy", "average_r", "max_drawdown_fraction", "ending_equity_realized",
            "fees", "execution_drag", "ambiguous_closed_trades", "unambiguous_net_pnl",
        ) if k in row})
    print(json.dumps({
        "db": str(path),
        "trades": len(records),
        "split": split_meta,
        "primary": [r for r in compact if r["config_id"] == primary_id],
        "out": str(out),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
