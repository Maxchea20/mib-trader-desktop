"""Failed 5-minute retest, checked in both directions.

This does not replace V3 or rule B. After the same 15-minute range and the
same first 5-minute body breakout, the first later 5-minute candle that
closes back through the broken level is a failed retest. A wick back to the
level is not enough. The fill is the next 1-minute open, before 11:00.

``fade`` trades against the break: a failed upside break is a short, and a
failed downside break is a long. ``with`` takes the original break direction
on that same failure candle. Costs, cutoff, and the 1-minute exit are the
V3 primary preset.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .market import (
    Bar,
    execution_price,
    isolated_liquidation_price,
    resolve_exit,
    size_contracts,
    stop_and_target,
)
from .session import entry_cutoff_ts, ny_date_of_utc_ts
from .v3 import (
    FIVE_MIN,
    ONE_MIN,
    BreakoutV3,
    OpeningRangeV3,
    OrbV3Config,
    _valid_ohlc,
    find_breakout,
    funding_cash,
    is_funding_ts,
    opening_range_v3,
    v3_primary_config,
)


def find_retest_failure(
    bars_5m: list[Bar],
    rng: OpeningRangeV3,
    brk: BreakoutV3,
    *,
    cutoff_ts: int,
) -> Bar | None | str:
    """First 5-minute close back through the broken level, before 11:00.

    The search starts at the breakout's close. The breakout candle itself
    cannot be the failure. Returns ``incomplete`` if a 5-minute slot before
    the cutoff is missing, and None if no close comes back through.
    """
    if not rng.usable or rng.high is None or rng.low is None:
        return None
    by5 = {bar.ts: bar for bar in bars_5m}
    ts = brk.close_ts
    level = rng.high if brk.side == "LONG" else rng.low
    while ts + FIVE_MIN < cutoff_ts:
        bar = by5.get(ts)
        if bar is None or not _valid_ohlc(bar) or bar.close_ts(FIVE_MIN) != ts + FIVE_MIN:
            return "incomplete"
        failed = bar.close < level if brk.side == "LONG" else bar.close > level
        if failed:
            return bar
        ts += FIVE_MIN
    return None


def _opposite(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"


def run_retest_fail_backtest(
    bars_15m: list[Bar],
    bars_5m: list[Bar],
    bars_1m: list[Bar],
    config: OrbV3Config | None = None,
    *,
    direction: str = "fade",
    entry_dates: set[date] | None = None,
    split_name: str | None = None,
) -> list[dict[str, Any]]:
    """One failed-retest trade per session. ``direction`` is ``fade`` or ``with``."""
    if direction not in ("fade", "with"):
        raise ValueError(f"unknown retest-fail direction: {direction}")
    cfg = config or v3_primary_config()
    spec = cfg.spec
    ordered = sorted(bars_1m, key=lambda bar: bar.ts)
    by1 = {bar.ts: bar for bar in ordered}
    dates = sorted(
        {ny_date_of_utc_ts(bar.ts) for bar in list(bars_15m) + list(bars_5m) + ordered}
    )
    equity = float(cfg.starting_equity)
    records: list[dict[str, Any]] = []

    def allowed(day: date) -> bool:
        return entry_dates is None or day in entry_dates

    for day in dates:
        if not allowed(day):
            continue
        rng = opening_range_v3(bars_15m, day, bars_5m=bars_5m)
        cutoff = entry_cutoff_ts(day, cfg.entry_cutoff)
        if not rng.usable:
            continue
        found = find_breakout(bars_5m, rng, cutoff_ts=cutoff)
        if not isinstance(found, BreakoutV3):
            continue
        failure = find_retest_failure(bars_5m, rng, found, cutoff_ts=cutoff)
        base = {
            "config_id": cfg.config_id() + f"|retest-fail-{direction}",
            "strategy": f"ORB-V3-retest-fail-{direction}",
            "split": split_name or "unspecified",
            "ny_date": day.isoformat(),
            "symbol": cfg.symbol,
            "direction": None,
            "status": "unfilled",
            "unfilled_reason": None,
            "or_high": rng.high,
            "or_low": rng.low,
            "breakout_close_ts": found.close_ts,
            "breakout_close": found.close,
            "breakout_side": found.side,
            "ohlc_proxy": True,
            "simulated_fill": True,
            "fee_entry": None,
            "fee_exit": None,
            "fees": None,
            "funding": 0.0,
            "gross_pnl": None,
            "execution_drag": None,
            "net_pnl": None,
            "r_multiple": None,
            "equity_after": None,
            "path_ambiguous": False,
            "exit_reason": None,
            "exit_ts": None,
            "fill_ts": None,
            "entry_signal_ts": None,
        }
        if failure == "incomplete":
            base["unfilled_reason"] = "incomplete_5m"
            records.append(base)
            continue
        if failure is None:
            base["unfilled_reason"] = "no_retest_failure"
            records.append(base)
            continue
        assert isinstance(failure, Bar)
        signal_ts = failure.close_ts(FIVE_MIN)
        fill_bar = by1.get(signal_ts)
        if signal_ts >= cutoff or fill_bar is None or not _valid_ohlc(fill_bar):
            base["unfilled_reason"] = "entry_cutoff" if signal_ts >= cutoff else "no_executable_bar"
            base["entry_signal_ts"] = signal_ts
            records.append(base)
            continue
        side = _opposite(found.side) if direction == "fade" else found.side
        raw_entry = fill_bar.open
        fill = execution_price(
            raw_entry, side, "entry",
            slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec.price_unit,
        )
        sl, tp = stop_and_target(
            side, fill, cfg.stop_loss_fraction, cfg.take_profit_fraction, spec.price_unit,
        )
        valid = sl < fill < tp if side == "LONG" else tp < fill < sl
        if not valid:
            base["unfilled_reason"] = "stop_target_invalid"
            base["direction"] = side
            records.append(base)
            continue
        qty, err = size_contracts(equity, cfg.risk_fraction, fill, sl, cfg.leverage, spec)
        if err:
            base["unfilled_reason"] = err
            base["direction"] = side
            records.append(base)
            continue
        notional = qty * spec.contract_size * fill
        entry_fee = notional * spec.taker_fee_rate
        if notional / cfg.leverage + entry_fee > equity + 1e-9:
            base["unfilled_reason"] = "insufficient_margin"
            base["direction"] = side
            records.append(base)
            continue
        later = [bar for bar in ordered if bar.ts >= fill_bar.ts]
        funding = 0.0
        funding_events = 0
        seen: set[int] = set()
        exit_bar = None
        raw_exit = None
        reason = None
        ambiguous = False
        last_ts = None
        gapped = False
        for bar in later:
            if last_ts is not None and bar.ts > last_ts + ONE_MIN:
                gapped = True
                break
            last_ts = bar.ts
            if (
                cfg.funding_rate
                and bar.ts not in seen
                and is_funding_ts(bar.ts, cfg.funding_interval_hours)
                and fill_bar.ts < bar.ts
            ):
                funding += funding_cash(side, qty, bar.open, cfg.funding_rate, spec.contract_size)
                funding_events += 1
                seen.add(bar.ts)
            res = resolve_exit(side, raw_entry, sl, tp, bar, in_position_from_open=True)
            if res.closed and res.raw_price is not None:
                exit_bar = bar
                raw_exit = res.raw_price
                reason = res.reason or "stop"
                ambiguous = res.ambiguous
                break
        base.update({
            "direction": side,
            "entry_signal_ts": signal_ts,
            "fill_ts": fill_bar.ts,
            "fill_price": fill,
            "raw_entry": raw_entry,
            "stop_price": sl,
            "target_price": tp,
            "qty": qty,
            "notional": notional,
            "failure_close": failure.close,
            "funding": funding,
            "funding_events": funding_events,
        })
        if gapped or exit_bar is None or raw_exit is None:
            base["status"] = "filled_open"
            base["exit_reason"] = "data_gap" if gapped else "open_at_data_end"
            base["fee_entry"] = entry_fee
            base["fees"] = entry_fee
            records.append(base)
            continue
        sign = 1.0 if side == "LONG" else -1.0
        fill_exit = execution_price(
            raw_exit, side, "exit",
            slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec.price_unit,
        )
        gross = (raw_exit - raw_entry) * sign * qty * spec.contract_size
        price_pnl = (fill_exit - fill) * sign * qty * spec.contract_size
        exit_fee = abs(qty * spec.contract_size * fill_exit) * spec.taker_fee_rate
        net = price_pnl - entry_fee - exit_fee + funding
        risk = abs(fill - sl) * qty * spec.contract_size
        equity += net
        base.update({
            "status": "closed",
            "exit_ts": exit_bar.ts,
            "exit_price": fill_exit,
            "raw_exit": raw_exit,
            "exit_reason": reason,
            "path_ambiguous": ambiguous,
            "fee_entry": entry_fee,
            "fee_exit": exit_fee,
            "fees": entry_fee + exit_fee,
            "gross_pnl": gross,
            "execution_drag": gross - price_pnl,
            "net_pnl": net,
            "r_multiple": (net / risk) if risk else None,
            "equity_after": equity,
        })
        records.append(base)
    return records
