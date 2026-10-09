"""MIB ORB V2: 09:30 New York 5-minute range, 5-minute body breakout, 1-minute continuation.

This is not ORB-5 / ORB-15 / ORB-30. It does not import Hunt, S1, or S2.
It does not submit orders. Fills are an OHLC simulation: the database has no
prints inside a bar.

Deterministic same-candle rule
-------------------------------
A completed 5-minute candle has one close. LONG requires that close to be
strictly above the opening-range high. SHORT requires it to be strictly below
the opening-range low. Those two tests cannot both be true when the range
high is greater than or equal to the range low. A wick through either level
does not count. No intrabar path is consulted, so a later price is never used
to decide which side printed first. If the range itself is inverted, the
session is invalid and is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
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

FIVE_MIN = 300
ONE_MIN = 60


@dataclass(frozen=True)
class OrbV2Config:
    """Risk defaults are copied from the pre-declared ORB primary preset."""

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
    # None disables the filter. A number is a multiple of that day's range
    # width: a long whose next open is more than this far above OR_HIGH is
    # not filled. It is not a tuned parameter.
    max_entry_distance_range_multiple: float | None = None
    spec: ContractSpec = BTC_USDT

    def config_id(self) -> str:
        distance = (
            "off"
            if self.max_entry_distance_range_multiple is None
            else f"{self.max_entry_distance_range_multiple:g}x"
        )
        return "|".join(
            (
                self.symbol,
                "ORB-V2",
                "5m-body+1m-continuation",
                self.risk_profile,
                self.slippage_profile,
                f"distance={distance}",
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id(),
            "strategy": "ORB-V2",
            "definition": "09:30-09:35 NY range, first 5m body breakout, next 1m continuation",
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
            "max_entry_distance_range_multiple": self.max_entry_distance_range_multiple,
            "one_entry_per_session": True,
            "live_submit": False,
            "fills": "ohlc_simulation",
            "contract": self.spec.to_dict(),
        }


def v2_primary_config(**overrides: Any) -> OrbV2Config:
    """Same account, risk, fee and cost assumptions as the original primary preset.

    The distance filter stays off. Pass max_entry_distance_range_multiple to
    test that filter as a separate run.
    """
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
        max_entry_distance_range_multiple=None,
    )
    fields.update(overrides)
    return OrbV2Config(**fields)


@dataclass(frozen=True)
class OpeningRangeV2:
    ny_date: date
    status: str
    high: float | None
    low: float | None
    start_ts: int
    end_ts: int
    candle_open: float | None = None
    candle_close: float | None = None

    @property
    def usable(self) -> bool:
        return self.status == "ok" and self.high is not None and self.low is not None and self.high >= self.low


@dataclass(frozen=True)
class BreakoutV2:
    side: str
    candle_ts: int
    close_ts: int
    close: float
    open: float


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


def opening_range_v2(
    bars_5m: list[Bar],
    bars_1m: list[Bar],
    day: date,
    *,
    by5: dict[int, Bar] | None = None,
    by1: dict[int, Bar] | None = None,
) -> OpeningRangeV2:
    """The 09:30-09:35 New York candle only. Later candles cannot move it.

    The native 5-minute candle is the range. When 1-minute candles exist they
    must cover all five minutes and rebuild that same OHLC. A mismatch or a
    hole skips the session. Partial highs are not used.
    """
    start_ts, end_ts = range_bounds_ts(day, 5)
    blank = dict(ny_date=day, high=None, low=None, start_ts=start_ts, end_ts=end_ts)
    if not is_nyse_session_day(day):
        return OpeningRangeV2(status="not_nyse_session_day", **blank)
    by5 = _index(bars_5m, "5m") if by5 is None else by5
    by1 = _index(bars_1m, "1m") if by1 is None else by1
    candle = by5.get(start_ts)
    if candle is None or candle.close_ts(FIVE_MIN) != end_ts:
        return OpeningRangeV2(status="missing_opening_range", **blank)
    if not _valid_ohlc(candle):
        return OpeningRangeV2(status="invalid_opening_range", **blank)
    minutes = [by1.get(start_ts + i * ONE_MIN) for i in range(5)]
    if any(minute is None for minute in minutes):
        return OpeningRangeV2(status="missing_1m_opening_range", **blank)
    assert all(minute is not None for minute in minutes)
    packed = [minute for minute in minutes if minute is not None]
    if any(not _valid_ohlc(minute) for minute in packed):
        return OpeningRangeV2(status="invalid_opening_range", **blank)
    agg_open = packed[0].open
    agg_close = packed[-1].close
    agg_high = max(minute.high for minute in packed)
    agg_low = min(minute.low for minute in packed)
    if (
        abs(agg_open - candle.open) > 1e-6
        or abs(agg_close - candle.close) > 1e-6
        or abs(agg_high - candle.high) > 1e-6
        or abs(agg_low - candle.low) > 1e-6
    ):
        return OpeningRangeV2(status="opening_range_mismatch", **blank)
    return OpeningRangeV2(
        status="ok",
        high=candle.high,
        low=candle.low,
        candle_open=candle.open,
        candle_close=candle.close,
        **{k: v for k, v in blank.items() if k not in ("high", "low")},
    )


def _body_breakout(bar: Bar, or_high: float, or_low: float) -> str | None:
    """Close must finish strictly beyond the level. A wick is not enough.

    The close is the end of the body, so this is the body break. A candle
    that opens already beyond the level and closes there is still a body
    break: the body itself finished beyond the level, rather than only a wick.
    """
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
    rng: OpeningRangeV2,
    *,
    cutoff_ts: int,
    by5: dict[int, Bar] | None = None,
) -> BreakoutV2 | None | str:
    """First completed 5-minute body breakout after 09:35 and before 11:00.

    Returns None when no candle breaks, or the string ``incomplete`` when a
    5-minute slot before the cutoff is missing. Later candles are not used
    to fill that hole.
    """
    if not rng.usable or rng.high is None or rng.low is None:
        return None
    by5 = _index(bars_5m, "5m") if by5 is None else by5
    ts = rng.end_ts
    while ts + FIVE_MIN < cutoff_ts:
        bar = by5.get(ts)
        if bar is None or not _valid_ohlc(bar):
            return "incomplete"
        side = _body_breakout(bar, rng.high, rng.low)
        if side == "ambiguous":
            return "ambiguous"
        if side is not None:
            return BreakoutV2(
                side=side,
                candle_ts=bar.ts,
                close_ts=bar.close_ts(FIVE_MIN),
                close=bar.close,
                open=bar.open,
            )
        ts += FIVE_MIN
    return None


def _distance_blocks(cfg: OrbV2Config, side: str, raw_entry: float, rng: OpeningRangeV2) -> bool:
    multiple = cfg.max_entry_distance_range_multiple
    if multiple is None or rng.high is None or rng.low is None:
        return False
    width = rng.high - rng.low
    limit = multiple * width
    if side == "LONG":
        return (raw_entry - rng.high) > limit + 1e-9
    return (rng.low - raw_entry) > limit + 1e-9


def _blank(cfg: OrbV2Config, day: date, split: str, rng: OpeningRangeV2 | None, **fields: Any) -> dict[str, Any]:
    row = {
        "config_id": cfg.config_id(),
        "strategy": "ORB-V2",
        "split": split,
        "ny_date": day.isoformat(),
        "symbol": cfg.symbol,
        "direction": None,
        "status": "unfilled",
        "unfilled_reason": None,
        "or_high": None if rng is None else rng.high,
        "or_low": None if rng is None else rng.low,
        "breakout_candle_ts": None,
        "breakout_close_ts": None,
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
        "gross_pnl": None,
        "net_pnl": None,
        "execution_drag": None,
        "r_multiple": None,
        "equity_after": None,
        "mtm_pnl": None,
        "same_bar_exit": None,
        "distance_filter": cfg.max_entry_distance_range_multiple,
        "risk_profile": cfg.risk_profile,
        "slippage_profile": cfg.slippage_profile,
    }
    row.update(fields)
    return row


def run_v2_backtest(
    bars_5m: list[Bar],
    bars_1m: list[Bar],
    config: OrbV2Config | None = None,
    *,
    entry_dates: set[date] | None = None,
    split_name: str | None = None,
) -> list[dict[str, Any]]:
    """Chronological V2 backtest.

    The 5-minute breakout is known only at that candle's close. The 1-minute
    continuation must close strictly after that, and the fill is the next
    1-minute bar's open. Nothing on a bar reads a later bar.
    """
    cfg = config or v2_primary_config()
    spec = cfg.spec
    five = list(bars_5m)
    ordered = sorted(bars_1m, key=lambda bar: bar.ts)
    by5 = _index(five, "5m")
    by1 = _index(ordered, "1m")
    dates = sorted({ny_date_of_utc_ts(bar.ts) for bar in list(five) + ordered})
    ranges = {
        day: opening_range_v2(five, ordered, day, by5=by5, by1=by1) for day in dates
    }
    breakouts: dict[date, BreakoutV2 | None] = {}
    blocked: dict[date, str] = {}
    for day, rng in ranges.items():
        if not rng.usable:
            continue
        found = find_breakout(
            five, rng, cutoff_ts=entry_cutoff_ts(day, cfg.entry_cutoff), by5=by5,
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

    def remember(day: date, rng: OpeningRangeV2 | None, **fields: Any) -> None:
        records.append(_blank(cfg, day, split_of(day), rng, **fields))

    def try_open(day: date, rng: OpeningRangeV2, bar: Bar, pending_order: dict[str, Any]) -> dict[str, Any] | None:
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
        if _distance_blocks(cfg, side, raw_entry, rng):
            remember(
                day, rng, status="unfilled", unfilled_reason="entry_distance",
                direction=side, raw_entry=raw_entry, **_signal_fields(pending_order),
            )
            return None
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
            net = -room - liq_fee - pos["entry_fee"]
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
            net = price_pnl - pos["entry_fee"] - exit_fee
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

    def qualify(bar: Bar, side: str, rng: OpeningRangeV2) -> bool:
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
            current_day = day
            day_start_equity = equity
            realized_today = 0.0

        if position is not None and position["fill_ts"] <= bar.ts:
            manage(bar, position, same_bar=False)

        if pending is not None and index >= pending["fill_index"]:
            if bar.ts >= pending["entry_end"] or bar.ts < pending["info_known_ts"]:
                remember(
                    pending["day"], pending["rng"], status="unfilled",
                    unfilled_reason="entry_cutoff" if bar.ts >= pending["entry_end"] else "no_executable_bar",
                    direction=pending["side"], **_signal_fields(pending),
                )
            else:
                opened = try_open(pending["day"], pending["rng"], bar, pending)
                if opened is not None:
                    position = opened
                    manage(bar, position, same_bar=True)
            pending = None

        if position is not None or pending is not None or not allowed(day) or day in signaled:
            continue
        if day in blocked:
            signaled.add(day)
            remember(
                day, ranges[day], status="unfilled", unfilled_reason=blocked[day],
            )
            continue
        rng = ranges.get(day)
        brk = breakouts.get(day)
        if rng is None or not rng.usable or brk is None:
            continue
        close_ts = bar.close_ts(ONE_MIN)
        entry_end = entry_cutoff_ts(day, cfg.entry_cutoff)
        # The 1-minute candle must open at or after the 5-minute confirmation
        # and close before the entry cutoff. Candles inside the breakout bar
        # are already closed by the time the breakout is known.
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
                mtm_pnl=mtm,
                path_ambiguous=position["path_ambiguous"],
            )
        )
    return records


def _signal_fields(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "breakout_candle_ts": order.get("breakout_candle_ts"),
        "breakout_close_ts": order.get("breakout_close_ts"),
        "breakout_close": order.get("breakout_close"),
        "entry_signal_ts": order.get("entry_signal_ts"),
        "entry_signal_close": order.get("entry_signal_close"),
        "signal_price": order.get("signal_price"),
        "order_price": order.get("order_price"),
        "order_type": order.get("order_type"),
        "info_known_ts": order.get("info_known_ts"),
    }


def survey_sessions(bars_5m: list[Bar], bars_1m: list[Bar]) -> list[dict[str, Any]]:
    """Classify every New York date that appears in either series."""
    by5 = _index(bars_5m, "5m")
    by1 = _index(bars_1m, "1m")
    dates = sorted({ny_date_of_utc_ts(bar.ts) for bar in list(bars_5m) + list(bars_1m)})
    rows = []
    for day in dates:
        rng = opening_range_v2(bars_5m, bars_1m, day, by5=by5, by1=by1)
        cutoff = entry_cutoff_ts(day)
        breakout: BreakoutV2 | None = None
        breakout_state = None
        if rng.usable:
            found = find_breakout(bars_5m, rng, cutoff_ts=cutoff, by5=by5)
            if isinstance(found, BreakoutV2):
                breakout = found
                breakout_state = found.side
            elif found is None:
                breakout_state = "none"
            else:
                breakout_state = found
        rows.append(
            {
                "ny_date": day.isoformat(),
                "nyse_session": is_nyse_session_day(day),
                "range_status": rng.status,
                "or_high": rng.high,
                "or_low": rng.low,
                "range_start_ts": rng.start_ts,
                "range_end_ts": rng.end_ts,
                "breakout": breakout_state,
                "breakout_candle_ts": None if breakout is None else breakout.candle_ts,
                "breakout_close_ts": None if breakout is None else breakout.close_ts,
                "breakout_close": None if breakout is None else breakout.close,
            }
        )
    return rows
