#!/usr/bin/env python3
"""Same ORB tests with a 100 USDT account, 10% risk, 20% target.

The price stop stays 0.25% and the price target stays 0.50%. That is still
2R, so a filled stop loses about 10% of current equity and a filled target
makes about 20% before fees. Leverage is 50x because 5x cannot hold a 10%
loss on a 0.25% stop: 0.10 / 0.0025 = 40x notional, and the fee needs a
little room past that. The 1,000 USDT / 1% results are not overwritten.
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
from src.orb.v3 import run_v3_backtest, survey_sessions, v3_primary_config, v3b_primary_config


ACCOUNT = dict(
    starting_equity=100.0,
    risk_fraction=0.10,
    leverage=50.0,
    risk_profile="equity100_risk10_target20",
)


def _pct(stats, name):
    row = stats[name]
    # Each split restarts at 100, so net is also the percent of that restart.
    net = row["net_pnl"]
    win = "n/a" if row["win_rate"] is None else f"{100.0 * row['win_rate']:.0f}%"
    avg = "n/a" if row["average_r"] is None else f"{row['average_r']:.2f}"
    return f"| {name} | {row['closed_trades']} | {win} | {net:,.2f} | {net:,.1f}% | {avg} | {100.0 * row['max_drawdown_fraction']:.1f}% |"


def _run_v3(config, bars, groups):
    rows = []
    for name, days in groups.items():
        rows.extend(run_v3_backtest(*bars, config, entry_dates=set(days), split_name=name))
    return rows


def _run_fail(direction, config, bars, groups):
    rows = []
    for name, days in groups.items():
        rows.extend(
            run_retest_fail_backtest(
                *bars, config, direction=direction, entry_dates=set(days), split_name=name,
            )
        )
    return rows


def main() -> int:
    path = resolve_market_db(None) or resolve_market_db("/tmp/market_data_clean.db")
    if path is None:
        print(json.dumps({"error": "no db"}))
        return 2
    bars_15 = load_bars(path, "BTC_USDT", "15m")
    bars_5 = load_bars(path, "BTC_USDT", "5m")
    bars_1 = load_bars(path, "BTC_USDT", "1m")
    bars = (bars_15, bars_5, bars_1)
    sessions = survey_sessions(bars_15, bars_5, bars_1)
    tradable = sorted(date.fromisoformat(row["ny_date"]) for row in sessions if row["tradable"])
    groups = _runner._splits(tradable)
    current_cfg = v3_primary_config(**ACCOUNT)
    b_cfg = v3b_primary_config(**ACCOUNT)
    # Price geometry is unchanged. Only the account bet changed.
    assert current_cfg.stop_loss_fraction == 0.0025
    assert current_cfg.take_profit_fraction == 0.005
    assert current_cfg.starting_equity == 100.0
    assert current_cfg.risk_fraction == 0.10
    runs = {
        "current": _run_v3(current_cfg, bars, groups),
        "rule_b": _run_v3(b_cfg, bars, groups),
        "fade": _run_fail("fade", current_cfg, bars, groups),
        "with_break": _run_fail("with", current_cfg, bars, groups),
    }
    stats = {name: _runner._closed_stats(rows) for name, rows in runs.items()}
    # Confirm the first current trade really risked about 10% of 100.
    first = next(row for row in runs["current"] if row.get("status") == "closed" and row.get("split") == "train")
    risk = abs(float(first["fill_price"]) - float(first["stop_price"])) * float(first["qty"]) * 0.0001
    lines = [
        "# 100 USDT, risk 10%, target 20%",
        "",
        "Same dates and same entry rules. Starting equity is 100 and restarts on every split. Each trade risks 10% of the equity at entry. The target is twice that, about 20% of equity before fees. The price stop is still 0.25% and the price target is still 0.50%. Leverage is 50x so the 10% bet fits. At 5x it would not.",
        "",
        f"First current-entry trade risked {risk:.2f} USDT, which is {risk:.1f}% of the 100 start.",
        "",
        "Net is USDT and also the percent of that split's 100 restart.",
        "",
    ]
    titles = {
        "current": "Current V3 entry",
        "rule_b": "Rule B",
        "fade": "Fade the failed retest",
        "with_break": "Stay with the break after the failed retest",
    }
    for key, title in titles.items():
        lines += [
            f"## {title}",
            "",
            "| Split | Closed | Win rate | Net USDT | Net vs 100 | Avg R | Max DD |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for name in ("train", "validation", "out_of_sample"):
            lines.append(_pct(stats[key], name))
        lines.append("")
    lines += [
        "Fees and slippage still take a win from 2R down to about 1.8R and a loss from 1R to about 1.2R. On this account that is roughly +18% and -12% of the equity at the time of the trade, not a clean +20% and -10%. One loss is larger than the old 3% daily limit, but these rules still take only one trade a day, so that limit does not block the entry.",
        "",
        "Simulated OHLC fills. Not exchange prints. Changing the bet size does not change whether the next week repeats.",
        "",
    ]
    out = BACKEND / "orb_account_100_results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "REPORT.md").write_text("\n".join(lines))
    (out / "summary.json").write_text(json.dumps({
        "account": ACCOUNT,
        "first_trade_risk_usdt": risk,
        "stats": stats,
        "live_orders_sent": 0,
    }, indent=2))
    print(json.dumps({
        "first_trade_risk_usdt": risk,
        "nets": {
            key: {name: stats[key][name]["net_pnl"] for name in ("train", "validation", "out_of_sample")}
            for key in stats
        },
        "trades": {
            key: {name: stats[key][name]["closed_trades"] for name in ("train", "validation", "out_of_sample")}
            for key in stats
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
