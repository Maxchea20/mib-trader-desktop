#!/usr/bin/env python3
"""Current V3 entry with the stop on an opening-range line.

Account is 100 USDT, 10% of equity at risk, target twice the stop distance.
The entry rule is unchanged. range_far stops a long at OR_LOW and a short at
OR_HIGH. range_near stops a long at OR_HIGH and a short at OR_LOW.
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
from src.orb.v3 import run_v3_backtest, survey_sessions, v3_primary_config


def _rows(stop_at, bars, groups):
    cfg = v3_primary_config(
        starting_equity=100.0,
        risk_fraction=0.10,
        leverage=50.0,
        stop_at=stop_at,
        risk_profile=f"equity100_stop_{stop_at}",
    )
    out = []
    for name, days in groups.items():
        out.extend(run_v3_backtest(*bars, cfg, entry_dates=set(days), split_name=name))
    return out


def _distance(rows):
    closed = [r for r in rows if r.get("status") == "closed"]
    dists = []
    capped = 0
    for row in closed:
        fill = float(row["fill_price"])
        stop = float(row["stop_price"])
        dist = abs(fill - stop) / fill
        dists.append(dist)
        equity_before = float(row["equity_after"]) - float(row["net_pnl"])
        risk = dist * float(row["qty"]) * 0.0001 * fill
        if equity_before > 0 and risk < 0.09 * equity_before:
            capped += 1
    dists.sort()
    mid = dists[len(dists) // 2] if dists else None
    return {
        "median_stop_pct": None if mid is None else mid * 100,
        "capped_below_10pct_risk": capped,
        "closed": len(closed),
    }


def main() -> int:
    path = resolve_market_db(None) or resolve_market_db("/tmp/market_data_clean.db")
    if path is None:
        print(json.dumps({"error": "no db"}))
        return 2
    bars = (
        load_bars(path, "BTC_USDT", "15m"),
        load_bars(path, "BTC_USDT", "5m"),
        load_bars(path, "BTC_USDT", "1m"),
    )
    sessions = survey_sessions(*bars)
    tradable = sorted(date.fromisoformat(row["ny_date"]) for row in sessions if row["tradable"])
    groups = _runner._splits(tradable)
    payload = {}
    for mode in ("fixed", "range_near", "range_far"):
        rows = _rows(mode, bars, groups)
        payload[mode] = {
            "stats": _runner._closed_stats(rows),
            "distance": _distance(rows),
        }
    print(json.dumps(payload, indent=2, default=str))
    out = BACKEND / "orb_range_stop_results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
