"""Wire V1b reevaluate() to an open paper/live trade on each closed 5m."""
from __future__ import annotations
from typing import Any, Dict, Optional

from .lifecycle import position_from_fire, reevaluate, Position


def position_from_open_trade(trade: Dict[str, Any], *, equity: float = 1000.0, risk_pct: float = 0.02) -> Position:
    fire = {
        "direction": trade.get("side") or trade.get("direction"),
        "entry": float(trade["entry_price"]),
        "stop": float(trade.get("sl_price") or trade["entry_price"]),
        "target": trade.get("tp_price"),
        "atr_15m": abs(float(trade["entry_price"]) - float(trade.get("sl_price") or trade["entry_price"])),
        "event": (trade.get("thesis") or {}).get("event") if isinstance(trade.get("thesis"), dict) else None,
        "why_state": ["open position"],
    }
    return position_from_fire(
        fire,
        trade_id=str(trade.get("id") or "open"),
        equity=equity,
        risk_pct=risk_pct,
        opened_ts=trade.get("opened_at"),
    )


def tick_5m(
    trade: Dict[str, Any],
    *,
    price: float,
    high: float,
    low: float,
    structure_event: Optional[str] = None,
    structure_dir: Optional[str] = None,
    level_lost: bool = False,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """One closed 5m bar. Returns HOLD / TRAIL / EXIT plus sl if trail moved."""
    pos = position_from_open_trade(trade)
    return reevaluate(
        pos,
        price=float(price),
        high=float(high),
        low=float(low),
        structure_event=structure_event,
        structure_dir=structure_dir,
        level_lost=level_lost,
        now_ts=now_ts,
    )
