"""Live executor for Swing AI: places the AI's plan on MEXC.  The AI still decides everything; this module only sends, cancels and
closes orders and reports what the exchange holds.  It is the ONLY place in Swing AI that can move real money.

Real orders are sent only when ALL of these hold (see `block_reason`):
  1. the panel is in LIVE mode,
  2. MEXC_LIVE_TRADING_ENABLED=true (the app-wide switch) AND SWING_AI_LIVE_ARMED=YES in the environment,
  3. MEXC_API_KEY / MEXC_API_SECRET are present.
Hard limits that no setting or AI answer can raise: notional <= SWING_AI_LIVE_MAX_USD (default 200), risk <= SWING_AI_LIVE_MAX_RISK_PCT
(default 0.5) of the available USDT balance, one position at a time, and nothing is sent while MEXC already holds any position or open
order on the symbol (a manual trade is never touched).  Only MARKET and LIMIT entries are supported; the stop and target are attached to
the order so they live on the exchange.  The existing client in market_data/mexc_private.py is reused unchanged."""
import logging
import math
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
SYMBOL = "BTC_USDT"
DEFAULT_MAX_USD = 200.0
DEFAULT_MAX_RISK_PCT = 0.5
FILL_WAIT_TRIES = 8
BUFFER = 0.02                      # keep 2% of the required margin free


class LiveError(Exception):
    pass


def _client():
    from ..market_data import mexc_private
    return mexc_private


def _detail() -> Dict[str, Any]:
    from ..market_data import mexc_market_data as mkt
    d = mkt.rest_get(f"/api/v1/contract/detail?symbol={SYMBOL}")
    if not d.get("success"):
        raise LiveError("contract detail not available")
    d = d["data"]
    return (d[0] if isinstance(d, list) and d else d) or {}


def max_usd() -> float:
    try:
        return float(os.environ.get("SWING_AI_LIVE_MAX_USD") or DEFAULT_MAX_USD)
    except ValueError:
        return DEFAULT_MAX_USD


def max_risk_pct() -> float:
    try:
        return float(os.environ.get("SWING_AI_LIVE_MAX_RISK_PCT") or DEFAULT_MAX_RISK_PCT)
    except ValueError:
        return DEFAULT_MAX_RISK_PCT


def env_armed() -> bool:
    return os.environ.get("MEXC_LIVE_TRADING_ENABLED", "").strip().lower() == "true" and os.environ.get("SWING_AI_LIVE_ARMED", "").strip() == "YES"


def block_reason(settings: Dict[str, Any], client=None) -> Optional[str]:
    """None when real orders may be sent, otherwise the reason they may not."""
    if settings.get("mode") != "LIVE":
        return "panel is in PAPER mode"
    if not env_armed():
        return "not armed: set MEXC_LIVE_TRADING_ENABLED=true and SWING_AI_LIVE_ARMED=YES in the environment and restart"
    c = client or _client()
    if not c.keys_present():
        return "MEXC_API_KEY / MEXC_API_SECRET not set"
    return None


def _snap(value: float, unit: float, scale: int) -> float:
    if unit and unit > 0:
        value = round(value / unit) * unit
    return round(value, scale)


def _hold(rows, side: str) -> float:
    want = 1 if side == "LONG" else 2
    total = 0.0
    for r in rows or []:
        try:
            pt = int(r.get("positionType") or 0)
        except (TypeError, ValueError):
            pt = 0
        if pt == want:
            total += float(r.get("holdVol") or r.get("hold_vol") or 0)
    return total


def exchange_state(side: str, order_id: Optional[str] = None, client=None) -> Dict[str, Any]:
    """What MEXC actually holds for this bot trade.  Raises on a read failure (callers must not assume 'flat')."""
    c = client or _client()
    rows = c.get_open_positions(SYMBOL) or []
    vol, avg = _hold(rows, side), None
    want = 1 if side == "LONG" else 2
    for r in rows:
        if int(r.get("positionType") or 0) == want:
            avg = float(r.get("holdAvgPrice") or r.get("openAvgPrice") or 0) or avg
    order_open = False
    if order_id:
        order_open = any(str(o.get("orderId") or o.get("id")) == str(order_id) for o in (c.get_open_orders(SYMBOL) or []))
    return {"position_vol": vol, "avg_price": avg, "order_open": order_open}


def open_order(plan: Dict[str, Any], cfg, client=None, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Sends the plan.  Returns {ok, error, order_id, vol, entry_type, leverage, fill_price}.  Never raises."""
    c = client or _client()
    try:
        return _open(plan, cfg, c, detail)
    except Exception as e:                                       # any failure means NO order is assumed to exist
        logger.exception("live order failed")
        return {"ok": False, "error": f"live order failed: {e}"}


def _open(plan, cfg, c, detail) -> Dict[str, Any]:
    side, etype = plan["side"], plan["entry_type"]
    if etype not in ("MARKET", "LIMIT"):
        return {"ok": False, "error": f"live trading supports MARKET and LIMIT entries only (got {etype})"}
    if c.get_open_positions(SYMBOL):
        return {"ok": False, "error": "MEXC already has an open position on the symbol - not touching it"}
    if c.get_open_orders(SYMBOL):
        return {"ok": False, "error": "MEXC already has open orders on the symbol - not touching them"}
    d = detail or _detail()
    cs, vol_unit = float(d.get("contractSize") or 0), float(d.get("volUnit") or 1)
    min_vol, max_vol = float(d.get("minVol") or vol_unit), float(d.get("maxVol") or float("inf"))
    unit, scale = float(d.get("priceUnit") or 0), int(d.get("priceScale") or 2)
    max_lev = float(d.get("maxLeverage") or 1)
    if not cs or d.get("apiAllowed") is False:
        return {"ok": False, "error": "contract not tradeable through the API"}
    px, sl, tp = _snap(plan["entry"], unit, scale), _snap(plan["sl"], unit, scale), _snap(plan["tp"], unit, scale)
    long_ = side == "LONG"
    if not ((long_ and sl < px < tp) or (not long_ and tp < px < sl)):
        return {"ok": False, "error": "stop/target not valid around the entry after snapping to the tick size"}
    asset = next((a for a in c.get_assets() if a.get("currency") == "USDT"), None)
    avail = float((asset or {}).get("availableBalance") or 0)
    if avail <= 0:
        return {"ok": False, "error": "no available USDT balance"}
    lev = int(max(1, min(float(cfg.max_leverage), max_lev)))
    risk_dist = abs(px - sl)
    risk_usd = avail * min(float(cfg.risk_pct), max_risk_pct()) / 100.0
    notional_cap = min(float(cfg.max_position_usd), max_usd(), avail * lev / (1 + BUFFER))
    raw = min(risk_usd / (risk_dist * cs), notional_cap / (px * cs))
    vol = math.floor(raw / vol_unit + 1e-9) * vol_unit
    if vol < min_vol:
        return {"ok": False, "error": f"size {raw:.2f} contracts is below the minimum {min_vol} for this account/limits - not rounded up"}
    vol = min(vol, max_vol)
    margin = vol * cs * px / lev
    if avail < margin * (1 + BUFFER):
        return {"ok": False, "error": f"insufficient available balance for margin {margin:.2f} USDT"}
    pos_type = c.POSITION_TYPE_LONG if long_ else c.POSITION_TYPE_SHORT
    c.change_leverage(symbol=SYMBOL, leverage=lev, position_type=pos_type, open_type=c.OPEN_TYPE_ISOLATED)
    res = c.submit_order(
        symbol=SYMBOL, side=c.SIDE_OPEN_LONG if long_ else c.SIDE_OPEN_SHORT, vol=vol, price=px,
        order_type=c.ORDER_TYPE_MARKET if etype == "MARKET" else c.ORDER_TYPE_LIMIT,
        open_type=c.OPEN_TYPE_ISOLATED, leverage=lev, stop_loss_price=sl, take_profit_price=tp,
        external_oid=f"swing{int(time.time() * 1000)}")
    order_id = res.get("data")
    if isinstance(order_id, dict):
        order_id = order_id.get("orderId")
    if not order_id:
        return {"ok": False, "error": f"MEXC returned no order id: {res}"}
    out = {"ok": True, "error": None, "order_id": str(order_id), "vol": vol, "entry_type": etype, "leverage": lev, "contract_size": cs,
           "px": px, "sl": sl, "tp": tp, "risk_usd": vol * cs * risk_dist, "notional": vol * cs * px, "fill_price": None}
    if etype == "MARKET":
        for _ in range(FILL_WAIT_TRIES):
            st = exchange_state(side, client=c)
            if st["position_vol"] > 0:
                out["fill_price"] = st["avg_price"]
                break
            time.sleep(0.25)
    return out


def cancel_order(order_id: Optional[str], client=None) -> bool:
    if not order_id:
        return False
    try:
        (client or _client()).cancel_orders([str(order_id)])
        return True
    except Exception:
        logger.exception("live cancel failed for %s - check MEXC open orders", order_id)
        return False


def flatten(side: str, vol: float, client=None) -> bool:
    """Market-close at most the bot's own volume; a position on the other side (or none) is left alone."""
    c = client or _client()
    try:
        live = _hold(c.get_open_positions(SYMBOL) or [], side)
        qty = min(float(vol or 0), live)
        if qty <= 0:
            return False
        c.close_position(symbol=SYMBOL, opened_side=side, vol=qty, open_type=c.OPEN_TYPE_ISOLATED)
        return True
    except Exception:
        logger.exception("live close failed - close the position by hand on MEXC")
        return False
