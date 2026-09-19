"""Sizing + account helpers for Hunt autotrader."""
import json
import time
from typing import Dict, List, Optional

from .config import SYMBOL
from .market_data import data_access as dao
from .autotrader_state import (
    CONFIG, STATE, SAFETY_BUFFER_PCT, MIN_LIVE_LEVERAGE, AUDIT_LOG_PATH,
    _persist, _live_armed, _open_auto, logger,
)


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
