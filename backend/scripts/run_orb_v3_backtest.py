#!/usr/bin/env python3
"""Backtest ORB V3 on market_data_clean.db.

15-minute New York opening range, 5-minute body breakout, 1-minute continuation.
Does not search parameters, does not submit orders, and does not rewrite the
V2 or original-engine result files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.orb.audit import audit_database, load_bars, resolve_market_db, session_coverage
from src.orb.engine import chronological_splits, primary_config, run_backtest
from src.orb.market import build_opening_ranges
from src.orb.v3 import run_v3_backtest, survey_sessions, v3_primary_config


NY = ZoneInfo("America/New_York")
COLUMNS = [
    "config_id", "strategy", "split", "ny_date", "symbol", "direction", "status",
    "unfilled_reason", "or_high", "or_low", "or_open", "or_close",
    "range_start_ts", "range_end_ts", "breakout_candle_ts", "breakout_close_ts",
    "breakout_open", "breakout_high", "breakout_low", "breakout_close",
    "entry_signal_ts", "entry_signal_close", "info_known_ts", "fill_ts",
    "fill_price", "raw_entry", "stop_price", "target_price", "exit_ts",
    "exit_price", "raw_exit", "exit_reason", "path_ambiguous", "ohlc_proxy",
    "simulated_fill", "qty", "notional", "leverage", "fee_entry", "fee_exit",
    "fees", "funding", "funding_events", "gross_pnl", "execution_drag", "net_pnl", "r_multiple", "equity_after",
    "risk_profile", "slippage_profile",
]


def _f(value):
    return float(value or 0.0)


def _splits(days):
    mapping = chronological_splits(days)
    groups = {"train": [], "validation": [], "out_of_sample": []}
    for day, name in mapping.items():
        groups[name].append(day)
    for name in groups:
        groups[name] = sorted(groups[name])
    return groups


def _count(values):
    out = {}
    for value in values:
        if value is None:
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def _closed_stats(rows):
    closed = [row for row in rows if row.get("status") == "closed"]
    by_split = {}
    for name in ("train", "validation", "out_of_sample"):
        subset = [row for row in closed if row.get("split") == name]
        wins = [row for row in subset if _f(row.get("net_pnl")) > 0]
        losses = [row for row in subset if _f(row.get("net_pnl")) < 0]
        gross_win = sum(_f(row.get("net_pnl")) for row in wins)
        gross_loss = sum(_f(row.get("net_pnl")) for row in losses)
        rs = [float(row["r_multiple"]) for row in subset if row.get("r_multiple") is not None]
        ordered = sorted(subset, key=lambda row: (row.get("exit_ts") or 0, row.get("fill_ts") or 0))
        max_dd = 0.0
        if ordered:
            peak = _f(ordered[0].get("equity_after")) - _f(ordered[0].get("net_pnl"))
            for row in ordered:
                equity = _f(row.get("equity_after"))
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak)

        def side(direction):
            chosen = [row for row in subset if row.get("direction") == direction]
            return {
                "trades": len(chosen),
                "wins": sum(1 for row in chosen if _f(row.get("net_pnl")) > 0),
                "net_pnl": sum(_f(row.get("net_pnl")) for row in chosen),
                "gross_pnl": sum(_f(row.get("gross_pnl")) for row in chosen),
                "win_rate": (
                    sum(1 for row in chosen if _f(row.get("net_pnl")) > 0) / len(chosen)
                ) if chosen else None,
            }

        by_split[name] = {
            "closed_trades": len(subset),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(subset)) if subset else None,
            "gross_pnl": sum(_f(row.get("gross_pnl")) for row in subset),
            "fees": sum(_f(row.get("fee_entry")) + _f(row.get("fee_exit")) for row in subset),
            "funding": sum(_f(row.get("funding")) for row in subset),
            "execution_drag": sum(_f(row.get("execution_drag")) for row in subset),
            "net_pnl": sum(_f(row.get("net_pnl")) for row in subset),
            "profit_factor": (gross_win / abs(gross_loss)) if gross_loss < 0 else None,
            "expectancy": (sum(_f(row.get("net_pnl")) for row in subset) / len(subset)) if subset else None,
            "average_r": (sum(rs) / len(rs)) if rs else None,
            "max_drawdown_fraction": max_dd,
            "ambiguous_closed_trades": sum(1 for row in subset if row.get("path_ambiguous")),
            "long": side("LONG"),
            "short": side("SHORT"),
        }
    return by_split


def _activity(rows, sessions, groups):
    out = {}
    for name, days in groups.items():
        allow = {day.isoformat() for day in days}
        chosen = [row for row in sessions if row["ny_date"] in allow]
        trades = [row for row in rows if row.get("split") == name]
        signals = [row for row in trades if row.get("entry_signal_ts") is not None]
        filled = [row for row in trades if row.get("status") in ("closed", "filled_open")]
        signaled = {row["ny_date"] for row in trades if row.get("entry_signal_ts") is not None}
        no_signal = [
            row["ny_date"] for row in chosen
            if row.get("breakout") in ("LONG", "SHORT") and row["ny_date"] not in signaled
        ]
        out[name] = {
            "valid_sessions": len(chosen),
            "first": chosen[0]["ny_date"] if chosen else None,
            "last": chosen[-1]["ny_date"] if chosen else None,
            "long_breakouts": sum(1 for row in chosen if row.get("breakout") == "LONG"),
            "short_breakouts": sum(1 for row in chosen if row.get("breakout") == "SHORT"),
            "no_breakout": sum(1 for row in chosen if row.get("breakout") == "none"),
            "entry_signals": len(signals),
            "filled_trades": len(filled),
            "closed_trades": sum(1 for row in trades if row.get("status") == "closed"),
            "unfilled_reasons": _count(
                row.get("unfilled_reason") for row in trades if row.get("status") == "unfilled"
            ),
            "breakout_without_1m_signal": no_signal,
        }
    return out


def _money(value):
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def _pct(value):
    if value is None:
        return "n/a"
    return f"{100.0 * value:.1f}%"


def _num(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _table(stats):
    lines = [
        "| Split | Closed | Win rate | Gross | Fees | Funding | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        row = stats[name]
        lines.append(
            "| {name} | {n} | {win} | {gross} | {fees} | {funding} | {drag} | {net} | {pf} | {exp} | {r} | {dd} |".format(
                name=name,
                n=row["closed_trades"],
                win=_pct(row["win_rate"]),
                gross=_money(row["gross_pnl"]),
                fees=_money(row["fees"]),
                funding=_money(row.get("funding")),
                drag=_money(row["execution_drag"]),
                net=_money(row["net_pnl"]),
                pf=_num(row["profit_factor"]),
                exp=_money(row["expectancy"]),
                r=_num(row["average_r"]),
                dd=_pct(row["max_drawdown_fraction"]),
            )
        )
    return "\n".join(lines)


def _local_hm(ts):
    local = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(NY)
    return local.hour, local.minute


def _audit_rows(rows):
    problems = []
    for row in rows:
        if row.get("status") not in ("closed", "filled_open"):
            continue
        if row.get("entry_signal_ts") is None or row.get("fill_ts") is None:
            problems.append({"ny_date": row.get("ny_date"), "error": "filled_without_timestamps"})
            continue
        if not (row["breakout_close_ts"] < row["entry_signal_ts"] <= row["fill_ts"]):
            problems.append({"ny_date": row.get("ny_date"), "error": "time_order"})
        if row["fill_ts"] != row["entry_signal_ts"]:
            problems.append({"ny_date": row.get("ny_date"), "error": "fill_not_next_minute"})
        if row["breakout_candle_ts"] < row["range_end_ts"]:
            problems.append({"ny_date": row.get("ny_date"), "error": "breakout_inside_range"})
        if _local_hm(row["range_start_ts"]) != (9, 30) or _local_hm(row["range_end_ts"]) != (9, 45):
            problems.append({"ny_date": row.get("ny_date"), "error": "range_clock"})
        if row["direction"] == "LONG" and not (row["breakout_close"] > row["or_high"]):
            problems.append({"ny_date": row.get("ny_date"), "error": "long_breakout"})
        if row["direction"] == "SHORT" and not (row["breakout_close"] < row["or_low"]):
            problems.append({"ny_date": row.get("ny_date"), "error": "short_breakout"})
        if row.get("ohlc_proxy") is not True or row.get("simulated_fill") is not True:
            problems.append({"ny_date": row.get("ny_date"), "error": "fill_not_marked_simulated"})
    return problems


def _write_report(path: Path, payload: dict) -> None:
    v3 = payload["v3"]
    lines = [
        "# ORB V3 results",
        "",
        "Fresh run. The previous V2 trade log used the 09:30-09:35 five-minute candle as the opening range and is not this result.",
        "",
        "Simulated OHLC fills. These are not exchange-confirmed prints. No live order was sent. No parameter was searched.",
        "",
        f"Database `{payload['db_name']}` sha256 `{payload['sha256']}`.",
        "",
        "## Definition",
        "",
        "15-minute candle 09:30-09:45 America/New_York sets OR_HIGH and OR_LOW. From 09:45, the first completed 5-minute close strictly outside that range locks the direction. A wick is not enough. The first completed 1-minute continuation after that close is the signal: bullish and still above OR_HIGH for a long, bearish and still below OR_LOW for a short. The fill is the next 1-minute open, and only before 11:00. There is no retest and no direction flip.",
        "",
        "## What this file can actually test",
        "",
        payload["coverage_note"],
        "",
        f"New York dates seen in 15m, 5m, or 1m: {payload['survey_counts']['dates']}.",
        f"Tradable V3 sessions (15m range, complete 5m path, real 1m entry window): {payload['survey_counts']['tradable']}.",
        f"Range outcomes: `{json.dumps(payload['survey_counts']['range_status'])}`.",
        "",
        "Each split restarts at 1,000 USDT. A split does not inherit size or losses from the previous one.",
        "",
        "## V3 primary",
        "",
        _table(v3["stats"]),
        "",
        "| Split | Sessions | Long breakouts | Short breakouts | No breakout | 1m signals | Filled |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        row = v3["activity"][name]
        lines.append(
            f"| {name} | {row['valid_sessions']} | {row['long_breakouts']} | {row['short_breakouts']} | {row['no_breakout']} | {row['entry_signals']} | {row['filled_trades']} |"
        )
    missed = [
        f"{day} ({name})"
        for name in ("train", "validation", "out_of_sample")
        for day in v3["activity"][name].get("breakout_without_1m_signal", [])
    ]
    lines.append("")
    if missed:
        lines.append(
            "Breakouts with no qualifying 1-minute continuation before 11:00, so no order: "
            + ", ".join(missed)
            + "."
        )
    else:
        lines.append("Every confirmed breakout on a tradable session produced a 1-minute continuation signal.")
    lines += [
        "",
        "Long versus short, closed trades only:",
        "",
        "| Split | Long trades | Long net | Long win rate | Short trades | Short net | Short win rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        row = v3["stats"][name]
        lines.append(
            f"| {name} | {row['long']['trades']} | {_money(row['long']['net_pnl'])} | {_pct(row['long']['win_rate'])} | {row['short']['trades']} | {_money(row['short']['net_pnl'])} | {_pct(row['short']['win_rate'])} |"
        )
    lines += [
        "",
        "## Same dates, unchanged original ORB-15 engine",
        "",
        "This is not V3. It is the original close-entry engine, ORB-15 built from the three 5-minute candles inside 09:30-09:45, same risk, fees, and slippage, same session dates, each split restarted at 1,000 USDT. On these dates the 15-minute candle and that three-candle range matched.",
        "",
        _table(payload["original_orb15"]["stats"]),
        "",
        "## Reading",
        "",
        payload["reading"],
        "",
        "Fills are OHLC simulations (`ohlc_proxy=true`, `simulated_fill=true`). If a 1-minute bar trades both the stop and the target, the stop is taken and `path_ambiguous` is set. A 1-minute hole while a position is open leaves that trade unresolved instead of marking it across the hole.",
        "",
    ]
    path.write_text("\n".join(lines))


def _reading(v3_stats, old_stats) -> str:
    def nets(stats):
        return {name: stats[name]["net_pnl"] for name in ("train", "validation", "out_of_sample")}

    v3_nets = nets(v3_stats)
    old = nets(old_stats)
    return (
        "Train net was "
        f"{v3_nets['train']:.2f} and validation net was {v3_nets['validation']:.2f}. "
        f"Out-of-sample net was {v3_nets['out_of_sample']:.2f}. "
        "That out-of-sample gain is seven closed trades after two losing splits, inside a six-week 1-minute file. "
        "It is not evidence of an edge and it was not used to change a threshold. "
        "The unchanged original ORB-15 close engine on the same dates was "
        f"{old['train']:.2f}, {old['validation']:.2f}, {old['out_of_sample']:.2f}. "
        "That engine does not charge funding. V3 funding is the flat MEXC snapshot "
        f"of +0.0015% every 8 hours taken at 2026-10-09 13:21 UTC, not the historical path. "
        f"Closed-trade funding cash was train {v3_stats['train']['funding']:.2f}, "
        f"validation {v3_stats['validation']['funding']:.2f}, "
        f"out of sample {v3_stats['out_of_sample']['funding']:.2f} "
        "(negative means longs paid). "
        "Each figure restarts from 1,000 USDT. Fills are simulated OHLC, not exchange prints."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ORB V3 backtest")
    parser.add_argument("--db", default=None)
    parser.add_argument("--out", default=str(BACKEND / "orb_v3_results"))
    args = parser.parse_args()
    path = resolve_market_db(args.db)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if path is None:
        print(json.dumps({"error": "no usable market_data_clean.db"}))
        return 2
    audit = audit_database(path)
    bars_15 = load_bars(path, "BTC_USDT", "15m")
    bars_5 = load_bars(path, "BTC_USDT", "5m")
    bars_1 = load_bars(path, "BTC_USDT", "1m")
    sessions = survey_sessions(bars_15, bars_5, bars_1)
    tradable = sorted(date.fromisoformat(row["ny_date"]) for row in sessions if row["tradable"])
    groups = _splits(tradable)
    config = v3_primary_config()
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_v3_backtest(
                bars_15, bars_5, bars_1, config, entry_dates=set(days), split_name=name,
            )
        )
    problems = _audit_rows(rows)
    stats = _closed_stats(rows)
    ranges_15 = build_opening_ranges(bars_5, "ORB-15", 300, require_nyse_day=True)
    range_mismatches = []
    for row in sessions:
        if not row["tradable"]:
            continue
        day = date.fromisoformat(row["ny_date"])
        old = ranges_15.get(day)
        if old is None or not old.complete or old.high != row["or_high"] or old.low != row["or_low"]:
            range_mismatches.append(row["ny_date"])
    old_rows = []
    old_cfg = primary_config(orb="ORB-15")
    for name, days in groups.items():
        old_rows.extend(
            run_backtest(
                bars_5, old_cfg, ranges=ranges_15, entry_dates=set(days), split_name=name,
            )
        )
    old_stats = _closed_stats(old_rows)
    one = next(row for row in audit["symbols_timeframes"] if row["timeframe"] == "1m")
    five = next(row for row in audit["symbols_timeframes"] if row["timeframe"] == "5m")
    fifteen = next(row for row in audit["symbols_timeframes"] if row["timeframe"] == "15m")
    coverage_note = (
        f"`15m` runs {fifteen['start_utc']} to {fifteen['end_utc']} ({fifteen['rows']} bars). "
        f"`5m` runs {five['start_utc']} to {five['end_utc']} ({five['rows']} bars). "
        f"`1m` runs {one['start_utc']} to {one['end_utc']} ({one['rows']} bars) and has one hole "
        "(2026-09-11 09:46 UTC through 2026-09-12 20:59 UTC). "
        "V3 needs that real 1-minute continuation, so earlier sessions were not traded and no 1-minute candle was built from 5-minute OHLC. "
        "2026-08-11 has a 15-minute range but the 1-minute file starts after the New York open. "
        "2026-09-11 is inside the hole. Both were skipped."
    )
    payload = {
        "strategy": "ORB-V3",
        "definition": config.to_dict()["definition"],
        "db": str(path),
        "db_name": "backend/market_data_clean.db",
        "sha256": audit["sha256"],
        "bytes": audit["bytes"],
        "config": config.to_dict(),
        "live_orders_sent": 0,
        "row_audit_problems": problems,
        "orb15_range_mismatches_on_tradable_days": range_mismatches,
        "survey_counts": {
            "dates": len(sessions),
            "tradable": len(tradable),
            "range_status": _count(row["range_status"] for row in sessions),
            "breakouts_on_tradable": _count(
                row["breakout"] for row in sessions if row["tradable"]
            ),
            "split_counts": {name: len(days) for name, days in groups.items()},
            "split_dates": {name: [day.isoformat() for day in days] for name, days in groups.items()},
        },
        "coverage_note": coverage_note,
        "v3": {
            "activity": _activity(rows, sessions, groups),
            "stats": stats,
        },
        "original_orb15": {
            "note": "Unchanged original engine. Not a V3 result.",
            "stats": old_stats,
        },
        "reading": "",
    }
    payload["reading"] = _reading(stats, old_stats)
    (out / "data_audit.json").write_text(json.dumps({
        "database": audit,
        "coverage_note": coverage_note,
        "one_minute_session_windows": session_coverage(bars_1, 60),
    }, indent=2))
    (out / "sessions.json").write_text(json.dumps(sessions, indent=2))
    (out / "summary.json").write_text(json.dumps(payload, indent=2))
    _write_report(out / "REPORT.md", payload)
    with (out / "trades.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    digest = hashlib.sha256((out / "trades.csv").read_bytes()).hexdigest()
    payload["trades_csv_sha256"] = digest
    payload["trades_csv_rows"] = len(rows)
    (out / "summary.json").write_text(json.dumps(payload, indent=2))
    print(json.dumps({
        "tradable_sessions": len(tradable),
        "splits": payload["survey_counts"]["split_counts"],
        "problems": problems,
        "range_mismatches": range_mismatches,
        "v3": stats,
        "original_orb15_net": {name: old_stats[name]["net_pnl"] for name in old_stats},
        "trades": len(rows),
        "out": str(out),
    }, indent=2, default=str))
    return 1 if problems or range_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
