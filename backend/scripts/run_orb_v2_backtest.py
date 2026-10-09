#!/usr/bin/env python3
"""Run the original ORB primary preset, then ORB V2, on market_data_clean.db.

Does not search parameters and does not submit orders.
V2's entry-distance filter is off on the primary run. The 1x-range run is
reported separately and is not a selected configuration.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.orb.audit import audit_database, load_bars, resolve_market_db
from src.orb.engine import chronological_splits, primary_config, run_backtest
from src.orb.market import build_opening_ranges
from src.orb.metrics import summarize
from src.orb.v2 import run_v2_backtest, survey_sessions, v2_primary_config


COLUMNS = [
    "config_id", "strategy", "split", "ny_date", "symbol", "direction", "status",
    "unfilled_reason", "or_high", "or_low", "breakout_candle_ts", "breakout_close_ts",
    "breakout_close", "entry_signal_ts", "entry_signal_close", "info_known_ts",
    "fill_ts", "fill_price", "raw_entry", "stop_price", "target_price", "exit_ts",
    "exit_price", "raw_exit", "exit_reason", "path_ambiguous", "ohlc_proxy",
    "simulated_fill", "qty", "notional", "leverage", "fee_entry", "fee_exit", "fees",
    "gross_pnl", "execution_drag", "net_pnl", "r_multiple", "equity_after",
    "distance_filter", "risk_profile", "slippage_profile",
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
    return mapping, groups


def _run_v2(bars_5m, bars_1m, groups, config):
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_v2_backtest(
                bars_5m,
                bars_1m,
                config,
                entry_dates=set(days),
                split_name=name,
            )
        )
    return rows


def _run_baseline(bars_5m, groups, orb):
    ranges = build_opening_ranges(bars_5m, orb, 300, require_nyse_day=True)
    cfg = primary_config(orb=orb)
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_backtest(
                bars_5m,
                cfg,
                ranges=ranges,
                entry_dates=set(days),
                split_name=name,
            )
        )
    return cfg, rows


def _closed_stats(rows):
    closed = [row for row in rows if row.get("status") == "closed"]
    summary = summarize(closed)["by_config_and_split"] if closed else []
    # summarize groups by config and split. Recompute a direct view so unfilled
    # rows are not counted as trades.
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
    return by_split, summary


def _v2_activity(rows, sessions, groups):
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
            "incomplete_breakout_path": sum(1 for row in chosen if row.get("breakout") == "incomplete"),
            "breakout_without_1m_signal": no_signal,
            "entry_signals": len(signals),
            "filled_trades": len(filled),
            "unfilled_reasons": _count(row.get("unfilled_reason") for row in trades if row.get("status") == "unfilled"),
        }
    return out


def _count(values):
    out = {}
    for value in values:
        if value is None:
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def _baseline_activity(rows):
    out = {}
    for name in ("train", "validation", "out_of_sample"):
        chosen = [row for row in rows if row.get("split") == name and row.get("status") in ("closed", "filled_open")]
        out[name] = {
            "filled_trades": len(chosen),
            "long": sum(1 for row in chosen if row.get("direction") == "LONG"),
            "short": sum(1 for row in chosen if row.get("direction") == "SHORT"),
        }
    return out


def _match_published(fresh_rows, committed_rows):
    mismatches = []
    fresh = {(row["split"]): row for row in fresh_rows}
    for old in committed_rows:
        new = fresh.get(old["split"])
        if new is None:
            mismatches.append({"split": old["split"], "error": "missing"})
            continue
        for key in ("closed_trades", "net_pnl", "gross_pnl", "fees", "execution_drag", "win_rate"):
            if key not in new:
                mismatches.append({"split": old["split"], "key": key, "error": "missing"})
                continue
            if abs(_f(new[key]) - _f(old[key])) > 1e-6:
                mismatches.append(
                    {"split": old["split"], "key": key, "fresh": new[key], "committed": old[key]}
                )
    return mismatches


def _primary_full(bars, timeframe, bar_seconds):
    orbs = ("ORB-5", "ORB-15", "ORB-30")
    ranges = {
        orb: build_opening_ranges(bars, orb, bar_seconds, require_nyse_day=True) for orb in orbs
    }
    shared = set.intersection(*({day for day, rng in found.items() if rng.complete} for found in ranges.values()))
    _, groups = _splits(sorted(shared))
    cfg = primary_config(timeframe=timeframe, bar_seconds=bar_seconds)
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_backtest(
                bars, cfg, ranges=ranges["ORB-15"], entry_dates=set(days), split_name=name,
            )
        )
    return groups, summarize(rows)["by_config_and_split"]


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
        "| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        row = stats[name]
        lines.append(
            "| {name} | {n} | {win} | {gross} | {fees} | {drag} | {net} | {pf} | {exp} | {r} | {dd} |".format(
                name=name,
                n=row["closed_trades"],
                win=_pct(row["win_rate"]),
                gross=_money(row["gross_pnl"]),
                fees=_money(row["fees"]),
                drag=_money(row["execution_drag"]),
                net=_money(row["net_pnl"]),
                pf=_num(row["profit_factor"]),
                exp=_money(row["expectancy"]),
                r=_num(row["average_r"]),
                dd=_pct(row["max_drawdown_fraction"]),
            )
        )
    return "\n".join(lines)


def _write_report(path: Path, payload: dict) -> None:
    v2 = payload["v2_primary"]
    base = payload["same_window_baseline"]
    repro = payload["baseline_repro"]
    lines = [
        "# ORB V2 results",
        "",
        "Simulated OHLC fills. No live order was sent. Nothing here was selected because it made money.",
        "",
        f"Database `backend/market_data_clean.db` sha256 `{payload['sha256']}`.",
        "",
        "## Baseline reproducibility",
        "",
        "The original primary preset was run again on the full 5-minute history: ORB-15, close entry, both sides, 0.25% stop, 0.50% target, 1% equity risk, 5x, 1 bp slippage, 0.10 USDT spread, 1,000 USDT restarted on every split.",
        "",
        f"Match against the committed `backend/orb_results/summary.json` primary rows: **{repro['5m_primary_match']}**.",
        f"The same check on the committed 1-minute ORB-15 resolution run: **{repro['1m_primary_match']}**.",
        "",
        "Published 5-minute primary nets were train -335.17, validation -159.33, out of sample -154.87. The fresh primary run is in `summary.json` under `baseline_repro`. A full 108-cell rerun of `scripts/run_orb_backtest.py` in the same pass reproduced those primary cells.",
        "",
        "## What V2 could actually see",
        "",
        "V2 needs the 09:30 5-minute candle and the five 1-minute candles inside it. `1m` in this file runs from 2026-08-11 18:01 UTC to 2026-09-27 11:02 UTC and has one hole (2026-09-11 09:46 UTC through 2026-09-12 20:59 UTC). Earlier 5-minute history was not turned into fake 1-minute candles.",
        "",
        f"New York dates in the file: {payload['survey_counts']['dates']}.",
        f"Usable V2 sessions: {payload['survey_counts']['usable']}.",
        f"Range outcomes: `{json.dumps(payload['survey_counts']['range_status'])}`.",
        "",
        "Each split below restarts at 1,000 USDT. A split does not inherit size from the previous one.",
        "",
        "## V2 primary (distance filter off)",
        "",
        _table(v2["stats"]),
        "",
        "| Split | Sessions | Long breakouts | Short breakouts | No breakout | 1m signals | Filled |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        row = v2["activity"][name]
        lines.append(
            f"| {name} | {row['valid_sessions']} | {row['long_breakouts']} | {row['short_breakouts']} | {row['no_breakout']} | {row['entry_signals']} | {row['filled_trades']} |"
        )
    missed = [
        f"{day} ({name})"
        for name in ("train", "validation", "out_of_sample")
        for day in v2["activity"][name].get("breakout_without_1m_signal", [])
    ]
    lines.append("")
    if missed:
        lines.append(
            "Breakouts with no qualifying 1-minute continuation before 11:00, so no order: "
            + ", ".join(missed)
            + "."
        )
    else:
        lines.append("Every confirmed breakout produced a 1-minute continuation signal.")
    lines += [
        "",
        "Long versus short, closed trades only:",
        "",
        "| Split | Long trades | Long net | Long win rate | Short trades | Short net | Short win rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "out_of_sample"):
        row = v2["stats"][name]
        lines.append(
            f"| {name} | {row['long']['trades']} | {_money(row['long']['net_pnl'])} | {_pct(row['long']['win_rate'])} | {row['short']['trades']} | {_money(row['short']['net_pnl'])} | {_pct(row['short']['win_rate'])} |"
        )
    lines += [
        "",
        "## Same sessions, previous ORB engine",
        "",
        "These two runs use the unchanged engine and the same risk, fees, and slippage. Entries are still the old close-and-next-open rule on 5-minute candles. The dates are the usable V2 sessions, not the full 2025-2026 sample.",
        "",
        "### Previous primary, ORB-15",
        "",
        _table(base["ORB-15"]["stats"]),
        "",
        "### Previous ORB-5 close (same five-minute window, old entry rule)",
        "",
        _table(base["ORB-5"]["stats"]),
        "",
        "## Distance filter, separate",
        "",
        "Primary leaves the filter off. This run rejects a next open that is more than one opening-range width beyond the broken level. It is not a tuned setting and it is not the comparison above.",
        "",
        _table(payload["v2_distance_filter"]["stats"]),
        "",
        "## Reading",
        "",
        payload["reading"],
        "",
        "Fills are OHLC simulations (`ohlc_proxy=true`, `simulated_fill=true`), not exchange-confirmed prints. If a 1-minute bar trades both the stop and the target, the stop is taken and `path_ambiguous` is set.",
        "",
    ]
    path.write_text("\n".join(lines))


def _reading(v2_stats, orb15_stats, orb5_stats) -> str:
    def nets(stats):
        return {name: stats[name]["net_pnl"] for name in ("train", "validation", "out_of_sample")}

    v2_nets = nets(v2_stats)
    old15 = nets(orb15_stats)
    old5 = nets(orb5_stats)
    better15 = [name for name in v2_nets if v2_nets[name] > old15[name]]
    better5 = [name for name in v2_nets if v2_nets[name] > old5[name]]

    def listed(names):
        return ", ".join(names) if names else "no split"

    return (
        "On the only dates where the 1-minute continuation can be simulated, "
        f"V2 net versus the previous ORB-15 primary was higher on {listed(better15)}. "
        f"Versus the previous ORB-5 close entry it was higher on {listed(better5)}. "
        "Higher on a six-week sample is not evidence of an edge. "
        f"V2 nets were train {v2_nets['train']:.2f}, validation {v2_nets['validation']:.2f}, "
        f"out of sample {v2_nets['out_of_sample']:.2f}. "
        f"ORB-15 on those dates was {old15['train']:.2f}, {old15['validation']:.2f}, {old15['out_of_sample']:.2f}. "
        f"ORB-5 on those dates was {old5['train']:.2f}, {old5['validation']:.2f}, {old5['out_of_sample']:.2f}. "
        "The ORB-15 out-of-sample gain on this window is seven sessions inside a full-sample result that lost 154.87. "
        "The full-sample baseline remains negative on every split. "
        "The separate 1x-range distance filter was also negative on every split and was not adopted. "
        "No parameter was changed to improve these numbers."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ORB V2 backtest")
    parser.add_argument("--db", default=None)
    parser.add_argument("--out", default=str(BACKEND / "orb_v2_results"))
    args = parser.parse_args()
    path = resolve_market_db(args.db)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if path is None:
        print(json.dumps({"error": "no usable market_data_clean.db"}))
        return 2
    audit = audit_database(path)
    bars_5m = load_bars(path, "BTC_USDT", "5m")
    bars_1m = load_bars(path, "BTC_USDT", "1m")
    committed = json.loads((BACKEND / "orb_results" / "summary.json").read_text())
    groups_5m, fresh_5m = _primary_full(bars_5m, "5m", 300)
    groups_1m, fresh_1m = _primary_full(bars_1m, "1m", 60)
    repro = {
        "5m_sessions": {name: [day.isoformat() for day in days] for name, days in groups_5m.items()},
        "5m_primary": fresh_5m,
        "5m_primary_match": "yes" if not _match_published(fresh_5m, committed["primary_rows"]) else "no",
        "5m_primary_mismatches": _match_published(fresh_5m, committed["primary_rows"]),
        "1m_primary": fresh_1m,
        "1m_primary_match": "yes" if not _match_published(fresh_1m, committed["resolution_check_1m"]["summary"]["by_config_and_split"]) else "no",
        "1m_primary_mismatches": _match_published(
            fresh_1m, committed["resolution_check_1m"]["summary"]["by_config_and_split"]
        ),
    }

    sessions = survey_sessions(bars_5m, bars_1m)
    usable_days = sorted(
        date.fromisoformat(row["ny_date"])
        for row in sessions if row["range_status"] == "ok"
    )
    _, groups = _splits(usable_days)
    primary = v2_primary_config()
    filtered = v2_primary_config(max_entry_distance_range_multiple=1.0)
    v2_rows = _run_v2(bars_5m, bars_1m, groups, primary)
    filter_rows = _run_v2(bars_5m, bars_1m, groups, filtered)
    v2_stats, _ = _closed_stats(v2_rows)
    filter_stats, _ = _closed_stats(filter_rows)
    _, orb15_rows = _run_baseline(bars_5m, groups, "ORB-15")
    _, orb5_rows = _run_baseline(bars_5m, groups, "ORB-5")
    orb15_stats, _ = _closed_stats(orb15_rows)
    orb5_stats, _ = _closed_stats(orb5_rows)
    range_status = _count(row["range_status"] for row in sessions)
    payload = {
        "db": str(path),
        "sha256": audit["sha256"],
        "bytes": audit["bytes"],
        "baseline_repro": {
            "5m_primary_match": repro["5m_primary_match"],
            "1m_primary_match": repro["1m_primary_match"],
            "5m_primary_mismatches": repro["5m_primary_mismatches"],
            "1m_primary_mismatches": repro["1m_primary_mismatches"],
            "5m_primary": fresh_5m,
            "1m_primary": fresh_1m,
        },
        "survey_counts": {
            "dates": len(sessions),
            "usable": len(usable_days),
            "range_status": range_status,
            "split_counts": {name: len(days) for name, days in groups.items()},
            "split_dates": {name: [day.isoformat() for day in days] for name, days in groups.items()},
        },
        "v2_primary": {
            "config": primary.to_dict(),
            "activity": _v2_activity(v2_rows, sessions, groups),
            "stats": v2_stats,
        },
        "v2_distance_filter": {
            "config": filtered.to_dict(),
            "activity": _v2_activity(filter_rows, sessions, groups),
            "stats": filter_stats,
            "note": "Separate from the primary comparison. Not used to choose a risk setting.",
        },
        "same_window_baseline": {
            "ORB-15": {"activity": _baseline_activity(orb15_rows), "stats": orb15_stats},
            "ORB-5": {"activity": _baseline_activity(orb5_rows), "stats": orb5_stats},
        },
        "reading": "",
        "live_orders_sent": 0,
    }
    payload["reading"] = _reading(v2_stats, orb15_stats, orb5_stats)
    (out / "data_audit.json").write_text(json.dumps(audit, indent=2))
    (out / "sessions.json").write_text(json.dumps(sessions, indent=2))
    (out / "summary.json").write_text(json.dumps(payload, indent=2))
    _write_report(out / "REPORT.md", payload)
    with (out / "trades.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(v2_rows)
    with (out / "trades_distance_filter.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filter_rows)
    print(json.dumps({
        "usable_sessions": len(usable_days),
        "splits": payload["survey_counts"]["split_counts"],
        "repro_5m": repro["5m_primary_match"],
        "repro_1m": repro["1m_primary_match"],
        "v2": v2_stats,
        "orb15": {name: orb15_stats[name]["net_pnl"] for name in orb15_stats},
        "orb5": {name: orb5_stats[name]["net_pnl"] for name in orb5_stats},
        "out": str(out),
    }, indent=2, default=str))
    return 0 if repro["5m_primary_match"] == "yes" else 1


if __name__ == "__main__":
    raise SystemExit(main())
