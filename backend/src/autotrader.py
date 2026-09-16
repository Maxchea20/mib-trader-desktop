"""Auto-trade: Hunt C-FI entry on each closed 5m + V1b lifecycle.
Paper always works. LIVE mode places real MEXC Isolated orders ONLY if both:
  1) CONFIG["mode"] == "LIVE" (the UI toggle), AND
  2) env var MEXC_LIVE_TRADING_ENABLED=true is set.
The env var is a second, deliberate switch independent of the UI — a stray
click or a UI bug can't send real orders on its own. Both must be true.
One AUTO position at a time. New signal does not override.
Live fills also open a local AUTO shadow so lifecycle / one-position work.
If LIVE is on but the env flag is off, Hunt still papers the fill.

Sizing:
  NORMAL      — old notional: locked Available x allocation% x leverage.
  COMPOUNDING — 2% of live Available as dollar risk at the Hunt stop.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from .config import SYMBOL, ANALYSIS_LOOKBACK, TF_SECONDS
from .market_data import data_access as dao
from . import analysis_service
from . import paper_trading
from .brain.lifecycle_tick import manage_open_on_5m
from .brain.weather import side_allowed
from .brain.observation_hunt_c_fi import HUNT_VERSION_C_FI

logger = logging.getLogger(__name__)

SAFETY_BUFFER_PCT = 0.02
MIN_LIVE_LEVERAGE = 10.0
PERSIST_KEYS = (
    "enabled", "mode", "timeframe", "notional_usd", "sl_atr_mult", "tp_atr_mult",
    "cooldown_bars_normal", "cooldown_bars_after_failure", "sizing_mode",
    "allocation_pct", "risk_pct", "leverage", "max_live_notional_usd", "margin_mode",
)

AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "live_sizing_log.jsonl"

CONFIG = {
    "enabled": True,
    "mode": "PAPER",
    "timeframe": "15m",
    "hunt_version": HUNT_VERSION_C_FI,
    "notional_usd": 1000.0,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    "cooldown_bars_normal": 1,
    "cooldown_bars_after_failure": 3,
    "sizing_mode": "NORMAL",
    "allocation_pct": 20.0,
    "risk_pct": 2.0,
    "leverage": 10.0,
    "max_live_notional_usd": 1000.0,
    "margin_mode": "ISOLATED",
}

STATE = {
    "last_candle_ts": None,
    "last_hunt_5m_ts": None,
    "last_state": None,
    "last_action": None,
    "last_reason": None,
    "last_eval_at": None,
    "last_close_ts": None,
    "last_close_reason": None,
    "last_5m_ts": None,
    "last_lifecycle": None,
    "last_hunt": None,
    "normal_base": None,
    "normal_base_captured_at": None,
}


def _config_path() -> Path:
    db = os.environ.get("MARKET_DB_PATH")
    if db:
        return Path(db).resolve().parent / "autotrade_config.json"
    return Path(__file__).resolve().parent.parent / "data" / "autotrade_config.json"


def _load_persisted() -> None:
    path = _config_path()
    try:
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        for k in PERSIST_KEYS:
            if k in data and data[k] is not None:
                CONFIG[k] = data[k]
        CONFIG["hunt_version"] = HUNT_VERSION_C_FI
        CONFIG["margin_mode"] = "ISOLATED"
        if float(CONFIG.get("leverage") or 0) < MIN_LIVE_LEVERAGE:
            CONFIG["leverage"] = MIN_LIVE_LEVERAGE
        if CONFIG.get("risk_pct") is None:
            CONFIG["risk_pct"] = 2.0
    except Exception:
        logger.exception("could not load persisted autotrade config")


def _persist() -> None:
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = {k: CONFIG.get(k) for k in PERSIST_KEYS}
        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("could not persist autotrade config")


_load_persisted()


def _live_armed() -> bool:
    return os.environ.get("MEXC_LIVE_TRADING_ENABLED", "").lower() == "true"


def _open_auto() -> Optional[Dict]:
    for t in paper_trading.list_trades("OPEN"):
        if t.get("source") == "AUTO":
            return t
    return None


def _hunt_levels(entry: Optional[float] = None, stop: Optional[float] = None):
    hunt = STATE.get("last_hunt") or {}
    if entry is None:
        entry = hunt.get("entry")
    if stop is None:
        stop = hunt.get("stop")
    try:
        entry_f = float(entry) if entry is not None else None
        stop_f = float(stop) if stop is not None else None
    except (TypeError, ValueError):
        return None, None
    return entry_f, stop_f


def _hunt_pnl_preview(sizing: Dict) -> Dict:
    hunt = STATE.get("last_hunt") or {}
    out = {
        "sl_price": hunt.get("stop"),
        "tp_price": hunt.get("target"),
        "entry_price": hunt.get("entry"),
        "direction": hunt.get("direction"),
        "sl_pnl_usd": None,
        "tp_pnl_usd": None,
    }
    try:
        entry = float(hunt["entry"]) if hunt.get("entry") is not None else None
        sl = float(hunt["stop"]) if hunt.get("stop") is not None else None
        tp = float(hunt["target"]) if hunt.get("target") is not None else None
        side = hunt.get("direction")
        vol = sizing.get("final_quantity")
        cs = sizing.get("contract_size")
        if entry and sl and tp and vol and cs and side:
            sign = 1.0 if str(side).upper() == "LONG" else -1.0
            out["sl_pnl_usd"] = round((sl - entry) * sign * float(vol) * float(cs), 2)
            out["tp_pnl_usd"] = round((tp - entry) * sign * float(vol) * float(cs), 2)
    except Exception:
        pass
    return out


def status() -> Dict:
    from .market_data import mexc_private
    sizing = compute_sizing()
    pnl = _hunt_pnl_preview(sizing)
    return {
        "config": CONFIG,
        "state": STATE,
        "open_auto_trade": _open_auto(),
        "sizing_preview": sizing,
        "live_armed": _live_armed(),
        "keys_present": mexc_private.keys_present(),
        "sl_price": pnl["sl_price"],
        "tp_price": pnl["tp_price"],
        "sl_pnl_usd": pnl["sl_pnl_usd"],
        "tp_pnl_usd": pnl["tp_pnl_usd"],
    }


def _fetch_available_balance() -> float:
    from .market_data import mexc_private
    assets = mexc_private.get_assets()
    usdt = next((a for a in assets if a.get("currency") == "USDT"), None)
    if not usdt:
        raise RuntimeError("no USDT asset entry returned by MEXC")
    return float(usdt.get("availableBalance") or 0)


def _capture_normal_base() -> float:
    bal = _fetch_available_balance()
    STATE["normal_base"] = bal
    STATE["normal_base_captured_at"] = time.time()
    return bal


def update(payload: Dict) -> Dict:
    warnings: List[str] = []
    if "enabled" in payload:
        CONFIG["enabled"] = bool(payload["enabled"])
    if "mode" in payload:
        mode = str(payload["mode"]).upper()
        if mode in ("PAPER", "LIVE"):
            CONFIG["mode"] = mode
        else:
            warnings.append(f"mode: '{payload['mode']}' is not PAPER or LIVE — unchanged")
    if payload.get("timeframe"):
        CONFIG["timeframe"] = str(payload["timeframe"])
    if "sizing_mode" in payload and payload["sizing_mode"] is not None:
        sm = str(payload["sizing_mode"]).upper()
        if sm in ("NORMAL", "COMPOUNDING"):
            prev = CONFIG.get("sizing_mode")
            CONFIG["sizing_mode"] = sm
            if prev == "COMPOUNDING" and sm == "NORMAL":
                try:
                    _capture_normal_base()
                except Exception as e:
                    warnings.append(f"refresh_normal_base: failed to capture from MEXC on mode switch ({e}) — normal_base unchanged")
        else:
            warnings.append(f"sizing_mode: '{payload['sizing_mode']}' is not NORMAL or COMPOUNDING — unchanged")
    if payload.get("refresh_normal_base"):
        try:
            _capture_normal_base()
        except Exception as e:
            warnings.append(f"refresh_normal_base: failed to fetch balance from MEXC ({e}) — normal_base unchanged")
    for k in ("notional_usd", "sl_atr_mult", "tp_atr_mult", "allocation_pct", "risk_pct", "leverage", "max_live_notional_usd"):
        if k in payload and payload[k] is not None:
            try:
                v = float(payload[k])
            except (TypeError, ValueError):
                warnings.append(f"{k}: '{payload[k]}' is not a number — unchanged")
                continue
            if k == "allocation_pct" and not (0 < v <= 100):
                warnings.append(f"allocation_pct: must be between 0 and 100, got {v} — kept at {CONFIG['allocation_pct']}")
                continue
            if k == "risk_pct" and not (0 < v <= 20):
                warnings.append(f"risk_pct: must be between 0 and 20, got {v} — kept at {CONFIG.get('risk_pct')}")
                continue
            if k == "leverage" and v < MIN_LIVE_LEVERAGE:
                warnings.append(f"leverage: must be at least {MIN_LIVE_LEVERAGE:g}x, got {v} — kept at {CONFIG['leverage']}")
                continue
            if k == "max_live_notional_usd" and v <= 0:
                warnings.append(f"max_live_notional_usd: must be positive, got {v} — kept at {CONFIG['max_live_notional_usd']}")
                continue
            CONFIG[k] = v
    CONFIG["margin_mode"] = "ISOLATED"
    _persist()
    result = status()
    result["warnings"] = warnings
    return result


def _finish_qty(result, raw_qty, vol_unit, min_vol, max_vol, contract_size, price, leverage, max_notional):
    result["raw_quantity"] = raw_qty
    steps = round(raw_qty / vol_unit)
    final_qty = steps * vol_unit
    result["final_quantity"] = final_qty
    position_notional = final_qty * contract_size * price if (final_qty and contract_size and price) else 0.0
    if result.get("calculated_notional") is None:
        result["calculated_notional"] = position_notional
    if position_notional > max_notional and price > 0 and contract_size > 0:
        cap_qty = max_notional / (contract_size * price)
        cap_steps = max(vol_unit, (cap_qty // vol_unit) * vol_unit)
        final_qty = cap_steps
        result["final_quantity"] = final_qty
        result["capped"] = True
        position_notional = final_qty * contract_size * price
    result["final_notional"] = position_notional
    if final_qty <= 0:
        result["error"] = "calculated quantity is zero"
        return result
    if final_qty < min_vol:
        result["error"] = f"calculated quantity {final_qty} is below MEXC minVol {min_vol} for {SYMBOL}"
        return result
    if final_qty > max_vol:
        result["error"] = f"calculated quantity {final_qty} exceeds MEXC maxVol {max_vol} for {SYMBOL}"
        return result
    result["required_margin"] = position_notional / leverage if leverage else position_notional
    return result


def compute_sizing(override_price: Optional[float] = None, entry: Optional[float] = None, stop: Optional[float] = None) -> Dict:
    cfg = CONFIG
    mode = cfg.get("sizing_mode", "NORMAL")
    allocation_pct = float(cfg.get("allocation_pct", 20.0))
    risk_pct = float(cfg.get("risk_pct", 2.0))
    leverage = float(cfg.get("leverage", 10.0))
    max_notional = float(cfg.get("max_live_notional_usd", 1000.0))
    result: Dict = {
        "sizing_mode": mode, "allocation_pct": allocation_pct, "risk_pct": risk_pct,
        "leverage": leverage, "max_live_notional_usd": max_notional, "margin_mode": "ISOLATED",
        "balance_used": None, "normal_base": STATE.get("normal_base"),
        "normal_base_captured_at": STATE.get("normal_base_captured_at"),
        "risk_usd": None, "stop_distance": None, "calculated_capital": None,
        "calculated_notional": None, "final_notional": None, "capped": False,
        "contract_size": None, "price_unit": None, "price_scale": None,
        "min_leverage": None, "max_leverage": None, "price": None,
        "raw_quantity": None, "final_quantity": None, "required_margin": None,
        "as_of": time.time(), "error": None,
    }
    if mode == "COMPOUNDING":
        try:
            balance_used = _fetch_available_balance()
        except Exception as e:
            result["error"] = f"could not fetch MEXC balance for COMPOUNDING sizing: {e}"
            return result
    else:
        if STATE.get("normal_base") is None:
            try:
                _capture_normal_base()
            except Exception as e:
                result["error"] = f"could not capture NORMAL sizing base from MEXC: {e}"
                return result
            result["normal_base"] = STATE.get("normal_base")
            result["normal_base_captured_at"] = STATE.get("normal_base_captured_at")
        balance_used = STATE["normal_base"]
    result["balance_used"] = balance_used
    result["risk_usd"] = balance_used * risk_pct / 100.0
    try:
        from .market_data import mexc_market_data as mkt
        detail = mkt.rest_get(f"/api/v1/contract/detail?symbol={SYMBOL}")
    except Exception as e:
        result["error"] = f"contract detail fetch failed: {e}"
        return result
    if not detail.get("success"):
        result["error"] = f"contract detail not successful for {SYMBOL}"
        return result
    d = detail["data"]
    if isinstance(d, list):
        d = d[0] if d else {}
    contract_size = float(d.get("contractSize") or 0)
    vol_unit = float(d.get("volUnit") or 1)
    min_vol = float(d.get("minVol") or vol_unit)
    max_vol = float(d.get("maxVol") or float("inf"))
    price_unit = float(d.get("priceUnit") or 0)
    price_scale = int(d.get("priceScale") or 2)
    min_leverage = float(d.get("minLeverage") or 1)
    max_leverage = float(d.get("maxLeverage") or 125)
    contract_state = d.get("state")
    api_allowed = d.get("apiAllowed")
    if not contract_size:
        result["error"] = f"contractSize missing/zero for {SYMBOL}"
        return result
    result["contract_size"] = contract_size
    result["price_unit"] = price_unit
    result["price_scale"] = price_scale
    result["min_leverage"] = min_leverage
    result["max_leverage"] = max_leverage
    if contract_state not in (0, None):
        result["error"] = f"{SYMBOL} is not currently tradable on MEXC (state={contract_state})"
        return result
    if api_allowed is False:
        result["error"] = f"{SYMBOL} contract metadata reports apiAllowed=false — API trading may be restricted for this symbol"
        return result
    if not (min_leverage <= leverage <= max_leverage):
        result["error"] = (
            f"leverage {leverage:g}x is outside MEXC's allowed range "
            f"[{min_leverage:g}x, {max_leverage:g}x] for {SYMBOL}"
        )
        return result
    price = override_price
    if price is None:
        try:
            candles = dao.read_closed_candles(cfg.get("timeframe", "15m"), limit=1)
            if candles:
                price = float(candles[-1]["close"])
        except Exception:
            price = None
    result["price"] = price
    if not price:
        result["error"] = "no price available for sizing"
        return result
    if mode == "COMPOUNDING":
        entry_f, stop_f = _hunt_levels(entry, stop)
        if not entry_f or not stop_f:
            result["error"] = "waiting for Hunt stop to size 2% risk"
            return result
        stop_distance = abs(entry_f - stop_f)
        result["stop_distance"] = stop_distance
        if stop_distance <= 0:
            result["error"] = "Hunt stop distance is zero — cannot size risk"
            return result
        risk_usd = result["risk_usd"]
        result["calculated_capital"] = risk_usd
        raw_qty = risk_usd / (stop_distance * contract_size)
        return _finish_qty(result, raw_qty, vol_unit, min_vol, max_vol, contract_size, price, leverage, max_notional)
    capital_margin = balance_used * allocation_pct / 100.0
    position_notional = capital_margin * leverage
    result["calculated_capital"] = capital_margin
    result["calculated_notional"] = position_notional
    final_notional = min(position_notional, max_notional)
    result["capped"] = final_notional < position_notional
    if final_notional <= 0:
        result["error"] = "calculated notional is zero or negative"
        return result
    raw_qty = final_notional / (contract_size * price)
    return _finish_qty(result, raw_qty, vol_unit, min_vol, max_vol, contract_size, price, leverage, max_notional)


def _snap_to_tick(value: float, price_unit: float, price_scale: int) -> float:
    if price_unit and price_unit > 0:
        steps = round(value / price_unit)
        value = steps * price_unit
    return round(value, price_scale)


def _check_available_margin(required_margin: float) -> Dict:
    try:
        avail = _fetch_available_balance()
    except Exception as e:
        return {"ok": False, "reason": f"balance check failed: {e}", "available_balance": None}
    needed = required_margin * (1 + SAFETY_BUFFER_PCT)
    if avail < needed:
        return {
            "ok": False,
            "reason": f"insufficient available balance: need {needed:.4f} USDT (incl. {SAFETY_BUFFER_PCT*100:.0f}% buffer), have {avail:.4f}",
            "available_balance": avail,
        }
    return {"ok": True, "reason": None, "available_balance": avail}


def _log_sizing_attempt(sizing: Dict, result: str, reason: Optional[str] = None,
                         order_result: Optional[Dict] = None,
                         submitted: Optional[Dict] = None) -> None:
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(), "sizing_mode": sizing.get("sizing_mode"),
            "balance_used": sizing.get("balance_used"), "normal_base": sizing.get("normal_base"),
            "risk_pct": sizing.get("risk_pct"), "risk_usd": sizing.get("risk_usd"),
            "stop_distance": sizing.get("stop_distance"), "allocation_pct": sizing.get("allocation_pct"),
            "calculated_capital": sizing.get("calculated_capital"), "leverage": sizing.get("leverage"),
            "calculated_notional": sizing.get("calculated_notional"),
            "max_live_notional_usd": sizing.get("max_live_notional_usd"),
            "final_notional": sizing.get("final_notional"), "capped": sizing.get("capped"),
            "contract_size": sizing.get("contract_size"), "price": sizing.get("price"),
            "raw_quantity": sizing.get("raw_quantity"), "final_quantity": sizing.get("final_quantity"),
            "required_margin": sizing.get("required_margin"), "submitted": submitted,
            "safety_buffer_pct": SAFETY_BUFFER_PCT,
            "available_balance": (sizing.get("_margin_check") or {}).get("available_balance"),
            "result": result, "rejection_reason": reason, "order_result": order_result,
        }
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.exception("failed to write live sizing audit log")


def _open_from_hunt(hunt: Dict, tf: str, thesis: Optional[Dict] = None) -> None:
    side = hunt.get("direction")
    th = thesis or {}
    entry = float(th.get("fill_price") or hunt["entry"])
    sl = float(th.get("stop") or hunt["stop"])
    tp = float(th.get("target") or hunt["target"])
    path = (hunt.get("hunt") or {}).get("m5_path") or hunt.get("v3a_path")
    gate = hunt.get("gate") or ""
    why0 = (hunt.get("why_state") or [""])[0] if isinstance(hunt.get("why_state"), list) else (hunt.get("why_state") or "")
    paper_trading.open_trade(
        symbol=SYMBOL, side=side, entry_price=entry, sl_price=sl, tp_price=tp,
        notional_usd=float((thesis or {}).get("notional") or CONFIG["notional_usd"]),
        timeframe=tf, brain_state=side, consensus=0, confidence=0,
        note=f"Hunt C-FI {gate} {path} {(hunt.get('event') or '')} {why0}",
        source="AUTO", thesis=thesis,
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


def _open_live_from_hunt(hunt: Dict, tf: str, live_price: Optional[float]) -> Dict:
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
    })
    try:
        rows = paper_trading.list_trades(status="OPEN")
        live_rows = [t for t in rows if t.get("source") == "AUTO"]
        if live_rows:
            paper_trading.update_open_fill(live_rows[0]["id"], float(fill))
    except Exception:
        logger.exception("could not persist MEXC fill onto shadow row")
    return order_result


def _in_cooldown(tf_seconds: int) -> bool:
    if STATE["last_close_ts"] is None:
        return False
    bars = CONFIG["cooldown_bars_after_failure"] if STATE["last_close_reason"] in (
        "SL", "BRAIN_EXIT", "FLY", "cancel", "STRUCTURAL_INVALIDATION"
    ) else CONFIG["cooldown_bars_normal"]
    return (time.time() - STATE["last_close_ts"]) < bars * tf_seconds


def _record_close(reason: str) -> None:
    STATE["last_close_ts"] = time.time()
    STATE["last_close_reason"] = reason


def evaluate(live_price: Optional[float], force: bool = False) -> Dict:
    if not CONFIG["enabled"]:
        STATE["last_action"] = "DISABLED"
        return STATE
    try:
        open_before = _open_auto()
        rec = manage_open_on_5m(STATE, open_before)
        if rec:
            STATE["last_lifecycle"] = {k: rec.get(k) for k in ("action", "reason", "exit_kind", "sl")}
            if rec.get("action") == "EXIT":
                _close_live_if_needed(open_before, rec.get("exit_px") or live_price)
                _record_close(rec.get("exit_kind") or "BRAIN_EXIT")
                STATE["last_action"] = f"LIFECYCLE_EXIT {rec.get('exit_kind')}"
                STATE["last_reason"] = rec.get("reason")
            elif rec.get("action") == "TRAIL":
                STATE["last_action"] = "LIFECYCLE_TRAIL"
                STATE["last_reason"] = rec.get("reason")
    except Exception:
        pass
    tf = CONFIG["timeframe"]
    candles = dao.read_closed_candles(tf, limit=ANALYSIS_LOOKBACK)
    if len(candles) < 30:
        return STATE
    c5 = dao.read_closed_candles("5m", limit=6)
    hunt_5m_ts = c5[-1]["ts"] if c5 else None
    last_ts = candles[-1]["ts"]
    if not force and hunt_5m_ts is not None and STATE.get("last_hunt_5m_ts") == hunt_5m_ts:
        STATE["last_candle_ts"] = last_ts
        return STATE
    STATE["last_candle_ts"] = last_ts
    STATE["last_hunt_5m_ts"] = hunt_5m_ts
    STATE["last_eval_at"] = int(time.time())
    result = analysis_service.full_analysis(tf)
    hunt = result.get("hunt") or {}
    weather = result.get("weather") or {}
    STATE["last_hunt"] = {
        "action": hunt.get("action"),
        "version": hunt.get("brain_version") or HUNT_VERSION_C_FI,
        "path": (hunt.get("hunt") or {}).get("m5_path") or hunt.get("v3a_path"),
        "event": hunt.get("event"), "gate": hunt.get("gate"),
        "why": (hunt.get("why_state") or [None])[0],
        "direction": hunt.get("direction"), "entry": hunt.get("entry"),
        "stop": hunt.get("stop"), "target": hunt.get("target"),
    }
    STATE["last_state"] = hunt.get("action")
    STATE["last_reason"] = (hunt.get("why_state") or [""])[0]
    live_mode = CONFIG.get("mode") == "LIVE"
    live_armed = _live_armed()
    open_auto = _open_auto()
    if open_auto is not None:
        STATE["last_action"] = f"HOLD {open_auto.get('side')}"
        return STATE
    if hunt.get("action") != "FIRE":
        STATE["last_action"] = f"NO-TRADE ({hunt.get('action') or 'WAIT'})"
        return STATE
    side = hunt.get("direction")
    flag = weather.get("flag")
    if flag and not side_allowed(flag, side):
        STATE["last_action"] = "WEATHER_BLOCK"
        STATE["last_reason"] = f"{flag} blocks {side}"
        return STATE
    if _in_cooldown(300):
        STATE["last_action"] = "COOLDOWN"
        return STATE
    if live_mode and live_armed:
        try:
            order_result = _open_live_from_hunt(hunt, tf, live_price)
            STATE["last_action"] = f"LIVE OPEN {side} Isolated order {order_result.get('data')}"
        except Exception as e:
            STATE["last_action"] = "LIVE ORDER FAILED"
            STATE["last_reason"] = str(e)
            logger.exception("live order failed")
        return STATE
    if live_mode and not live_armed:
        STATE["last_reason"] = (
            "LIVE toggle is on but MEXC_LIVE_TRADING_ENABLED is not true — paper fill only. "
            "Desktop: tray → Show Data Folder → add that line to .env → Restart Trading Engine."
        )
    _open_from_hunt(hunt, tf)
    STATE["last_action"] = f"OPEN {side} Hunt C-FI {hunt.get('gate') or ''}"
    return STATE
