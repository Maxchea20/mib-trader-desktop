"""MIB ORB V3: 09:30-09:45 New York 15-minute range, 5-minute body breakout, 1-minute continuation.

V2 used the 09:30-09:35 five-minute candle as the opening range. That was
wrong. This module does not change V2 or the original ORB engine.

Sequence, America/New_York only:

1. The 15-minute candle that opens at 09:30 and closes at 09:45 sets OR_HIGH
   and OR_LOW. Those two prices stay fixed. Later candles cannot move them.
   Nothing is entered before that candle has closed.
2. From 09:45 onward, the first completed 5-minute candle whose close is
   strictly outside the range is the session's only direction. A wick through
   a level with the close back inside is not a breakout. The breakout may be
   any later 5-minute candle before the cutoff. It is not required to be the
   09:45 candle.
3. After that 5-minute close, the first completed 1-minute continuation in
   the same direction is the entry signal. LONG needs a bullish minute
   (close > open) that also closes strictly above OR_HIGH. SHORT needs a
   bearish minute (close < open) that closes strictly below OR_LOW. There is
   no retest. A 1-minute candle that completed at or before the 5-minute
   confirmation is ignored.
4. The fill is the next 1-minute bar's open, and only when that open is
   strictly before 11:00. The signal close is not a fill. An open position
   keeps its stop and target after 11:00.

Fills are an OHLC simulation. The database has no prints inside a bar.
No order is submitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

from .contract_spec import BTC_USDT, ContractSpec
from .engine import primary_config
from .market import (
    Bar,
    execution_price,
    isolated_liquidation_price,
    resolve_exit,
    size_contracts,
    stop_and_target,
)
from .session import (
    DEFAULT_ENTRY_CUTOFF,
    entry_cutoff_ts,
    is_nyse_session_day,
    ny_date_of_utc_ts,
    range_bounds_ts,
)

FIFTEEN_MIN = 900
FIVE_MIN = 300
ONE_MIN = 60

# Live MEXC BTC_USDT snapshot, 2026-10-09 13:21:54 UTC.
# GET https://contract.mexc.com/api/v1/contract/funding_rate/BTC_USDT
# fundingRate 0.000015, collectCycle 8 hours. Not the historical path.
FUNDING_RATE_SNAPSHOT = 0.000015
FUNDING_AS_OF = "2026-10-09T13:21:54Z"


@dataclass(frozen=True)
class OrbV3Config:
    """Same account and cost assumptions as the pre-declared ORB primary preset."""

    stop_loss_fraction: float = 0.0025
    take_profit_fraction: float = 0.005
    risk_fraction: float = 0.01
    leverage: float = 5.0
    max_daily_loss_fraction: float = 0.03
    max_open_positions: int = 1
    starting_equity: float = 1000.0
    slippage_bps: float = 1.0
    spread_usd: float = 0.10
    slippage_profile: str = "normal"
    risk_profile: str = "baseline_25bps_2R"
    symbol: str = "BTC_USDT"
    entry_cutoff: time = DEFAULT_ENTRY_CUTOFF
    compound: bool = True
    spec: ContractSpec = BTC_USDT
    funding_rate: float = FUNDING_RATE_SNAPSHOT
    funding_interval_hours: int = 8
    funding_as_of: str = FUNDING_AS_OF
    # "range" is the original V3 entry. "next_5m" is rule B and is opt-in.
    continuation: str = "range"

    def config_id(self) -> str:
        entry = "15m-range+5m-body+1m-continuation"
        if self.continuation == "next_5m":
            entry = "15m-range+5m-body+1m-next5m-breakout-close"
        return "|".join(
            (
                self.symbol,
                "ORB-V3",
                entry,
                self.risk_profile,
                self.slippage_profile,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id(),
            "strategy": "ORB-V3",
            "definition": (
                "09:30-09:45 NY 15m range, first 5m body breakout from 09:45, "
                "1m continuation only inside the next 5m candle and only "
                "through that breakout candle's close"
                if self.continuation == "next_5m"
                else
                "09:30-09:45 NY 15m range, first 5m body breakout from 09:45, "
                "next 1m direct continuation"
            ),
            "stop_loss_fraction": self.stop_loss_fraction,
            "take_profit_fraction": self.take_profit_fraction,
            "risk_fraction": self.risk_fraction,
            "leverage": self.leverage,
            "max_daily_loss_fraction": self.max_daily_loss_fraction,
            "max_open_positions": self.max_open_positions,
            "starting_equity": self.starting_equity,
            "slippage_bps": self.slippage_bps,
            "spread_usd": self.spread_usd,
            "slippage_profile": self.slippage_profile,
            "risk_profile": self.risk_profile,
            "entry_cutoff_local": self.entry_cutoff.strftime("%H:%M:%S"),
            "compound": self.compound,
            "one_entry_per_session": True,
            "direction_locked": True,
            "live_submit": False,
            "fills": "ohlc_simulation",
            "funding_rate": self.funding_rate,
            "funding_interval_hours": self.funding_interval_hours,
            "funding_as_of": self.funding_as_of,
            "continuation": self.continuation,
            "funding_note": (
                "Flat snapshot. Positive rate: longs pay, shorts receive, "
                "at 00:00, 08:00 and 16:00 UTC, on mark = that minute's open. "
                "Not the historical funding path."
            ),
            "contract": self.spec.to_dict(),
        }


def v3_primary_config(**overrides: Any) -> OrbV3Config:
    """Risk, fees and slippage copied from the original primary preset. Not tuned."""
    base = primary_config()
    fields = dict(
        stop_loss_fraction=base.stop_loss_fraction,
        take_profit_fraction=base.take_profit_fraction,
        risk_fraction=base.risk_fraction,
        leverage=base.leverage,
        max_daily_loss_fraction=base.max_daily_loss_fraction,
        max_open_positions=base.max_open_positions,
        starting_equity=base.starting_equity,
        slippage_bps=base.slippage_bps,
        spread_usd=base.spread_usd,
        slippage_profile=base.slippage_profile,
        risk_profile=base.risk_profile,
        symbol=base.symbol,
        entry_cutoff=base.entry_cutoff,
        compound=base.compound,
        spec=base.spec,
        funding_rate=FUNDING_RATE_SNAPSHOT,
        funding_interval_hours=8,
        funding_as_of=FUNDING_AS_OF,
    )
    fields.update(overrides)
    mode = fields.get("continuation", "range")
    if mode not in ("range", "next_5m"):
        raise ValueError(f"unknown continuation: {mode}")
    return OrbV3Config(**fields)


def v3b_primary_config(**overrides: Any) -> OrbV3Config:
    """Rule B. Same costs as the V3 primary. Only the 1-minute trigger differs."""
    fields = dict(overrides)
    fields["continuation"] = "next_5m"
    return v3_primary_config(**fields)


@dataclass(frozen=True)
class OpeningRangeV3:
    ny_date: date
    status: str
    high: float | None
    low: float | None
    start_ts: int
    end_ts: int
    candle_open: float | None = None
    candle_high: float | None = None
    candle_low: float | None = None
    candle_close: float | None = None

    @property
    def usable(self) -> bool:
        return (
            self.status == "ok"
            and self.high is not None
            and self.low is not None
            and self.high >= self.low
        )


@dataclass(frozen=True)
class BreakoutV3:
    side: str
    candle_ts: int
    close_ts: int
    open: float
    high: float
    low: float
    close: float


def _index(bars: list[Bar], timeframe: str) -> dict[int, Bar]:
    out: dict[int, Bar] = {}
    for bar in bars:
        if bar.ts in out:
            raise ValueError(f"duplicate {timeframe} candle ts {bar.ts}")
        out[bar.ts] = bar
    return out


def _valid_ohlc(bar: Bar) -> bool:
    return (
        bar.high >= bar.low
        and bar.high >= bar.open
        and bar.high >= bar.close
        and bar.low <= bar.open
        and bar.low <= bar.close
    )


def is_funding_ts(ts: int, interval_hours: int = 8) -> bool:
    """True at MEXC settlement minutes: 00:00, 08:00 and 16:00 UTC when interval is 8."""
    if interval_hours <= 0:
        return False
    moment = datetime.fromtimestamp(int(ts), timezone.utc)
    return moment.minute == 0 and moment.second == 0 and moment.hour % interval_hours == 0


def funding_cash(side: str, qty: float, mark: float, rate: float, contract_size: float) -> float:
    """Account cash at one settlement. Positive rate: longs pay, shorts receive."""
    cash = qty * contract_size * mark * rate
    return -cash if side == "LONG" else cash


def _matches(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-6


def opening_range_v3(
    bars_15m: list[Bar],
    day: date,
    *,
    bars_5m: list[Bar] | None = None,
    by15: dict[int, Bar] | None = None,
    by5: dict[int, Bar] | None = None,
) -> OpeningRangeV3:
    """The 09:30-09:45 New York 15-minute candle. Later candles cannot move it.

    The native 15-minute candle is the range. The first five-minute candle is
    not a substitute. When the three 5-minute candles inside the window all
    exist, they must rebuild that same OHLC or the session is skipped. A
    partial 5-minute set is not used to invent a different high or low.
    """
    start_ts, end_ts = range_bounds_ts(day, 15)
    blank = dict(
        ny_date=day,
        high=None,
        low=None,
        start_ts=start_ts,
        end_ts=end_ts,
        candle_open=None,
        candle_high=None,
        candle_low=None,
        candle_close=None,
    )
    if not is_nyse_session_day(day):
        return OpeningRangeV3(status="not_nyse_session_day", **blank)
    by15 = _index(bars_15m, "15m") if by15 is None else by15
    candle = by15.get(start_ts)
    if candle is None or candle.close_ts(FIFTEEN_MIN) != end_ts:
        return OpeningRangeV3(status="missing_opening_range", **blank)
    if not _valid_ohlc(candle) or candle.high < candle.low:
        return OpeningRangeV3(status="invalid_opening_range", **blank)
    if bars_5m is not None or by5 is not None:
        by5 = _index(bars_5m, "5m") if by5 is None else by5
        assert by5 is not None
        parts = [by5.get(start_ts + i * FIVE_MIN) for i in range(3)]
        if all(part is not None for part in parts):
            packed = [part for part in parts if part is not None]
            if any(not _valid_ohlc(part) for part in packed):
                return OpeningRangeV3(status="invalid_opening_range", **blank)
            agg_open = packed[0].open
            agg_close = packed[-1].close
            agg_high = max(part.high for part in packed)
            agg_low = min(part.low for part in packed)
            if not (
                _matches(agg_open, candle.open)
                and _matches(agg_close, candle.close)
                and _matches(agg_high, candle.high)
                and _matches(agg_low, candle.low)
            ):
                return OpeningRangeV3(status="opening_range_mismatch", **blank)
    return OpeningRangeV3(
        status="ok",
        high=candle.high,
        low=candle.low,
        candle_open=candle.open,
        candle_high=candle.high,
        candle_low=candle.low,
        candle_close=candle.close,
        **{k: v for k, v in blank.items() if k in ("ny_date", "start_ts", "end_ts")},
    )


def _continues_breakout_close(bar: Bar, side: str, breakout_close: float) -> bool:
    """Rule B. A 1-minute body must close beyond the 5-minute breakout close.

    Closing back over the opening range is not enough. The candle must be
    green for a long and red for a short, and the close is strict.
    """
    if side == "LONG":
        return bar.close > bar.open and bar.close > breakout_close
    if side == "SHORT":
        return bar.close < bar.open and bar.close < breakout_close
    return False


def _body_breakout(bar: Bar, or_high: float, or_low: float) -> str | None:
    """Close must finish strictly beyond the level. A wick is not enough."""
    if or_high < or_low:
        return None
    long_ok = bar.close > or_high
    short_ok = bar.close < or_low
    if long_ok and short_ok:
        return "ambiguous"
    if long_ok:
        return "LONG"
    if short_ok:
        return "SHORT"
    return None


def find_breakout(
    bars_5m: list[Bar],
    rng: OpeningRangeV3,
    *,
    cutoff_ts: int,
    by5: dict[int, Bar] | None = None,
) -> BreakoutV3 | None | str:
    """First completed 5-minute body breakout at or after 09:45 and before 11:00.

    The search starts at the range close. Candles inside 09:30-09:45 are not
    candidates. Returns None when no candle breaks, or ``incomplete`` when a
    5-minute slot before the cutoff is missing. A later candle does not fill
    that hole. A candle that closes at exactly 11:00 is not used: the signal
    would be known at the cutoff.
    """
    if not rng.usable or rng.high is None or rng.low is None:
        return None
    by5 = _index(bars_5m, "5m") if by5 is None else by5
    ts = rng.end_ts
    while ts + FIVE_MIN < cutoff_ts:
        if ts < rng.end_ts:
            return "incomplete"
        bar = by5.get(ts)
        if bar is None or not _valid_ohlc(bar) or bar.close_ts(FIVE_MIN) != ts + FIVE_MIN:
            return "incomplete"
        side = _body_breakout(bar, rng.high, rng.low)
        if side == "ambiguous":
            return "ambiguous"
        if side is not None:
            return BreakoutV3(
                side=side,
                candle_ts=bar.ts,
                close_ts=bar.close_ts(FIVE_MIN),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
            )
        ts += FIVE_MIN
    return None


def entry_window_complete(bars_1m: list[Bar], day: date, *, by1: dict[int, Bar] | None = None) -> bool:
    """True when every 1-minute bar from 09:45 through 10:59 New York exists.

    10:59 is the last open that is still strictly before 11:00. Missing
    minutes are not synthesized from 5-minute candles.
    """
    if not is_nyse_session_day(day):
        return False
    _, start = range_bounds_ts(day, 15)
    cutoff = entry_cutoff_ts(day)
    by1 = _index(bars_1m, "1m") if by1 is None else by1
    ts = start
    while ts < cutoff:
        bar = by1.get(ts)
        if bar is None or not _valid_ohlc(bar):
            return False
        ts += ONE_MIN
    return True


def breakout_path_complete(bars_5m: list[Bar], day: date, *, by5: dict[int, Bar] | None = None) -> bool:
    """True when every 5-minute breakout slot from 09:45 through 10:50 exists."""
    if not is_nyse_session_day(day):
        return False
    _, start = range_bounds_ts(day, 15)
    cutoff = entry_cutoff_ts(day)
    by5 = _index(bars_5m, "5m") if by5 is None else by5
    ts = start
    while ts + FIVE_MIN < cutoff:
        bar = by5.get(ts)
        if bar is None or not _valid_ohlc(bar):
            return False
        ts += FIVE_MIN
    return True


def _blank(cfg: OrbV3Config, day: date, split: str, rng: OpeningRangeV3 | None, **fields: Any) -> dict[str, Any]:
    row = {
        "config_id": cfg.config_id(),
        "strategy": "ORB-V3",
        "split": split,
        "ny_date": day.isoformat(),
        "symbol": cfg.symbol,
        "direction": None,
        "status": "unfilled",
        "unfilled_reason": None,
        "or_high": None if rng is None else rng.high,
        "or_low": None if rng is None else rng.low,
        "or_open": None if rng is None else rng.candle_open,
        "or_close": None if rng is None else rng.candle_close,
        "range_start_ts": None if rng is None else rng.start_ts,
        "range_end_ts": None if rng is None else rng.end_ts,
        "breakout_candle_ts": None,
        "breakout_close_ts": None,
        "breakout_open": None,
        "breakout_high": None,
        "breakout_low": None,
        "breakout_close": None,
        "entry_signal_ts": None,
        "entry_signal_close": None,
        "signal_price": None,
        "order_price": None,
        "order_type": None,
        "info_known_ts": None,
        "fill_ts": None,
        "fill_price": None,
        "raw_entry": None,
        "fill_outside_bar": None,
        "ohlc_proxy": True,
        "simulated_fill": True,
        "qty": None,
        "notional": None,
        "leverage": cfg.leverage,
        "stop_price": None,
        "target_price": None,
        "exit_ts": None,
        "exit_price": None,
        "raw_exit": None,
        "exit_reason": None,
        "path_ambiguous": None,
        "fee_entry": None,
        "fee_exit": None,
        "fees": None,
        "funding": None,
        "funding_events": None,
        "gross_pnl": None,
        "net_pnl": None,
        "execution_drag": None,
        "r_multiple": None,
        "equity_after": None,
        "mtm_pnl": None,
        "same_bar_exit": None,
        "risk_profile": cfg.risk_profile,
        "slippage_profile": cfg.slippage_profile,
    }
    row.update(fields)
    return row


def _breakout_fields(brk: BreakoutV3 | None) -> dict[str, Any]:
    if brk is None:
        return {}
    return {
        "direction": brk.side,
        "breakout_candle_ts": brk.candle_ts,
        "breakout_close_ts": brk.close_ts,
        "breakout_open": brk.open,
        "breakout_high": brk.high,
        "breakout_low": brk.low,
        "breakout_close": brk.close,
    }


def _signal_fields(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "breakout_candle_ts": order.get("breakout_candle_ts"),
        "breakout_close_ts": order.get("breakout_close_ts"),
        "breakout_open": order.get("breakout_open"),
        "breakout_high": order.get("breakout_high"),
        "breakout_low": order.get("breakout_low"),
        "breakout_close": order.get("breakout_close"),
        "entry_signal_ts": order.get("entry_signal_ts"),
        "entry_signal_close": order.get("entry_signal_close"),
        "signal_price": order.get("signal_price"),
        "order_price": order.get("order_price"),
        "order_type": order.get("order_type"),
        "info_known_ts": order.get("info_known_ts"),
    }


def run_v3_backtest(
    bars_15m: list[Bar],
    bars_5m: list[Bar],
    bars_1m: list[Bar],
    config: OrbV3Config | None = None,
    *,
    entry_dates: set[date] | None = None,
    split_name: str | None = None,
) -> list[dict[str, Any]]:
    """Chronological V3 backtest.

    The 15-minute range is known at 09:45. The 5-minute breakout is known
    only at that candle's close. The 1-minute continuation must close strictly
    after that, and the fill is the next 1-minute bar's open. A bar never
    reads a later bar.
    """
    cfg = config or v3_primary_config()
    if cfg.continuation not in ("range", "next_5m"):
        raise ValueError(f"unknown continuation: {cfg.continuation}")
    spec = cfg.spec
    by15 = _index(list(bars_15m), "15m")
    by5 = _index(list(bars_5m), "5m")
    ordered = sorted(bars_1m, key=lambda bar: bar.ts)
    by1 = _index(ordered, "1m")
    dates = sorted(
        {
            ny_date_of_utc_ts(bar.ts)
            for bar in list(by15.values()) + list(by5.values()) + ordered
        }
    )
    ranges = {
        day: opening_range_v3(bars_15m, day, bars_5m=bars_5m, by15=by15, by5=by5)
        for day in dates
    }
    breakouts: dict[date, BreakoutV3 | None] = {}
    blocked: dict[date, str] = {}
    for day, rng in ranges.items():
        if not rng.usable:
            continue
        found = find_breakout(
            bars_5m, rng, cutoff_ts=entry_cutoff_ts(day, cfg.entry_cutoff), by5=by5,
        )
        if found == "incomplete":
            blocked[day] = "incomplete_5m_breakout_path"
            breakouts[day] = None
        elif found == "ambiguous":
            blocked[day] = "ambiguous_both_sides"
            breakouts[day] = None
        else:
            breakouts[day] = found

    equity = float(cfg.starting_equity)
    position: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    current_day: date | None = None
    day_start_equity = equity
    realized_today = 0.0
    signaled: set[date] = set()
    records: list[dict[str, Any]] = []

    def split_of(day: date) -> str:
        if split_name is not None:
            return split_name
        return "unspecified"

    def allowed(day: date) -> bool:
        return entry_dates is None or day in entry_dates

    def remember(day: date, rng: OpeningRangeV3 | None, **fields: Any) -> None:
        records.append(_blank(cfg, day, split_of(day), rng, **fields))

    def flush_missed(day: date | None) -> None:
        if day is None or not allowed(day) or day in signaled or day in blocked:
            return
        rng = ranges.get(day)
        brk = breakouts.get(day)
        if rng is None or not rng.usable or brk is None:
            return
        signaled.add(day)
        remember(
            day, rng, status="unfilled", unfilled_reason="no_1m_continuation",
            **_breakout_fields(brk),
        )

    def try_open(day: date, rng: OpeningRangeV3, bar: Bar, pending_order: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal equity
        side = pending_order["side"]
        if position is not None or cfg.max_open_positions < 1:
            remember(
                day, rng, status="unfilled", unfilled_reason="max_open_positions",
                direction=side, **_signal_fields(pending_order),
            )
            return None
        loss_limit = -cfg.max_daily_loss_fraction * day_start_equity
        if realized_today <= loss_limit:
            remember(
                day, rng, status="unfilled", unfilled_reason="max_daily_loss",
                direction=side, **_signal_fields(pending_order),
            )
            return None
        raw_entry = bar.open
        fill = execution_price(
            raw_entry, side, "entry",
            slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec.price_unit,
        )
        sl, tp = stop_and_target(
            side, fill, cfg.stop_loss_fraction, cfg.take_profit_fraction, spec.price_unit,
        )
        valid = sl < fill < tp if side == "LONG" else tp < fill < sl
        if not valid:
            remember(
                day, rng, status="unfilled", unfilled_reason="stop_target_invalid",
                direction=side, fill_price=fill, stop_price=sl, target_price=tp,
                raw_entry=raw_entry, **_signal_fields(pending_order),
            )
            return None
        sizing_equity = equity if cfg.compound else cfg.starting_equity
        qty, err = size_contracts(sizing_equity, cfg.risk_fraction, fill, sl, cfg.leverage, spec)
        if err:
            remember(
                day, rng, status="unfilled", unfilled_reason=err, direction=side,
                fill_price=fill, stop_price=sl, target_price=tp, raw_entry=raw_entry,
                **_signal_fields(pending_order),
            )
            return None
        notional = qty * spec.contract_size * fill
        entry_fee = notional * spec.taker_fee_rate
        margin = notional / cfg.leverage
        if margin + entry_fee > equity + 1e-9:
            remember(
                day, rng, status="unfilled", unfilled_reason="insufficient_margin",
                direction=side, fill_price=fill, qty=qty, notional=notional,
                stop_price=sl, target_price=tp, raw_entry=raw_entry,
                **_signal_fields(pending_order),
            )
            return None
        outside = fill < bar.low - 1e-9 or fill > bar.high + 1e-9
        return {
            "side": side,
            "qty": qty,
            "raw_entry": raw_entry,
            "fill": fill,
            "sl": sl,
            "tp": tp,
            "entry_fee": entry_fee,
            "notional": notional,
            "outside": outside,
            "fill_ts": bar.ts,
            "liq": isolated_liquidation_price(side, fill, cfg.leverage, spec.maintenance_margin_rate),
            "risk_usd": abs(fill - sl) * qty * spec.contract_size,
            "day": day,
            "rng": rng,
            "pending": pending_order,
            "same_bar_exit": False,
            "path_ambiguous": False,
            "funding": 0.0,
            "funding_events": 0,
            "funding_ts": set(),
        }

    def close_position(pos: dict[str, Any], bar: Bar, raw_exit: float, reason: str, ambiguous: bool) -> None:
        nonlocal equity, position, realized_today
        side = pos["side"]
        direction = 1.0 if side == "LONG" else -1.0
        liq_worse = raw_exit <= pos["liq"] if side == "LONG" else raw_exit >= pos["liq"]
        qty = pos["qty"]
        if liq_worse:
            reason = "liquidation"
            raw_exit = pos["liq"]
            fill_exit = execution_price(
                raw_exit, side, "exit",
                slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec.price_unit,
            )
            room = pos["notional"] * max(0.0, (1.0 / cfg.leverage) - spec.maintenance_margin_rate)
            liq_fee = pos["notional"] * spec.liquidation_fee_rate
            gross = -room
            net = -room - liq_fee - pos["entry_fee"] + float(pos.get("funding") or 0.0)
            exit_fee = liq_fee
            drag = 0.0
            ambiguous = True
        else:
            fill_exit = execution_price(
                raw_exit, side, "exit",
                slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec.price_unit,
            )
            gross = (raw_exit - pos["raw_entry"]) * direction * qty * spec.contract_size
            price_pnl = (fill_exit - pos["fill"]) * direction * qty * spec.contract_size
            exit_fee = abs(qty * spec.contract_size * fill_exit) * spec.taker_fee_rate
            net = price_pnl - pos["entry_fee"] - exit_fee + float(pos.get("funding") or 0.0)
            drag = gross - price_pnl
        equity += net
        exit_day = ny_date_of_utc_ts(bar.ts)
        if exit_day == current_day:
            realized_today += net
        risk = pos["risk_usd"]
        order = pos["pending"]
        records.append(
            _blank(
                cfg, pos["day"], split_of(pos["day"]), pos["rng"],
                status="closed",
                direction=side,
                **_signal_fields(order),
                fill_ts=pos["fill_ts"],
                fill_price=pos["fill"],
                raw_entry=pos["raw_entry"],
                fill_outside_bar=pos["outside"],
                qty=qty,
                notional=pos["notional"],
                stop_price=pos["sl"],
                target_price=pos["tp"],
                exit_ts=bar.ts,
                exit_price=fill_exit,
                raw_exit=raw_exit,
                exit_reason=reason,
                path_ambiguous=bool(ambiguous or pos["path_ambiguous"]),
                fee_entry=pos["entry_fee"],
                fee_exit=exit_fee,
                fees=pos["entry_fee"] + exit_fee,
                funding=float(pos.get("funding") or 0.0),
                funding_events=int(pos.get("funding_events") or 0),
                gross_pnl=gross,
                net_pnl=net,
                execution_drag=drag,
                r_multiple=(net / risk) if risk else None,
                equity_after=equity,
                same_bar_exit=pos["same_bar_exit"],
            )
        )
        position = None

    def manage(bar: Bar, pos: dict[str, Any], *, same_bar: bool) -> bool:
        res = resolve_exit(
            pos["side"], pos["raw_entry"], pos["sl"], pos["tp"], bar,
            in_position_from_open=True,
        )
        if not res.closed or res.raw_price is None:
            return False
        pos["same_bar_exit"] = same_bar
        if res.ambiguous:
            pos["path_ambiguous"] = True
        close_position(pos, bar, res.raw_price, res.reason or "stop", res.ambiguous)
        return True

    def apply_funding(pos: dict[str, Any], bar: Bar) -> None:
        """Charge the settlement if this minute is one and the position was already open."""
        if cfg.funding_rate == 0.0 or not is_funding_ts(bar.ts, cfg.funding_interval_hours):
            return
        if pos["fill_ts"] >= bar.ts or bar.ts in pos["funding_ts"]:
            return
        cash = funding_cash(pos["side"], pos["qty"], bar.open, cfg.funding_rate, spec.contract_size)
        pos["funding"] = float(pos.get("funding") or 0.0) + cash
        pos["funding_events"] = int(pos.get("funding_events") or 0) + 1
        pos["funding_ts"].add(bar.ts)

    def qualify(bar: Bar, side: str, rng: OpeningRangeV3) -> bool:
        if rng.high is None or rng.low is None:
            return False
        if side == "LONG":
            return bar.close > bar.open and bar.close > rng.high
        return bar.close < bar.open and bar.close < rng.low

    for index, bar in enumerate(ordered):
        day = ny_date_of_utc_ts(bar.ts)
        if day != current_day:
            if pending is not None and pending["day"] != day:
                remember(
                    pending["day"], pending["rng"], status="unfilled",
                    unfilled_reason="entry_cutoff", direction=pending["side"],
                    **_signal_fields(pending),
                )
                pending = None
            flush_missed(current_day)
            current_day = day
            day_start_equity = equity
            realized_today = 0.0

        if position is not None and position["fill_ts"] <= bar.ts:
            if bar.ts > position.get("last_bar_ts", position["fill_ts"]) + ONE_MIN:
                position["pending"] = position["pending"]
                records.append(
                    _blank(
                        cfg, position["day"], split_of(position["day"]), position["rng"],
                        status="filled_open",
                        direction=position["side"],
                        **_signal_fields(position["pending"]),
                        fill_ts=position["fill_ts"],
                        fill_price=position["fill"],
                        raw_entry=position["raw_entry"],
                        fill_outside_bar=position["outside"],
                        qty=position["qty"],
                        notional=position["notional"],
                        stop_price=position["sl"],
                        target_price=position["tp"],
                        exit_reason="data_gap",
                        fee_entry=position["entry_fee"],
                        fees=position["entry_fee"],
                        funding=float(position.get("funding") or 0.0),
                        funding_events=int(position.get("funding_events") or 0),
                        mtm_pnl=(
                            (ordered[index - 1].close - position["fill"])
                            * (1.0 if position["side"] == "LONG" else -1.0)
                            * position["qty"] * spec.contract_size
                            - position["entry_fee"]
                        ) if index else None,
                        path_ambiguous=True,
                    )
                )
                position = None
            else:
                apply_funding(position, bar)
                position["last_bar_ts"] = bar.ts
                manage(bar, position, same_bar=False)

        if pending is not None and index >= pending["fill_index"]:
            immediate = bar.ts == pending["info_known_ts"]
            if not immediate or bar.ts >= pending["entry_end"] or bar.ts < pending["info_known_ts"]:
                reason = "entry_cutoff" if bar.ts >= pending["entry_end"] else "no_executable_bar"
                remember(
                    pending["day"], pending["rng"], status="unfilled",
                    unfilled_reason=reason, direction=pending["side"],
                    **_signal_fields(pending),
                )
            else:
                opened = try_open(pending["day"], pending["rng"], bar, pending)
                if opened is not None:
                    opened["last_bar_ts"] = bar.ts
                    position = opened
                    manage(bar, position, same_bar=True)
            pending = None

        if position is not None or pending is not None or not allowed(day) or day in signaled:
            continue
        if day in blocked:
            signaled.add(day)
            remember(day, ranges[day], status="unfilled", unfilled_reason=blocked[day])
            continue
        rng = ranges.get(day)
        brk = breakouts.get(day)
        if rng is None or not rng.usable or brk is None:
            continue
        close_ts = bar.close_ts(ONE_MIN)
        entry_end = entry_cutoff_ts(day, cfg.entry_cutoff)
        if cfg.continuation == "next_5m":
            # The next 5-minute candle opens when the breakout closes.
            # Its five 1-minute bars are the only continuation candidates.
            window_open = brk.close_ts
            window_limit = brk.close_ts + FIVE_MIN
            if bar.ts >= window_limit:
                signaled.add(day)
                remember(
                    day, rng, status="unfilled", unfilled_reason="no_1m_continuation",
                    **_breakout_fields(brk),
                )
                continue
            if bar.ts < window_open or close_ts <= window_open or close_ts >= entry_end:
                continue
            if not _continues_breakout_close(bar, brk.side, brk.close):
                continue
        else:
            # Opens at or after the 5-minute confirmation and closes before 11:00.
            # Minutes inside the breakout candle are already closed when the
            # breakout becomes known, so they are not a continuation.
            if bar.ts < brk.close_ts or close_ts <= brk.close_ts or close_ts >= entry_end:
                continue
            if not qualify(bar, brk.side, rng):
                continue
        signaled.add(day)
        pending = {
            "fill_index": index + 1,
            "day": day,
            "rng": rng,
            "side": brk.side,
            "breakout_candle_ts": brk.candle_ts,
            "breakout_close_ts": brk.close_ts,
            "breakout_open": brk.open,
            "breakout_high": brk.high,
            "breakout_low": brk.low,
            "breakout_close": brk.close,
            "entry_signal_ts": close_ts,
            "entry_signal_close": bar.close,
            "signal_price": bar.close,
            "order_price": bar.close,
            "order_type": "market",
            "info_known_ts": close_ts,
            "entry_end": entry_end,
        }

    if pending is not None:
        remember(
            pending["day"], pending["rng"], status="unfilled",
            unfilled_reason="end_of_data", direction=pending["side"],
            **_signal_fields(pending),
        )
        pending = None
    flush_missed(current_day)
    if position is not None and ordered:
        last = ordered[-1]
        side = position["side"]
        direction = 1.0 if side == "LONG" else -1.0
        mtm = (last.close - position["fill"]) * direction * position["qty"] * spec.contract_size
        mtm -= position["entry_fee"]
        records.append(
            _blank(
                cfg, position["day"], split_of(position["day"]), position["rng"],
                status="filled_open",
                direction=side,
                **_signal_fields(position["pending"]),
                fill_ts=position["fill_ts"],
                fill_price=position["fill"],
                raw_entry=position["raw_entry"],
                fill_outside_bar=position["outside"],
                qty=position["qty"],
                notional=position["notional"],
                stop_price=position["sl"],
                target_price=position["tp"],
                exit_reason="open_at_data_end",
                fee_entry=position["entry_fee"],
                fees=position["entry_fee"],
                funding=float(position.get("funding") or 0.0),
                funding_events=int(position.get("funding_events") or 0),
                mtm_pnl=mtm,
                path_ambiguous=position["path_ambiguous"],
            )
        )
    return records


def survey_sessions(
    bars_15m: list[Bar],
    bars_5m: list[Bar],
    bars_1m: list[Bar],
) -> list[dict[str, Any]]:
    """Classify every New York date present in the 15-minute, 5-minute, or 1-minute series."""
    by15 = _index(bars_15m, "15m")
    by5 = _index(bars_5m, "5m")
    by1 = _index(bars_1m, "1m")
    dates = sorted(
        {ny_date_of_utc_ts(bar.ts) for bar in list(bars_15m) + list(bars_5m) + list(bars_1m)}
    )
    rows = []
    for day in dates:
        rng = opening_range_v3(bars_15m, day, bars_5m=bars_5m, by15=by15, by5=by5)
        cutoff = entry_cutoff_ts(day)
        breakout: BreakoutV3 | None = None
        breakout_state = None
        path_5m = breakout_path_complete(bars_5m, day, by5=by5) if rng.usable else False
        path_1m = entry_window_complete(bars_1m, day, by1=by1) if rng.usable else False
        if rng.usable:
            found = find_breakout(bars_5m, rng, cutoff_ts=cutoff, by5=by5)
            if isinstance(found, BreakoutV3):
                breakout = found
                breakout_state = found.side
            elif found is None:
                breakout_state = "none"
            else:
                breakout_state = found
        tradable = bool(rng.usable and path_5m and path_1m and breakout_state not in ("incomplete", "ambiguous"))
        rows.append(
            {
                "ny_date": day.isoformat(),
                "nyse_session": is_nyse_session_day(day),
                "range_status": rng.status,
                "or_high": rng.high,
                "or_low": rng.low,
                "or_open": rng.candle_open,
                "or_close": rng.candle_close,
                "range_start_ts": rng.start_ts,
                "range_end_ts": rng.end_ts,
                "breakout_path_complete": path_5m,
                "entry_window_1m_complete": path_1m,
                "tradable": tradable,
                "breakout": breakout_state,
                "breakout_candle_ts": None if breakout is None else breakout.candle_ts,
                "breakout_close_ts": None if breakout is None else breakout.close_ts,
                "breakout_open": None if breakout is None else breakout.open,
                "breakout_high": None if breakout is None else breakout.high,
                "breakout_low": None if breakout is None else breakout.low,
                "breakout_close": None if breakout is None else breakout.close,
            }
        )
    return rows
