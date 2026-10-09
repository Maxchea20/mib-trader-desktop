"""Performance summaries. Unresolved trades are not treated as realized results."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _f(value: Any) -> float:
    return float(value or 0.0)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in records:
        key = (row.get("config_id"), row.get("split"))
        groups[key].append(row)
    out = []
    for (config_id, split), rows in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        out.append(_one(config_id, split, rows))
    return {"by_config_and_split": out}


def _one(config_id: str | None, split: str | None, rows: list[dict]) -> dict[str, Any]:
    signals = [r for r in rows if r.get("signal_ts") is not None or r.get("status") != "unfilled" or r.get("unfilled_reason")]
    # Every row is either a signal attempt or an unresolved management record.
    signal_rows = [r for r in rows if r.get("status") != "filled_open" or r.get("signal_ts") is not None]
    # Count a row as a signal when it was an entry attempt (filled or not).
    attempts = [r for r in rows if r.get("status") in ("unfilled", "closed", "filled_open")]
    filled = [r for r in attempts if r.get("status") in ("closed", "filled_open")]
    closed = [r for r in attempts if r.get("status") == "closed"]
    unresolved = [r for r in attempts if r.get("status") == "filled_open"]
    wins = [r for r in closed if _f(r.get("net_pnl")) > 0]
    losses = [r for r in closed if _f(r.get("net_pnl")) < 0]
    flat = [r for r in closed if _f(r.get("net_pnl")) == 0]
    gross_win = sum(_f(r.get("net_pnl")) for r in wins)
    gross_loss = sum(_f(r.get("net_pnl")) for r in losses)
    net = sum(_f(r.get("net_pnl")) for r in closed)
    gross = sum(_f(r.get("gross_pnl")) for r in closed)
    fees = sum(_f(r.get("fee_entry")) + _f(r.get("fee_exit")) for r in closed)
    drag = sum(_f(r.get("execution_drag")) for r in closed)
    rs = [float(r["r_multiple"]) for r in closed if r.get("r_multiple") is not None]
    equity = None
    peak = None
    max_dd = 0.0
    # Drawdown from realized equity after each close, in exit order.
    ordered = sorted(closed, key=lambda r: (r.get("exit_ts") or 0, r.get("fill_ts") or 0))
    if ordered:
        # Reconstruct from the running equity_after values, which already compound.
        start_guess = _f(ordered[0].get("equity_after")) - _f(ordered[0].get("net_pnl"))
        peak = start_guess
        max_dd = 0.0
        for r in ordered:
            eq = _f(r.get("equity_after"))
            peak = max(peak, eq)
            if peak > 0:
                max_dd = max(max_dd, (peak - eq) / peak)
        equity = _f(ordered[-1].get("equity_after"))
    by_side: dict[str, dict] = {}
    for side in ("LONG", "SHORT"):
        side_rows = [r for r in closed if r.get("direction") == side]
        by_side[side] = {
            "trades": len(side_rows),
            "net_pnl": sum(_f(r.get("net_pnl")) for r in side_rows),
            "win_rate": (sum(1 for r in side_rows if _f(r.get("net_pnl")) > 0) / len(side_rows)) if side_rows else None,
        }
    by_day: dict[str, float] = defaultdict(float)
    for r in closed:
        by_day[str(r.get("ny_date"))] += _f(r.get("net_pnl"))
    unamb = [r for r in closed if not r.get("path_ambiguous")]
    reasons: dict[str, int] = defaultdict(int)
    for r in attempts:
        if r.get("status") == "unfilled":
            reasons[str(r.get("unfilled_reason"))] += 1
        elif r.get("status") == "closed":
            reasons["exit:" + str(r.get("exit_reason"))] += 1
        else:
            reasons["exit:" + str(r.get("exit_reason"))] += 1
    sample = rows[0] if rows else {}
    return {
        "config_id": config_id,
        "split": split,
        "orb": sample.get("orb"),
        "entry_model": sample.get("entry_model"),
        "sides": sample.get("sides"),
        "risk_profile": sample.get("risk_profile"),
        "slippage_profile": sample.get("slippage_profile"),
        "timeframe": sample.get("timeframe"),
        "signals": len(attempts),
        "filled_trades": len(filled),
        "fill_rate": (len(filled) / len(attempts)) if attempts else None,
        "closed_trades": len(closed),
        "unresolved_trades": len(unresolved),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(flat),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "gross_pnl": gross,
        "net_pnl": net,
        "profit_factor": (gross_win / abs(gross_loss)) if gross_loss < 0 else None,
        "expectancy": (net / len(closed)) if closed else None,
        "average_r": (sum(rs) / len(rs)) if rs else None,
        "max_drawdown_fraction": max_dd,
        "ending_equity_realized": equity,
        "fees": fees,
        "execution_drag": drag,
        "ambiguous_closed_trades": sum(1 for r in closed if r.get("path_ambiguous")),
        "unambiguous_closed_trades": len(unamb),
        "unambiguous_net_pnl": sum(_f(r.get("net_pnl")) for r in unamb),
        "unresolved_mtm_pnl": sum(_f(r.get("mtm_pnl")) for r in unresolved),
        "long": by_side["LONG"],
        "short": by_side["SHORT"],
        "pnl_by_ny_date": dict(sorted(by_day.items())),
        "reason_counts": dict(sorted(reasons.items())),
        "ohlc_proxy": bool(sample.get("ohlc_proxy")),
    }
