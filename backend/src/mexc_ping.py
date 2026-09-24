"""Min-size MEXC Isolated ping: prove an order actually reaches the venue.

Opens 1 contract Isolated market LONG at 10x, waits for holdVol, then
flattens. Never uses Hunt sizing / 90% allocation.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .config import SYMBOL
from .autotrader_state import CONFIG, STATE, _live_armed
from .market_data import mexc_private


PING_VOL = 1
PING_LEVERAGE = 10


def probe() -> Dict[str, Any]:
    live_armed = _live_armed()
    keys = mexc_private.keys_present()
    out: Dict[str, Any] = {
        "ok": False,
        "keys_present": keys,
        "live_armed": live_armed,
        "mode": CONFIG.get("mode"),
        "entry_engine": CONFIG.get("entry_engine"),
        "autotrade_enabled": CONFIG.get("enabled"),
        "last_action": STATE.get("last_action"),
        "last_reason": STATE.get("last_reason"),
        "would_send_live": bool(
            CONFIG.get("mode") == "LIVE" and live_armed and CONFIG.get("enabled")
        ),
    }
    if not keys:
        out["error"] = "MEXC_API_KEY / MEXC_API_SECRET missing in .env"
        return out
    try:
        assets = mexc_private.get_assets()
        usdt = next((a for a in assets if a.get("currency") == "USDT"), {}) or {}
        out["equity"] = float(usdt.get("equity") or 0)
        out["available_balance"] = float(usdt.get("availableBalance") or 0)
    except Exception as e:
        out["error"] = f"account read failed: {e}"
        return out
    try:
        pos = mexc_private.get_open_positions(SYMBOL) or []
        vol = 0.0
        for p in pos:
            vol += float(p.get("holdVol") or p.get("hold_vol") or 0)
        out["open_vol"] = vol
        out["open_positions"] = len(pos)
    except Exception as e:
        out["error"] = f"positions read failed: {e}"
        return out
    if not live_armed:
        out["error"] = "MEXC_LIVE_TRADING_ENABLED is not true — paper only"
        return out
    out["ok"] = True
    return out


def _wait_vol(want_side: Optional[str] = None, tries: int = 10) -> float:
    want = None
    if want_side:
        want = 1 if str(want_side).upper() == "LONG" else 2
    last = 0.0
    for _ in range(tries):
        rows = mexc_private.get_open_positions(SYMBOL) or []
        total = 0.0
        for p in rows:
            try:
                pt = int(p.get("positionType") or 0)
            except (TypeError, ValueError):
                pt = 0
            if want and pt and pt != want:
                continue
            total += float(p.get("holdVol") or p.get("hold_vol") or 0)
        last = total
        if total > 0:
            return total
        time.sleep(0.3)
    return last


def ping() -> Dict[str, Any]:
    """Open 1 Isolated contract, then flatten. Real money, tiny size."""
    pre = probe()
    if not pre.get("ok"):
        return {"ok": False, "stage": "probe", **pre}
    if float(pre.get("open_vol") or 0) > 0:
        return {
            "ok": False,
            "stage": "guard",
            "error": "MEXC already has an Isolated position — flatten it first",
            **{k: pre[k] for k in ("open_vol", "available_balance", "equity")},
        }

    opened = None
    closed = None
    try:
        mexc_private.change_leverage(
            symbol=SYMBOL,
            leverage=PING_LEVERAGE,
            position_type=mexc_private.POSITION_TYPE_LONG,
            open_type=mexc_private.OPEN_TYPE_ISOLATED,
        )
        opened = mexc_private.submit_order(
            symbol=SYMBOL,
            side=mexc_private.SIDE_OPEN_LONG,
            vol=PING_VOL,
            order_type=mexc_private.ORDER_TYPE_MARKET,
            open_type=mexc_private.OPEN_TYPE_ISOLATED,
            leverage=PING_LEVERAGE,
            external_oid=f"mibping{int(time.time() * 1000)}",
        )
        vol = _wait_vol("LONG") or float(PING_VOL)
        closed = mexc_private.close_position(
            symbol=SYMBOL,
            opened_side="LONG",
            vol=vol,
            open_type=mexc_private.OPEN_TYPE_ISOLATED,
        )
        STATE["last_action"] = f"MEXC PING OK order {opened.get('data')}"
        return {
            "ok": True,
            "stage": "flattened",
            "symbol": SYMBOL,
            "vol": PING_VOL,
            "leverage": PING_LEVERAGE,
            "open_order": opened,
            "close_order": closed,
            "reached_mexc": True,
        }
    except Exception as e:
        leftover = 0.0
        try:
            leftover = _wait_vol("LONG", tries=3)
            if leftover > 0:
                closed = mexc_private.close_position(
                    symbol=SYMBOL,
                    opened_side="LONG",
                    vol=leftover,
                    open_type=mexc_private.OPEN_TYPE_ISOLATED,
                )
        except Exception:
            pass
        STATE["last_action"] = f"MEXC PING FAILED {e}"
        return {
            "ok": False,
            "stage": "order",
            "error": str(e),
            "open_order": opened,
            "close_order": closed,
            "leftover_vol": leftover,
            "reached_mexc": opened is not None,
        }
