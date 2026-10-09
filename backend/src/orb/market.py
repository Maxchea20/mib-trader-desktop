"""Opening-range construction, execution prices, and exit resolution.

OHLC candles cannot reconstruct the path inside a bar. Where the path is
ambiguous this module takes the worse outcome and marks it. It does not
invent a tick print.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, time

from .contract_spec import BTC_USDT, ContractSpec
from .session import (
    ORB_MINUTES,
    entry_cutoff_ts,
    is_nyse_session_day,
    ny_date_of_utc_ts,
    range_bounds_ts,
)


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def close_ts(self, bar_seconds: int) -> int:
        return int(self.ts) + int(bar_seconds)


@dataclass(frozen=True)
class OpeningRange:
    ny_date: date
    orb: str
    complete: bool
    reason: str
    high: float | None
    low: float | None
    start_ts: int
    end_ts: int
    bar_count: int
    expected_bars: int


@dataclass(frozen=True)
class ExitResult:
    closed: bool
    raw_price: float | None
    reason: str | None
    ambiguous: bool


def build_opening_ranges(
    bars: list[Bar],
    orb: str,
    bar_seconds: int,
    *,
    require_nyse_day: bool = True,
    cutoff: time | None = None,
) -> dict[date, OpeningRange]:
    """Range high/low from candles fully inside [09:30, 09:30+duration).

    A candle that starts before the window or ends after it is excluded.
    A missing candle inside the window makes the day unusable. No partial
    range is substituted.
    """
    if orb not in ORB_MINUTES:
        raise ValueError(f"unknown opening range {orb}")
    minutes = ORB_MINUTES[orb]
    by_ts: dict[int, Bar] = {}
    for bar in bars:
        if bar.ts in by_ts:
            raise ValueError(f"duplicate candle ts {bar.ts}")
        by_ts[bar.ts] = bar
    dates = sorted({ny_date_of_utc_ts(bar.ts) for bar in bars})
    out: dict[date, OpeningRange] = {}
    for day in dates:
        start_ts, end_ts = range_bounds_ts(day, minutes)
        expected = 0
        if bar_seconds > 0 and (end_ts - start_ts) % bar_seconds == 0:
            expected = (end_ts - start_ts) // bar_seconds
        base = dict(
            ny_date=day,
            orb=orb,
            high=None,
            low=None,
            start_ts=start_ts,
            end_ts=end_ts,
            bar_count=0,
            expected_bars=expected,
        )
        if require_nyse_day and not is_nyse_session_day(day):
            out[day] = OpeningRange(complete=False, reason="not_nyse_session_day", **base)
            continue
        if cutoff is not None and entry_cutoff_ts(day, cutoff) <= end_ts:
            out[day] = OpeningRange(complete=False, reason="cutoff_not_after_range", **base)
            continue
        if expected <= 0:
            out[day] = OpeningRange(
                complete=False, reason="bar_does_not_fit_inside_range", **base
            )
            continue
        highs: list[float] = []
        lows: list[float] = []
        missing = False
        for i in range(expected):
            ts = start_ts + i * bar_seconds
            bar = by_ts.get(ts)
            if bar is None or bar.close_ts(bar_seconds) > end_ts or bar.ts < start_ts:
                missing = True
                break
            highs.append(bar.high)
            lows.append(bar.low)
        if missing:
            out[day] = OpeningRange(complete=False, reason="incomplete_range_candles", **base)
            continue
        out[day] = OpeningRange(
            complete=True,
            reason="ok",
            high=max(highs),
            low=min(lows),
            bar_count=expected,
            **{k: v for k, v in base.items() if k not in ("high", "low", "bar_count")},
        )
    return out


def snap_worse(price: float, unit: float, *, worse_up: bool) -> float:
    if unit <= 0:
        return float(price)
    steps = price / unit
    if worse_up:
        snapped = math.ceil(steps - 1e-9) * unit
    else:
        snapped = math.floor(steps + 1e-9) * unit
    return float(round(snapped, 10))


def execution_price(
    ideal: float,
    side: str,
    kind: str,
    *,
    slippage_bps: float,
    spread_usd: float,
    unit: float,
) -> float:
    """Move the ideal price against the trader, then snap to the price tick.

    `kind` is `entry` or `exit`. Half the configured spread is charged on
    each fill. Slippage is `slippage_bps` of the ideal price, also adverse.
    """
    half_spread = max(0.0, spread_usd) / 2.0
    slip = abs(ideal) * max(0.0, slippage_bps) / 10_000.0
    drag = half_spread + slip
    pay_more = (kind == "entry" and side == "LONG") or (kind == "exit" and side == "SHORT")
    raw = ideal + drag if pay_more else ideal - drag
    if raw <= 0:
        raw = unit if unit > 0 else ideal
    return snap_worse(raw, unit, worse_up=pay_more)


def stop_and_target(
    side: str,
    fill_price: float,
    stop_fraction: float,
    target_fraction: float,
    unit: float,
) -> tuple[float, float]:
    """Percentage stop and target off the actual fill, snapped adversely."""
    if side == "LONG":
        sl = snap_worse(fill_price * (1.0 - stop_fraction), unit, worse_up=False)
        tp = snap_worse(fill_price * (1.0 + target_fraction), unit, worse_up=False)
    else:
        sl = snap_worse(fill_price * (1.0 + stop_fraction), unit, worse_up=True)
        tp = snap_worse(fill_price * (1.0 - target_fraction), unit, worse_up=True)
    return sl, tp


def size_contracts(
    equity: float,
    risk_fraction: float,
    entry_price: float,
    stop_price: float,
    leverage: float,
    spec: ContractSpec = BTC_USDT,
) -> tuple[int, str | None]:
    """Floor to the contract step. Never round risk up. Reject illegal size.

    Leverage is not silently reduced. If the requested leverage is outside
    the contract, or the capped quantity is below minVol, the order is rejected.
    """
    if equity <= 0:
        return 0, "no_equity"
    if leverage < spec.min_leverage or leverage > spec.max_leverage:
        return 0, "leverage_outside_contract"
    stop_dist = abs(entry_price - stop_price)
    if entry_price <= 0 or stop_dist <= 0 or spec.contract_size <= 0:
        return 0, "stop_distance_zero"
    risk_usd = equity * risk_fraction
    raw_qty = risk_usd / (stop_dist * spec.contract_size)
    max_notional = equity * leverage
    max_qty = max_notional / (entry_price * spec.contract_size)
    raw_qty = min(raw_qty, max_qty)
    steps = math.floor((raw_qty / spec.vol_unit) + 1e-9)
    qty = steps * spec.vol_unit
    if qty > spec.max_vol:
        qty = math.floor(spec.max_vol / spec.vol_unit) * spec.vol_unit
    if qty < spec.min_vol:
        return 0, "below_min_vol"
    if leverage > spec.max_leverage_for_qty(qty) + 1e-9:
        return 0, "leverage_above_risk_tier"
    return int(qty), None


def resolve_exit(
    side: str,
    entry_price: float,
    sl: float,
    tp: float,
    bar: Bar,
    *,
    in_position_from_open: bool,
) -> ExitResult:
    """Resolve SL/TP against one bar.

    `in_position_from_open` is true when the fill happened at this bar's open
    or the position was already open. It is false for an intrabar stop-entry
    that triggers only if price trades through `entry_price` after the open.

    A stop-market that gaps through the stop fills at the open (the market
    has already passed the stop), then the usual extra slippage is applied
    by the caller. A target that gaps through fills at the target, not at
    the better open. If both stops are traded and the order is unknown, the
    stop is taken.
    """
    if side == "LONG":
        return _exit_long(entry_price, sl, tp, bar, in_position_from_open)
    return _exit_short(entry_price, sl, tp, bar, in_position_from_open)


def _exit_long(entry: float, sl: float, tp: float, bar: Bar, from_open: bool) -> ExitResult:
    if from_open:
        if bar.open <= sl:
            return ExitResult(True, bar.open, "stop_gap", False)
        if bar.open >= tp:
            return ExitResult(True, tp, "take_profit_gap_capped", False)
        hit_sl = bar.low <= sl
        hit_tp = bar.high >= tp
        if hit_sl and hit_tp:
            return ExitResult(True, sl, "stop", True)
        if hit_sl:
            return ExitResult(True, sl, "stop", False)
        if hit_tp:
            return ExitResult(True, tp, "take_profit", False)
        return ExitResult(False, None, None, False)
    # Intrabar stop-entry. Pre-entry prints are not exits.
    if bar.high < entry and bar.open < entry:
        return ExitResult(False, None, None, False)
    # Filled from the open because the bar opened already through the level.
    if bar.open >= entry:
        return _exit_long(entry, sl, tp, bar, True)
    hit_sl = bar.low <= sl
    hit_tp = bar.high >= tp
    if hit_sl:
        # Low may be before the entry. Taking the stop is the adverse path.
        return ExitResult(True, sl, "stop", True)
    if hit_tp:
        # Open is below entry and the low never reached the stop, so a print
        # at the target must have crossed the entry first.
        return ExitResult(True, tp, "take_profit", False)
    return ExitResult(False, None, None, False)


def _exit_short(entry: float, sl: float, tp: float, bar: Bar, from_open: bool) -> ExitResult:
    if from_open:
        if bar.open >= sl:
            return ExitResult(True, bar.open, "stop_gap", False)
        if bar.open <= tp:
            return ExitResult(True, tp, "take_profit_gap_capped", False)
        hit_sl = bar.high >= sl
        hit_tp = bar.low <= tp
        if hit_sl and hit_tp:
            return ExitResult(True, sl, "stop", True)
        if hit_sl:
            return ExitResult(True, sl, "stop", False)
        if hit_tp:
            return ExitResult(True, tp, "take_profit", False)
        return ExitResult(False, None, None, False)
    if bar.low > entry and bar.open > entry:
        return ExitResult(False, None, None, False)
    if bar.open <= entry:
        return _exit_short(entry, sl, tp, bar, True)
    hit_sl = bar.high >= sl
    hit_tp = bar.low <= tp
    if hit_sl:
        return ExitResult(True, sl, "stop", True)
    if hit_tp:
        return ExitResult(True, tp, "take_profit", False)
    return ExitResult(False, None, None, False)


def isolated_liquidation_price(side: str, fill: float, leverage: float, mmr: float) -> float:
    """Approximate isolated liquidation. Not MEXC's official bankruptcy price.

    Loss room is initial margin minus maintenance margin, i.e.
    notional * (1/leverage - mmr).
    """
    room = max(0.0, (1.0 / leverage) - mmr)
    if side == "LONG":
        return fill * (1.0 - room)
    return fill * (1.0 + room)
