"""Chronological ORB backtest.

Signal generation on bar N never reads bar N+1. Close and retest orders fill
on the next bar's open, and only if that open is still inside the entry
window. Intrabar orders are an OHLC proxy: the database does not contain the
print time inside the bar. Those rows are marked ohlc_proxy=True.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from .contract_spec import BTC_USDT, ContractSpec
from .market import (
    Bar,
    OpeningRange,
    build_opening_ranges,
    execution_price,
    isolated_liquidation_price,
    resolve_exit,
    size_contracts,
    stop_and_target,
)
from .session import DEFAULT_ENTRY_CUTOFF, entry_cutoff_ts, ny_date_of_utc_ts


@dataclass(frozen=True)
class OrbConfig:
    orb: str = "ORB-15"
    entry_model: str = "close"  # close | intrabar | retest
    sides: str = "both"  # long | short | both
    stop_loss_fraction: float = 0.0025
    take_profit_fraction: float = 0.005
    risk_fraction: float = 0.01
    leverage: float = 5.0
    max_daily_loss_fraction: float = 0.03
    max_trades_per_session: int = 2
    max_open_positions: int = 1
    starting_equity: float = 1000.0
    slippage_bps: float = 1.0
    spread_usd: float = 0.10
    slippage_profile: str = "normal"
    risk_profile: str = "baseline_25bps_2R"
    bar_seconds: int = 300
    timeframe: str = "5m"
    symbol: str = "BTC_USDT"
    require_nyse_day: bool = True
    entry_cutoff: time = DEFAULT_ENTRY_CUTOFF
    compound: bool = True
    spec: ContractSpec = BTC_USDT

    def config_id(self) -> str:
        return "|".join(
            (
                self.symbol,
                self.timeframe,
                self.orb,
                self.entry_model,
                self.sides,
                self.risk_profile,
                self.slippage_profile,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id(),
            "orb": self.orb,
            "entry_model": self.entry_model,
            "sides": self.sides,
            "stop_loss_fraction": self.stop_loss_fraction,
            "take_profit_fraction": self.take_profit_fraction,
            "risk_fraction": self.risk_fraction,
            "leverage": self.leverage,
            "max_daily_loss_fraction": self.max_daily_loss_fraction,
            "max_trades_per_session": self.max_trades_per_session,
            "max_open_positions": self.max_open_positions,
            "starting_equity": self.starting_equity,
            "slippage_bps": self.slippage_bps,
            "spread_usd": self.spread_usd,
            "slippage_profile": self.slippage_profile,
            "risk_profile": self.risk_profile,
            "bar_seconds": self.bar_seconds,
            "timeframe": self.timeframe,
            "symbol": self.symbol,
            "require_nyse_day": self.require_nyse_day,
            "entry_cutoff_local": self.entry_cutoff.strftime("%H:%M:%S"),
            "compound": self.compound,
            "time_exit": None,
            "session": "America/New_York 09:30 cash open only",
            "contract": self.spec.to_dict(),
        }


def comparison_grid(timeframe: str = "5m", bar_seconds: int = 300) -> list[OrbConfig]:
    """Pre-declared comparison set. Not a search, and not fit on any split."""
    risks = (
        dict(
            stop_loss_fraction=0.0025,
            take_profit_fraction=0.005,
            risk_fraction=0.01,
            leverage=5.0,
            max_daily_loss_fraction=0.03,
            risk_profile="baseline_25bps_2R",
        ),
        dict(
            stop_loss_fraction=0.005,
            take_profit_fraction=0.005,
            risk_fraction=0.005,
            leverage=3.0,
            max_daily_loss_fraction=0.02,
            risk_profile="wider_50bps_1R",
        ),
    )
    slips = (
        dict(slippage_bps=1.0, spread_usd=0.10, slippage_profile="normal"),
        dict(slippage_bps=4.0, spread_usd=1.40, slippage_profile="conservative"),
    )
    out: list[OrbConfig] = []
    for orb in ("ORB-5", "ORB-15", "ORB-30"):
        for model in ("close", "intrabar", "retest"):
            for sides in ("long", "short", "both"):
                for risk in risks:
                    for slip in slips:
                        out.append(
                            OrbConfig(
                                orb=orb,
                                entry_model=model,
                                sides=sides,
                                timeframe=timeframe,
                                bar_seconds=bar_seconds,
                                **risk,
                                **slip,
                            )
                        )
    return out


def primary_config(**overrides: Any) -> OrbConfig:
    """Default preset, chosen before any backtest result was looked at.

    ORB-15, close confirmation, both directions, 0.25% stop, 0.50% target,
    1% equity risk, 5x isolated, normal slippage. This is not a claim that
    the preset is profitable.
    """
    return OrbConfig(**overrides)


def chronological_splits(days: list[date]) -> dict[date, str]:
    ordered = sorted(set(days))
    n = len(ordered)
    n_train = int(n * 0.60)
    n_val = int(n * 0.20)
    out: dict[date, str] = {}
    for i, day in enumerate(ordered):
        if i < n_train:
            out[day] = "train"
        elif i < n_train + n_val:
            out[day] = "validation"
        else:
            out[day] = "out_of_sample"
    return out


def _allows(cfg: OrbConfig, side: str) -> bool:
    if cfg.sides == "both":
        return True
    if cfg.sides == "long":
        return side == "LONG"
    if cfg.sides == "short":
        return side == "SHORT"
    raise ValueError(cfg.sides)


def _blank(
    cfg: OrbConfig,
    day: date,
    split: str,
    rng: OpeningRange | None,
    **fields: Any,
) -> dict[str, Any]:
    row = {
        "config_id": cfg.config_id(),
        "split": split,
        "ny_date": day.isoformat(),
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe,
        "orb": cfg.orb,
        "entry_model": cfg.entry_model,
        "sides": cfg.sides,
        "risk_profile": cfg.risk_profile,
        "slippage_profile": cfg.slippage_profile,
        "direction": None,
        "status": "unfilled",
        "unfilled_reason": None,
        "signal_ts": None,
        "signal_price": None,
        "order_price": None,
        "order_type": None,
        "info_known_ts": None,
        "fill_ts": None,
        "fill_price": None,
        "raw_entry": None,
        "fill_outside_bar": None,
        "ohlc_proxy": cfg.entry_model == "intrabar",
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
        "gross_pnl": None,
        "net_pnl": None,
        "execution_drag": None,
        "r_multiple": None,
        "or_high": None if rng is None else rng.high,
        "or_low": None if rng is None else rng.low,
        "equity_after": None,
        "mtm_pnl": None,
        "same_bar_exit": None,
    }
    row.update(fields)
    return row


def _step_retest(phase: str, side: str, level: float, bar: Bar) -> tuple[str, bool]:
    broke = bar.close > level if side == "LONG" else bar.close < level
    touched = bar.low <= level if side == "LONG" else bar.high >= level
    if phase == "wait_break":
        return ("wait_retest", False) if broke else (phase, False)
    if phase == "wait_retest":
        if touched and broke:
            return "done", True
        if touched:
            return "wait_continuation", False
        return phase, False
    if phase == "wait_continuation":
        if broke:
            return "done", True
        return phase, False
    return phase, False


def run_backtest(
    bars: list[Bar],
    config: OrbConfig,
    *,
    ranges: dict[date, OpeningRange] | None = None,
    splits: dict[date, str] | None = None,
    entry_dates: set[date] | None = None,
    split_name: str | None = None,
) -> list[dict[str, Any]]:
    cfg = config
    spec = cfg.spec
    ordered = sorted(bars, key=lambda b: b.ts)
    if ranges is None:
        ranges = build_opening_ranges(
            ordered,
            cfg.orb,
            cfg.bar_seconds,
            require_nyse_day=cfg.require_nyse_day,
            cutoff=cfg.entry_cutoff,
        )
    splits = splits or {}
    equity = float(cfg.starting_equity)
    position: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    current_day: date | None = None
    day_start_equity = equity
    realized_today = 0.0
    fills_today = 0
    signaled: set[str] = set()
    phase = {"LONG": "wait_break", "SHORT": "wait_break"}
    records: list[dict[str, Any]] = []

    def split_of(day: date) -> str:
        if split_name is not None:
            return split_name
        return splits.get(day, "unspecified")

    def remember_unfilled(day: date, rng: OpeningRange | None, reason: str, **fields: Any) -> None:
        records.append(
            _blank(cfg, day, split_of(day), rng, status="unfilled", unfilled_reason=reason, **fields)
        )

    def try_open(
        day: date,
        rng: OpeningRange,
        bar: Bar,
        side: str,
        raw_entry: float,
        *,
        signal_ts: int,
        signal_price: float,
        order_price: float,
        order_type: str,
        info_known_ts: int,
        ohlc_proxy: bool,
    ) -> dict[str, Any] | None:
        nonlocal equity
        if fills_today >= cfg.max_trades_per_session:
            remember_unfilled(
                day, rng, "max_trades_per_session", direction=side,
                signal_ts=signal_ts, signal_price=signal_price, order_price=order_price,
                order_type=order_type, info_known_ts=info_known_ts,
            )
            return None
        loss_limit = -cfg.max_daily_loss_fraction * day_start_equity
        if realized_today <= loss_limit:
            remember_unfilled(
                day, rng, "max_daily_loss", direction=side,
                signal_ts=signal_ts, signal_price=signal_price, order_price=order_price,
                order_type=order_type, info_known_ts=info_known_ts,
            )
            return None
        if cfg.max_open_positions < 1 or position is not None:
            remember_unfilled(
                day, rng, "max_open_positions", direction=side,
                signal_ts=signal_ts, signal_price=signal_price, order_price=order_price,
                order_type=order_type, info_known_ts=info_known_ts,
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
            remember_unfilled(
                day, rng, "stop_target_invalid", direction=side,
                signal_ts=signal_ts, signal_price=signal_price, order_price=order_price,
                order_type=order_type, info_known_ts=info_known_ts, fill_price=fill,
            )
            return None
        sizing_equity = equity if cfg.compound else cfg.starting_equity
        qty, err = size_contracts(
            sizing_equity, cfg.risk_fraction, fill, sl, cfg.leverage, spec,
        )
        if err:
            remember_unfilled(
                day, rng, err, direction=side,
                signal_ts=signal_ts, signal_price=signal_price, order_price=order_price,
                order_type=order_type, info_known_ts=info_known_ts, fill_price=fill,
                stop_price=sl, target_price=tp,
            )
            return None
        notional = qty * spec.contract_size * fill
        entry_fee = notional * spec.taker_fee_rate
        margin = notional / cfg.leverage
        if margin + entry_fee > equity + 1e-9:
            remember_unfilled(
                day, rng, "insufficient_margin", direction=side,
                signal_ts=signal_ts, signal_price=signal_price, order_price=order_price,
                order_type=order_type, info_known_ts=info_known_ts, fill_price=fill,
                qty=qty, notional=notional, stop_price=sl, target_price=tp,
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
            "signal_ts": signal_ts,
            "signal_price": signal_price,
            "order_price": order_price,
            "order_type": order_type,
            "info_known_ts": info_known_ts,
            "fill_ts": bar.ts,
            "entry_fee": entry_fee,
            "notional": notional,
            "outside": outside,
            "ohlc_proxy": ohlc_proxy,
            "liq": isolated_liquidation_price(
                side, fill, cfg.leverage, spec.maintenance_margin_rate,
            ),
            "risk_usd": abs(fill - sl) * qty * spec.contract_size,
            "day": day,
            "rng": rng,
            "same_bar_exit": False,
            "path_ambiguous": False,
        }

    def close_position(pos: dict[str, Any], bar: Bar, raw_exit: float, reason: str, ambiguous: bool) -> None:
        nonlocal equity, position, realized_today
        side = pos["side"]
        spec_ = spec
        liq_worse = raw_exit <= pos["liq"] if side == "LONG" else raw_exit >= pos["liq"]
        qty = pos["qty"]
        direction = 1.0 if side == "LONG" else -1.0
        if liq_worse:
            reason = "liquidation"
            ambiguous = True if ambiguous else ambiguous
            raw_exit = pos["liq"]
            fill_exit = execution_price(
                raw_exit, side, "exit",
                slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec_.price_unit,
            )
            # Isolated loss is capped by initial margin minus maintenance.
            room = pos["notional"] * max(0.0, (1.0 / cfg.leverage) - spec_.maintenance_margin_rate)
            liq_fee = pos["notional"] * spec_.liquidation_fee_rate
            gross = -room
            net = -room - liq_fee - pos["entry_fee"]
            exit_fee = liq_fee
            drag = 0.0
        else:
            fill_exit = execution_price(
                raw_exit, side, "exit",
                slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=spec_.price_unit,
            )
            gross = (raw_exit - pos["raw_entry"]) * direction * qty * spec_.contract_size
            price_pnl = (fill_exit - pos["fill"]) * direction * qty * spec_.contract_size
            exit_fee = abs(qty * spec_.contract_size * fill_exit) * spec_.taker_fee_rate
            net = price_pnl - pos["entry_fee"] - exit_fee
            drag = gross - price_pnl
        equity += net
        exit_day = ny_date_of_utc_ts(bar.ts)
        if exit_day == current_day:
            realized_today += net
        risk = pos["risk_usd"]
        r_mult = (net / risk) if risk else None
        records.append(
            _blank(
                cfg, pos["day"], split_of(pos["day"]), pos["rng"],
                status="closed",
                direction=side,
                signal_ts=pos["signal_ts"],
                signal_price=pos["signal_price"],
                order_price=pos["order_price"],
                order_type=pos["order_type"],
                info_known_ts=pos["info_known_ts"],
                fill_ts=pos["fill_ts"],
                fill_price=pos["fill"],
                raw_entry=pos["raw_entry"],
                fill_outside_bar=pos["outside"],
                ohlc_proxy=pos["ohlc_proxy"],
                qty=qty,
                notional=pos["notional"],
                stop_price=pos["sl"],
                target_price=pos["tp"],
                exit_ts=bar.ts if reason != "open_at_data_end" else None,
                exit_price=fill_exit,
                raw_exit=raw_exit,
                exit_reason=reason,
                path_ambiguous=bool(ambiguous or pos["path_ambiguous"]),
                fee_entry=pos["entry_fee"],
                fee_exit=exit_fee,
                gross_pnl=gross,
                net_pnl=net,
                execution_drag=drag,
                r_multiple=r_mult,
                equity_after=equity,
                same_bar_exit=pos["same_bar_exit"],
            )
        )
        position = None

    def manage(bar: Bar, pos: dict[str, Any], *, from_open: bool, same_bar: bool) -> bool:
        """Return True if the position closed."""
        res = resolve_exit(
            pos["side"], pos["raw_entry"], pos["sl"], pos["tp"], bar,
            in_position_from_open=from_open,
        )
        if not res.closed or res.raw_price is None:
            return False
        pos["same_bar_exit"] = same_bar
        if res.ambiguous:
            pos["path_ambiguous"] = True
        close_position(pos, bar, res.raw_price, res.reason or "stop", res.ambiguous)
        return True

    for i, bar in enumerate(ordered):
        day = ny_date_of_utc_ts(bar.ts)
        if day != current_day:
            current_day = day
            day_start_equity = equity
            realized_today = 0.0
            fills_today = 0
            signaled = set()
            phase = {"LONG": "wait_break", "SHORT": "wait_break"}
            if pending is not None and pending["day"] != day:
                remember_unfilled(
                    pending["day"], pending["rng"], "entry_cutoff",
                    direction=pending["side"], signal_ts=pending["signal_ts"],
                    signal_price=pending["signal_price"], order_price=pending["order_price"],
                    order_type=pending["order_type"], info_known_ts=pending["info_known_ts"],
                )
                pending = None

        rng = ranges.get(day)
        entry_end = entry_cutoff_ts(day, cfg.entry_cutoff)
        acted = False

        if position is not None:
            acted = True
            manage(bar, position, from_open=True, same_bar=False)
        elif pending is not None and i >= pending["fill_index"]:
            acted = True
            if bar.ts >= pending["entry_end"]:
                remember_unfilled(
                    pending["day"], pending["rng"], "entry_cutoff",
                    direction=pending["side"], signal_ts=pending["signal_ts"],
                    signal_price=pending["signal_price"], order_price=pending["order_price"],
                    order_type=pending["order_type"], info_known_ts=pending["info_known_ts"],
                )
            else:
                opened = try_open(
                    pending["day"], pending["rng"], bar, pending["side"], bar.open,
                    signal_ts=pending["signal_ts"], signal_price=pending["signal_price"],
                    order_price=pending["order_price"], order_type=pending["order_type"],
                    info_known_ts=pending["info_known_ts"], ohlc_proxy=False,
                )
                if opened is not None:
                    opened["fill_ts"] = bar.ts
                    position = opened
                    fills_today += 1
                    manage(bar, position, from_open=True, same_bar=True)
            pending = None
        elif (
            position is None
            and pending is None
            and cfg.entry_model == "intrabar"
            and rng is not None
            and rng.complete
            and rng.high is not None
            and rng.low is not None
            and bar.ts >= rng.end_ts
            and bar.ts < entry_end
            and bar.close_ts(cfg.bar_seconds) <= entry_end
            and (entry_dates is None or day in entry_dates)
        ):
            long_hit = bar.high > rng.high
            short_hit = bar.low < rng.low
            want_long = long_hit and _allows(cfg, "LONG") and "LONG" not in signaled
            want_short = short_hit and _allows(cfg, "SHORT") and "SHORT" not in signaled
            if long_hit and short_hit and (_allows(cfg, "LONG") or _allows(cfg, "SHORT")):
                if "LONG" not in signaled or "SHORT" not in signaled:
                    signaled.update(("LONG", "SHORT"))
                    remember_unfilled(
                        day, rng, "ambiguous_both_sides",
                        signal_ts=bar.ts, info_known_ts=bar.close_ts(cfg.bar_seconds),
                        order_type="stop_market_proxy", ohlc_proxy=True,
                    )
                    acted = True
            elif want_long or want_short:
                side = "LONG" if want_long else "SHORT"
                level = rng.high if side == "LONG" else rng.low
                gapped = bar.open > level if side == "LONG" else bar.open < level
                raw = bar.open if gapped else level
                signaled.add(side)
                acted = True
                opened = try_open(
                    day, rng, bar, side, raw,
                    signal_ts=bar.ts,
                    signal_price=level,
                    order_price=level,
                    order_type="market" if gapped else "stop_market_proxy",
                    info_known_ts=bar.close_ts(cfg.bar_seconds),
                    ohlc_proxy=True,
                )
                if opened is not None:
                    position = opened
                    fills_today += 1
                    manage(
                        bar, position,
                        from_open=gapped,
                        same_bar=True,
                    )

        if (
            position is None
            and pending is None
            and not acted
            and rng is not None
            and rng.complete
            and (entry_dates is None or day in entry_dates)
        ):
            if rng.high is None or rng.low is None:
                continue
            close_ts = bar.close_ts(cfg.bar_seconds)
            in_window = bar.ts >= rng.end_ts and close_ts < entry_end
            if not in_window:
                continue
            if cfg.entry_model == "close":
                side = None
                if bar.close > rng.high and _allows(cfg, "LONG") and "LONG" not in signaled:
                    side = "LONG"
                elif bar.close < rng.low and _allows(cfg, "SHORT") and "SHORT" not in signaled:
                    side = "SHORT"
                if side is not None:
                    signaled.add(side)
                    pending = {
                        "fill_index": i + 1,
                        "day": day,
                        "rng": rng,
                        "side": side,
                        "signal_ts": close_ts,
                        "signal_price": bar.close,
                        "order_price": bar.close,
                        "order_type": "market",
                        "info_known_ts": close_ts,
                        "entry_end": entry_end,
                    }
            elif cfg.entry_model == "retest":
                signaled_now = None
                for side, level in (("LONG", rng.high), ("SHORT", rng.low)):
                    if not _allows(cfg, side) or side in signaled or phase[side] == "done":
                        continue
                    phase[side], fire = _step_retest(phase[side], side, level, bar)
                    if fire:
                        signaled_now = side
                        break
                if signaled_now is not None:
                    signaled.add(signaled_now)
                    pending = {
                        "fill_index": i + 1,
                        "day": day,
                        "rng": rng,
                        "side": signaled_now,
                        "signal_ts": close_ts,
                        "signal_price": bar.close,
                        "order_price": bar.close,
                        "order_type": "market",
                        "info_known_ts": close_ts,
                        "entry_end": entry_end,
                    }

    if pending is not None:
        remember_unfilled(
            pending["day"], pending["rng"], "end_of_data",
            direction=pending["side"], signal_ts=pending["signal_ts"],
            signal_price=pending["signal_price"], order_price=pending["order_price"],
            order_type=pending["order_type"], info_known_ts=pending["info_known_ts"],
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
                signal_ts=position["signal_ts"],
                signal_price=position["signal_price"],
                order_price=position["order_price"],
                order_type=position["order_type"],
                info_known_ts=position["info_known_ts"],
                fill_ts=position["fill_ts"],
                fill_price=position["fill"],
                raw_entry=position["raw_entry"],
                fill_outside_bar=position["outside"],
                ohlc_proxy=position["ohlc_proxy"],
                qty=position["qty"],
                notional=position["notional"],
                stop_price=position["sl"],
                target_price=position["tp"],
                exit_reason="open_at_data_end",
                fee_entry=position["entry_fee"],
                mtm_pnl=mtm,
                path_ambiguous=position["path_ambiguous"],
            )
        )
    return records
