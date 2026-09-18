"""Wire V1b reevaluate() to an open paper/live trade on each closed 5m."""
from __future__ import annotations
from typing import Any, Dict, Optional

from .lifecycle import position_from_fire, reevaluate, Position


def _thesis(trade: Dict[str, Any]) -> Dict[str, Any]:
    raw = trade.get("thesis")
    if isinstance(raw, dict):
        return raw
    raw = trade.get("thesis_json")
    if isinstance(raw, str):
        try:
            import json
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def position_from_open_trade(trade: Dict[str, Any], *, equity: float = 1000.0, risk_pct: float = 0.02) -> Position:
    th = _thesis(trade)
    fire = {
        "direction": trade.get("side") or trade.get("direction"),
        "entry": float(trade["entry_price"]),
        "stop": float(trade.get("sl_price") or trade["entry_price"]),
        "target": trade.get("tp_price"),
        "atr_15m": abs(float(trade["entry_price"]) - float(trade.get("sl_price") or trade["entry_price"])),
        "event": th.get("event"),
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


def _opened_ts(trade: Dict[str, Any]) -> Optional[int]:
    th = _thesis(trade)
    if trade.get("opened_at"):
        try:
            return int(trade["opened_at"])
        except (TypeError, ValueError):
            pass
    if th.get("thesis_ts"):
        try:
            return int(th["thesis_ts"])
        except (TypeError, ValueError):
            pass
    return None


def _frozen_invalid(trade: Dict[str, Any]) -> Optional[float]:
    th = _thesis(trade)
    v = th.get("thesis_invalid")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def si_checklist(
    *,
    side: str,
    opened_at: Optional[int],
    bar_15m_ts: Optional[int],
    close: Optional[float],
    choch_against: bool,
    parent: Optional[float],
    level: Optional[float],
) -> Dict[str, Any]:
    """Fill price is not an input. Fire-bar 15m cannot kill.

    Kill when a newer 15m prints CHoCH against, or close is beyond
    both the frozen parent swing and the frozen 15m level.
    """
    new_15m = (
        bar_15m_ts is not None
        and (opened_at is None or int(bar_15m_ts) > int(opened_at))
    )
    beyond_parent = False
    beyond_level = False
    if new_15m and close is not None:
        if parent is not None:
            if side == "LONG" and close < parent:
                beyond_parent = True
            if side == "SHORT" and close > parent:
                beyond_parent = True
        if level is not None:
            if side == "LONG" and close < level:
                beyond_level = True
            if side == "SHORT" and close > level:
                beyond_level = True
    choch = bool(new_15m and choch_against)
    return {
        "new_15m": bool(new_15m),
        "choch_against": choch,
        "beyond_parent": beyond_parent,
        "beyond_level": beyond_level,
        "hits": int(choch) + int(beyond_parent) + int(beyond_level),
        "kill_choch": choch,
        "kill_struct": (not choch) and beyond_parent and beyond_level,
    }


def manage_open_on_5m(state: Dict[str, Any], open_trade: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """EXIT / TRAIL / HOLD. SI is not fill-vs-15m-close.

    Kill structure only when a NEW closed 15m (ts > opened_at) meets
    CHoCH against, or TWO of: CHoCH against, close beyond frozen parent,
    close beyond frozen level. Hard SL and TP always exit.
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
        si = {
            "new_15m": False,
            "choch_against": False,
            "beyond_parent": False,
            "beyond_level": False,
            "hits": 0,
            "kill_choch": False,
            "kill_struct": False,
        }
        try:
            from ..structure.observe import observe as obs_structure
            w15 = dao.read_closed_candles("15m", limit=320)
            opened = _opened_ts(open_trade)
            side = open_trade["side"]
            th = _thesis(open_trade)
            parent = _frozen_invalid(open_trade)
            level = th.get("thesis_level")
            try:
                level = float(level) if level is not None else None
            except (TypeError, ValueError):
                level = None
            choch_raw = False
            last_ts = None
            close = None
            if len(w15) >= 60:
                last15 = w15[-1]
                last_ts = int(last15.get("ts") or 0)
                close = float(last15["close"])
                if opened is None or last_ts > int(opened):
                    st = obs_structure(w15, "15m")
                    for e in (getattr(st, "events", None) or []):
                        et = getattr(e, "event_type", "")
                        if et in ("CHoCH", "CHOCH") and getattr(e, "timestamp", None) == last15.get("ts"):
                            st_ev, st_dir = et, getattr(e, "direction", None)
                            sd = (st_dir or "").upper()
                            choch_raw = (
                                side == "LONG" and sd in ("BEARISH", "SHORT", "DOWN")
                            ) or (
                                side == "SHORT" and sd in ("BULLISH", "LONG", "UP")
                            )
                            break
            si = si_checklist(
                side=side,
                opened_at=opened,
                bar_15m_ts=last_ts,
                close=close,
                choch_against=choch_raw,
                parent=parent,
                level=level,
            )
        except Exception:
            pass
        rec = tick_5m(
            open_trade,
            price=float(bar["close"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            structure_event=st_ev if si.get("kill_choch") else None,
            structure_dir=st_dir if si.get("kill_choch") else None,
            level_lost=bool(si.get("kill_struct")),
            now_ts=bar.get("ts"),
        )
        rec["si_hits"] = si
        if rec.get("action") == EXIT:
            px = rec.get("exit_px") or float(bar["close"])
            paper_trading.close_trade(open_trade["id"], px, rec.get("exit_kind") or "BRAIN_EXIT")
        elif rec.get("action") == TRAIL and rec.get("sl") is not None:
            paper_trading.update_sl(open_trade["id"], float(rec["sl"]))
        return rec
    except Exception:
        return None
