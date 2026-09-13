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


def manage_open_on_5m(state: Dict[str, Any], open_trade: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """If an AUTO trade is open and a new 5m just closed, apply V1b.

    EXIT closes the paper trade. TRAIL tightens sl_price.
    Safe to call every few seconds. Never raises.
    """
    if not open_trade:
        return None
    try:
        from ..market_data import data_access as dao
        from .. import paper_trading
        from .lifecycle import EXIT, TRAIL
        c5 = dao.read_closed_candles("5m", limit=4)
        if not c5:
            return None
        ts = c5[-1]["ts"]
        if state.get("last_5m_ts") == ts:
            return None
        state["last_5m_ts"] = ts
        bar = c5[-1]
        st_ev = st_dir = None
        level_lost = False
        try:
            from ..structure.observe import observe as obs_structure
            w15 = dao.read_closed_candles("15m", limit=320)
            if len(w15) >= 60:
                st = obs_structure(w15, "15m")
                last15 = w15[-1]
                for e in (getattr(st, "events", None) or []):
                    et = getattr(e, "event_type", "")
                    if et in ("CHoCH", "CHOCH") and getattr(e, "timestamp", None) == last15.get("ts"):
                        st_ev, st_dir = et, getattr(e, "direction", None)
                        break
                entry = float(open_trade["entry_price"])
                side = open_trade["side"]
                if side == "LONG" and last15["close"] < entry:
                    level_lost = True
                if side == "SHORT" and last15["close"] > entry:
                    level_lost = True
        except Exception:
            pass
        rec = tick_5m(
            open_trade,
            price=float(bar["close"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            structure_event=st_ev,
            structure_dir=st_dir,
            level_lost=level_lost,
            now_ts=bar.get("ts"),
        )
        if rec.get("action") == EXIT:
            px = rec.get("exit_px") or float(bar["close"])
            paper_trading.close_trade(open_trade["id"], px, rec.get("exit_kind") or "BRAIN_EXIT")
        elif rec.get("action") == TRAIL and rec.get("sl") is not None:
            paper_trading.update_sl(open_trade["id"], float(rec["sl"]))
        return rec
    except Exception:
        return None
