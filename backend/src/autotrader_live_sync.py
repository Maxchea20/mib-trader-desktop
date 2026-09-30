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
    """Close ONLY what the bot itself opened.

    `vol` is the contract count of the bot's own MEXC trade. It is never
    replaced by the account's live position size, and a position on the other
    side (or nothing) is left alone, so a manual trade is never closed here.
    """
    from .market_data import mexc_private
    from .autotrader_exec import _fresh_price
    own = float(vol or 0)
    if own <= 0 or not side:
        logger.warning("flatten_mexc skipped: no bot-owned volume — manual position left untouched")
        return None
    want = 1 if str(side).upper() == "LONG" else 2
    try:
        rows = mexc_private.get_open_positions(SYMBOL) or []
    except Exception:
        logger.exception("flatten_mexc skipped: cannot read MEXC positions")
        return None
    live = 0.0
    for r in rows:
        try:
            pt = int(r.get("positionType") or 0)
        except (TypeError, ValueError):
            pt = 0
        if pt == want:
            live += float(r.get("holdVol") or r.get("hold_vol") or 0)
    close_vol = min(own, live)
    if close_vol <= 0:
        logger.warning("flatten_mexc skipped: MEXC holds no %s position — nothing of the bot's to close", side)
        return None
    return mexc_private.close_position(
        symbol=SYMBOL,
        opened_side=str(side).upper(),
        vol=close_vol,
        price=_fresh_price() or exit_px,
        open_type=mexc_private.OPEN_TYPE_ISOLATED,
    )


# A live MEXC position with no OPEN AUTO row is a MANUAL trade. Reviving an old
# AUTO row over it would let the bot manage (and possibly close) that trade.
REVIVE_SHADOW = False


def revive_shadow_if_mexc_open() -> Optional[Dict]:
    if not REVIVE_SHADOW:
        return None
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