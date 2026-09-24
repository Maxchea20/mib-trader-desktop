"""Keep the local AUTO row glued to the real MEXC Isolated position.

The paper table is a shadow. MEXC is the book.
A local trail / last-price tick must not close the shadow while MEXC
still holds the Isolated ticket.
"""
from typing import Optional, Dict

from .config import SYMBOL
from . import paper_trading
from .autotrader_state import logger, STATE


def _install_price_guard() -> None:
    orig = paper_trading.check_open_trades
    if getattr(orig, "_mexc_guard", False):
        return

    def guarded(price):
        if price is None:
            return 0
        closed = 0
        for t in paper_trading.list_trades("OPEN"):
            if paper_trading._is_mexc_live(t):
                continue
            side, sl, tp = t["side"], t["sl_price"], t["tp_price"]
            hit = None
            if side == paper_trading.LONG:
                if sl is not None and price <= sl:
                    hit = ("SL", sl)
                elif tp is not None and price >= tp:
                    hit = ("TP", tp)
            else:
                if sl is not None and price >= sl:
                    hit = ("SL", sl)
                elif tp is not None and price <= tp:
                    hit = ("TP", tp)
            if hit:
                paper_trading.close_trade(t["id"], hit[1], hit[0])
                closed += 1
                # Same reasoning as the other close paths -- this is the
                # MEXC-side SL/TP-fill reconciliation path specifically.
                STATE["normal_base"] = None
                STATE["normal_base_captured_at"] = None
        return closed

    guarded._mexc_guard = True
    paper_trading.check_open_trades = guarded


def flatten_mexc(side: Optional[str], vol: Optional[float], exit_px: Optional[float]) -> Optional[Dict]:
    from .market_data import mexc_private
    from .autotrader_exec import _fresh_price, _mexc_open_position_vol
    live_vol = vol
    try:
        live_vol = _mexc_open_position_vol() or vol or 0
    except Exception:
        live_vol = vol or 0
    if not live_vol:
        return None
    price = _fresh_price() or exit_px
    opened = side or "LONG"
    try:
        rows = mexc_private.get_open_positions(SYMBOL) or []
        if rows:
            pt = int(rows[0].get("positionType") or 0)
            opened = "LONG" if pt == 1 else "SHORT"
            live_vol = float(rows[0].get("holdVol") or live_vol)
    except Exception:
        pass
    return mexc_private.close_position(
        symbol=SYMBOL,
        opened_side=opened,
        vol=float(live_vol),
        price=price,
        open_type=mexc_private.OPEN_TYPE_ISOLATED,
    )


def revive_shadow_if_mexc_open() -> Optional[Dict]:
    if paper_trading.list_trades("OPEN"):
        for t in paper_trading.list_trades("OPEN"):
            if t.get("source") == "AUTO":
                return t
    snap = paper_trading._mexc_open_snapshot(SYMBOL)
    if not snap:
        return None
    rows = paper_trading.list_trades("CLOSED")
    last = next((t for t in rows if t.get("source") == "AUTO"), None)
    if not last:
        return None
    th = paper_trading._thesis(last)
    sl = th.get("stop") or th.get("hunt_stop") or 81035.5
    try:
        sl = float(sl)
    except (TypeError, ValueError):
        sl = last.get("sl_price")
    from .market_data import database as mdb
    import sqlite3
    conn = sqlite3.connect(mdb.db_path())
    try:
        conn.execute(
            "UPDATE paper_trades SET status='OPEN', closed_at=NULL, exit_price=NULL, "
            "exit_reason=NULL, pnl=NULL, pnl_pct=NULL, sl_price=? WHERE id=?",
            (sl, last["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    logger.warning("revived AUTO shadow %s with SL %s — MEXC Isolated still open", last["id"], sl)
    return paper_trading.get_trade(last["id"])