"""Live / paper order execution for Hunt autotrader."""
import json
import time
from typing import Dict, Optional

from .config import SYMBOL
from . import paper_trading
from .autotrader_state import CONFIG, STATE, logger
from .autotrader_sizing import (
    compute_sizing, _log_sizing_attempt, _fetch_available_balance,
    _check_available_margin, _snap_to_tick,
)


def _hunt_thesis(hunt: Dict, extra: Optional[Dict] = None) -> Dict:
    th = {
        "thesis_ts": hunt.get("thesis_ts"),
        "thesis_level": hunt.get("thesis_level"),
        "thesis_invalid": hunt.get("thesis_invalid"),
        "rearm": hunt.get("rearm"),
        "event": hunt.get("event"),
        "gate": hunt.get("gate"),
        "direction": hunt.get("direction"),
        "entry": hunt.get("entry"),
        "stop": hunt.get("stop"),
        "target": hunt.get("target"),
    }
    if extra:
        th.update(extra)
    return {k: v for k, v in th.items() if v is not None}


def _open_from_hunt(hunt: Dict, tf: str, thesis: Optional[Dict] = None) -> None:
    side = hunt.get("direction")
    th = _hunt_thesis(hunt, thesis)
    entry = float(th.get("fill_price") or hunt["entry"])
    sl = float(th.get("stop") or hunt["stop"])
    tp = float(th.get("target") or hunt["target"])
    path = (hunt.get("hunt") or {}).get("m5_path") or hunt.get("v3a_path")
    gate = hunt.get("gate") or ""
    why0 = (hunt.get("why_state") or [""])[0] if isinstance(hunt.get("why_state"), list) else (hunt.get("why_state") or "")
    paper_trading.open_trade(
        symbol=SYMBOL, side=side, entry_price=entry, sl_price=sl, tp_price=tp,
        notional_usd=float(th.get("notional") or CONFIG["notional_usd"]),
        timeframe=tf, brain_state=side, consensus=0, confidence=0,
        note=f"Hunt C-FI {gate} {path} {(hunt.get('event') or '')} {why0}",
        source="AUTO", thesis=th,
    )


def _fresh_price() -> Optional[float]:
    try:
        from .market_data import mexc_market_data as mkt
        ticker = mkt.rest_get(f"/api/v1/contract/ticker?symbol={SYMBOL}")
        if ticker.get("success"):
            return float(ticker["data"]["lastPrice"])
    except Exception:
        logger.exception("fresh ticker fetch failed, falling back to cached price")
    return None


def _mexc_open_position_vol() -> float:
    from .market_data import mexc_private
    pos = mexc_private.get_open_positions(SYMBOL) or []
    total = 0.0
    for p in pos:
        total += float(p.get("holdVol") or p.get("hold_vol") or 0)
    return total


def _live_meta(trade: Optional[Dict]) -> Optional[Dict]:
    if not trade:
        return None
    raw = trade.get("thesis_json") or trade.get("thesis")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("venue") != "MEXC":
        return None
    return data


def _close_live_if_needed(trade: Optional[Dict], exit_px: Optional[float]) -> None:
    meta = _live_meta(trade)
    if not meta:
        return
    try:
        from .market_data import mexc_private
        price = _fresh_price() or exit_px
        mexc_private.close_position(
            symbol=SYMBOL,
            opened_side=trade.get("side") or meta.get("side"),
            vol=float(meta.get("vol") or 0),
            price=price,
            open_type=mexc_private.OPEN_TYPE_ISOLATED,
        )
    except Exception:
        logger.exception("live MEXC close failed — close it by hand on MEXC if still open")


def _sl_tp_valid(side: str, price: float, sl: float, tp: float) -> bool:
    if str(side).upper() == "LONG":
        return sl < price < tp
    return tp < price < sl


def _wait_mexc_fill(side: str, tries: int = 8) -> Optional[float]:
    from .market_data import mexc_private
    want = 1 if str(side).upper() == "LONG" else 2
    last = None
    for _ in range(tries):
        try:
            rows = mexc_private.get_open_positions(SYMBOL) or []
        except Exception:
            rows = []
        for p in rows:
            try:
                pt = int(p.get("positionType") or 0)
            except (TypeError, ValueError):
                pt = 0
            if pt and pt != want:
                continue
            avg = p.get("holdAvgPrice") or p.get("openAvgPrice")
            if avg:
                last = float(avg)
                if last > 0:
                    return last
        time.sleep(0.25)
    return last


def _open_live_from_hunt(hunt: Dict, tf: str, live_price: Optional[float],
                          extra_thesis: Optional[Dict] = None) -> Dict:
    side = hunt.get("direction")
    hunt_entry = float(hunt["entry"])
    hunt_sl = float(hunt["stop"])
    hunt_tp = float(hunt["target"])
    price = _fresh_price() or float(live_price or hunt_entry)
    sl_dist = abs(hunt_entry - hunt_sl)
    tp_dist = abs(hunt_tp - hunt_entry)
    if str(side).upper() == "LONG":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist
    sizing = compute_sizing(override_price=price, entry=price, stop=sl)
    if sizing.get("error"):
        _log_sizing_attempt(sizing, result="REJECTED", reason=sizing["error"])
        raise RuntimeError(f"sizing failed: {sizing['error']}")
    try:
        hold = _mexc_open_position_vol()
    except Exception as e:
        reason = f"cannot confirm MEXC is flat — no live order ({e})"
        _log_sizing_attempt(sizing, result="REJECTED", reason=reason)
        raise RuntimeError(reason)
    if hold > 0:
        reason = "MEXC already has an open position — one trade only"
        _log_sizing_attempt(sizing, result="REJECTED", reason=reason)
        raise RuntimeError(reason)
    margin_check = _check_available_margin(sizing["required_margin"])
    sizing["_margin_check"] = margin_check
    if not margin_check["ok"]:
        _log_sizing_attempt(sizing, result="REJECTED", reason=margin_check["reason"])
        raise RuntimeError(margin_check["reason"])
    price_unit = sizing.get("price_unit") or 0
    price_scale = sizing.get("price_scale") if sizing.get("price_scale") is not None else 2
    submit_price = _snap_to_tick(price, price_unit, price_scale)
    submit_sl = _snap_to_tick(sl, price_unit, price_scale)
    submit_tp = _snap_to_tick(tp, price_unit, price_scale)
    if not _sl_tp_valid(side, submit_price, submit_sl, submit_tp):
        reason = (
            f"Hunt SL/TP no longer valid vs live price {submit_price} "
            f"(sl={submit_sl} tp={submit_tp} side={side}) — skipped live fill"
        )
        _log_sizing_attempt(sizing, result="REJECTED", reason=reason)
        raise RuntimeError(reason)
    from .market_data import mexc_private
    vol = sizing["final_quantity"]
    lev = int(sizing["leverage"])
    pos_type = mexc_private.POSITION_TYPE_LONG if side == "LONG" else mexc_private.POSITION_TYPE_SHORT
    try:
        mexc_private.change_leverage(
            symbol=SYMBOL, leverage=lev, position_type=pos_type,
            open_type=mexc_private.OPEN_TYPE_ISOLATED,
        )
    except Exception as e:
        reason = f"Isolated leverage {lev}x could not be set: {e}"
        _log_sizing_attempt(sizing, result="REJECTED", reason=reason)
        raise RuntimeError(reason)
    mexc_side = mexc_private.SIDE_OPEN_LONG if side == "LONG" else mexc_private.SIDE_OPEN_SHORT
    order_result = mexc_private.submit_order(
        symbol=SYMBOL, side=mexc_side, vol=vol, price=submit_price,
        order_type=mexc_private.ORDER_TYPE_MARKET,
        open_type=mexc_private.OPEN_TYPE_ISOLATED, leverage=lev,
        stop_loss_price=submit_sl, take_profit_price=submit_tp,
        external_oid=f"mib{int(time.time() * 1000)}",
    )
    _log_sizing_attempt(
        sizing, result="SUBMITTED", order_result=order_result,
        submitted={"price": submit_price, "stop_loss_price": submit_sl, "take_profit_price": submit_tp,
                   "vol": vol, "leverage": lev, "open_type": "ISOLATED",
                   "risk_pct": sizing.get("risk_pct"), "risk_usd": sizing.get("risk_usd")},
    )
    fill = _wait_mexc_fill(side) or price
    _open_from_hunt(hunt, tf, thesis={
        "venue": "MEXC", "order_id": order_result.get("data"), "vol": vol,
        "notional": sizing.get("final_notional"), "leverage": lev, "side": side,
        "margin_mode": "ISOLATED", "risk_pct": sizing.get("risk_pct"),
        "risk_usd": sizing.get("risk_usd"), "sizing_mode": sizing.get("sizing_mode"),
        "fill_price": fill, "live_price": price, "stop": submit_sl,
        "target": submit_tp, "hunt_entry": hunt_entry,
        "thesis_ts": hunt.get("thesis_ts"),
        "thesis_level": hunt.get("thesis_level"),
        "thesis_invalid": hunt.get("thesis_invalid"),
        "rearm": hunt.get("rearm"),
        "event": hunt.get("event"),
        "gate": hunt.get("gate"),
        **(extra_thesis or {}),
    })
    try:
        rows = paper_trading.list_trades(status="OPEN")
        live_rows = [t for t in rows if t.get("source") == "AUTO"]
        if live_rows:
            paper_trading.update_open_fill(live_rows[0]["id"], float(fill))
    except Exception:
        logger.exception("could not persist MEXC fill onto shadow row")
    return order_result