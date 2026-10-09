"""Live-order preparation. This module does not submit orders.

The existing private adapter (`mexc_private.submit_order`) is not imported
here on purpose. A caller must pass a sender, and `allow_live_submit` must
be true. The backtest never does either. Intrabar stop-entry is refused
because that adapter has market and limit only — no plan-order endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .contract_spec import BTC_USDT, ContractSpec
from .market import Bar, OpeningRange, build_opening_ranges
from .session import entry_cutoff_ts, ny_date_of_utc_ts


class LiveSubmitDisabled(RuntimeError):
    pass


@dataclass
class PreparedOrder:
    payload: dict
    reason: str
    executable: bool

    def submit(self, sender=None, allow_live_submit: bool = False):
        if not allow_live_submit:
            raise LiveSubmitDisabled(
                "ORB live submit is disabled. Pass allow_live_submit=True only "
                "with separate authorization. No order was sent."
            )
        if not self.executable:
            raise LiveSubmitDisabled(self.reason)
        if sender is None:
            raise LiveSubmitDisabled("no order sender was provided; nothing was sent")
        return sender(self.payload)


class OrbLiveSession:
    """Feed closed candles only. Forming candles are rejected."""

    def __init__(
        self,
        day: date,
        orb: str = "ORB-15",
        bar_seconds: int = 60,
        spec: ContractSpec = BTC_USDT,
    ):
        self.day = day
        self.orb = orb
        self.bar_seconds = bar_seconds
        self.spec = spec
        self.bars: list[Bar] = []
        self._seen: set[int] = set()

    def on_closed_candle(self, bar: Bar, as_of_ts: int) -> OpeningRange | None:
        if bar.close_ts(self.bar_seconds) > int(as_of_ts):
            raise ValueError("candle has not closed; it cannot update the opening range or a signal")
        if ny_date_of_utc_ts(bar.ts) != self.day:
            raise ValueError("candle does not belong to this New York session date")
        if bar.ts in self._seen:
            return self.range()
        self._seen.add(bar.ts)
        self.bars.append(bar)
        self.bars.sort(key=lambda b: b.ts)
        return self.range()

    def range(self) -> OpeningRange | None:
        found = build_opening_ranges(
            self.bars, self.orb, self.bar_seconds, require_nyse_day=True,
        )
        return found.get(self.day)

    def close_signal(self, side_mode: str = "both") -> dict | None:
        """Model A only: a closed candle beyond the finished range, before 11:00."""
        rng = self.range()
        if rng is None or not rng.complete or rng.high is None or rng.low is None:
            return None
        cutoff = entry_cutoff_ts(self.day)
        last = None
        for bar in self.bars:
            if bar.ts < rng.end_ts:
                continue
            known = bar.close_ts(self.bar_seconds)
            if known >= cutoff:
                break
            if bar.close > rng.high and side_mode in ("both", "long"):
                last = ("LONG", bar, rng.high)
            elif bar.close < rng.low and side_mode in ("both", "short"):
                last = ("SHORT", bar, rng.low)
        if last is None:
            return None
        side, bar, level = last
        return {
            "side": side,
            "signal_price": bar.close,
            "level": level,
            "info_known_ts": bar.close_ts(self.bar_seconds),
            "or_high": rng.high,
            "or_low": rng.low,
            "entry_model": "close",
        }


def prepare_market_order(
    signal: dict,
    qty: int,
    stop_price: float,
    target_price: float,
    leverage: int,
    spec: ContractSpec = BTC_USDT,
) -> PreparedOrder:
    if signal.get("entry_model") == "intrabar":
        return PreparedOrder(
            payload={},
            reason=(
                "intrabar stop-entry is not live-wired: mexc_private.submit_order "
                "has market (5) and limit (1) only, and no plan-order method"
            ),
            executable=False,
        )
    side = spec.side_open_long if signal["side"] == "LONG" else spec.side_open_short
    payload = {
        "symbol": spec.symbol,
        "side": side,
        "vol": int(qty),
        "type": spec.order_type_market,
        "openType": spec.open_type_isolated,
        "leverage": int(leverage),
        "stopLossPrice": stop_price,
        "takeProfitPrice": target_price,
        "externalOid": f"orb-{signal['info_known_ts']}-{signal['side']}",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    return PreparedOrder(
        payload=payload,
        reason="prepared market order with attached stop and target; not submitted",
        executable=True,
    )
