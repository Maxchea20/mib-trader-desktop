"""Auto-trade: Hunt C-FI entry on each closed 5m + V1b lifecycle.
Paper always works. LIVE mode places real MEXC orders ONLY if both:
  1) CONFIG["mode"] == "LIVE" (the UI toggle), AND
  2) env var MEXC_LIVE_TRADING_ENABLED=true is set.
The env var is a second, deliberate switch independent of the UI — a stray
click or a UI bug can't send real orders on its own. Both must be true.
One AUTO position at a time. New signal does not override.

POSITION SIZING (NORMAL / COMPOUNDING):
  compute_sizing() is the single authoritative formula. Both the UI preview
  (via status()/GET /autotrade) and the live order path (_open_live_from_hunt)
  call this same function — never two independent calculations.

    NORMAL:      balance_used = STATE["normal_base"]        (fixed for the session — captured
                                                               from MEXC, never user-typed; see
                                                               capture triggers below)
    COMPOUNDING: balance_used = fresh MEXC availableBalance   (fetched live, every call)

    capital_margin    = balance_used * allocation_pct / 100
    position_notional = capital_margin * leverage        <- leverage applied ONCE, here
    final_notional     = min(position_notional, max_live_notional_usd)   <- safety cap
    raw_quantity        = final_notional / (contract_size * price)
    final_quantity      = raw_quantity rounded to MEXC volUnit, validated against minVol/maxVol

  `leverage` is then also passed to MEXC's own order `leverage` field — that
  tells the EXCHANGE how much margin to reserve for the notional above; it
  does not multiply anything a second time on our side.

  Trading logic (Hunt C-FI, weather gate, SL/TP, lifecycle/trailing) is
  UNCHANGED by any of this — sizing only decides how big the order is.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

from .config import SYMBOL, ANALYSIS_LOOKBACK, TF_SECONDS
from .market_data import data_access as dao
from . import analysis_service
from . import paper_trading
from .brain.lifecycle_tick import manage_open_on_5m
from .brain.weather import side_allowed
from .brain.observation_hunt_c_fi import HUNT_VERSION_C_FI

logger = logging.getLogger(__name__)

# Safety-only buffer applied to the pre-submit margin check (NOT a strategy
# parameter — do not expose this as something the trading logic can tune).
SAFETY_BUFFER_PCT = 0.02

AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "live_sizing_log.jsonl"

CONFIG = {
    "enabled": True,
    "mode": "PAPER",
    "timeframe": "15m",
    "hunt_version": HUNT_VERSION_C_FI,
    # notional_usd: kept ONLY for the existing PAPER manual-entry path
    # (paper_trading.open_trade's notional_usd argument). It is NOT used for
    # live sizing anymore — see sizing_mode / compute_sizing() below.
    "notional_usd": 1000.0,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    "cooldown_bars_normal": 1,
    "cooldown_bars_after_failure": 3,
    # --- live position sizing ---
    "sizing_mode": "NORMAL",          # "NORMAL" | "COMPOUNDING"
    "allocation_pct": 10.0,           # % of balance_used -> capital/margin
    "leverage": 10.0,                 # existing preset buttons only: 10/20/50/100
    "max_live_notional_usd": 1000.0,  # configurable safety cap (was a hardcoded 50)
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
    # NORMAL mode's fixed sizing base — NOT a user setting. Captured from
    # MEXC on: (a) lazy first use of NORMAL, (b) a COMPOUNDING->NORMAL
    # transition, (c) the user pressing Refresh. Untouched by account P&L
    # in between. See compute_sizing() / update().
    "normal_base": None,
    "normal_base_captured_at": None,
}


def _open_auto() -> Optional[Dict]:
    for t in paper_trading.list_trades("OPEN"):
        if t.get("source") == "AUTO":
            return t
    return None


def status() -> Dict:
    return {
        "config": CONFIG,
        "state": STATE,
        "open_auto_trade": _open_auto(),
        "sizing_preview": compute_sizing(),
    }


def _fetch_available_balance() -> float:
    """The one place USDT availableBalance is read from MEXC — used by
    COMPOUNDING sizing, NORMAL base capture, and the margin safety check.
    Raises on failure; callers decide how to handle it."""
    from .market_data import mexc_private
    assets = mexc_private.get_assets()
    usdt = next((a for a in assets if a.get("currency") == "USDT"), None)
    if not usdt:
        raise RuntimeError("no USDT asset entry returned by MEXC")
    return float(usdt.get("availableBalance") or 0)


def _capture_normal_base() -> float:
    """Fetches a fresh availableBalance and stores it as the NORMAL session's
    fixed sizing base. Raises on failure (caller surfaces it via
    compute_sizing()'s error field, or update()'s return)."""
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
            # Trigger B: COMPOUNDING -> NORMAL transition re-captures the base.
            if prev == "COMPOUNDING" and sm == "NORMAL":
                try:
                    _capture_normal_base()
                except Exception as e:
                    warnings.append(f"refresh_normal_base: failed to capture from MEXC on mode switch ({e}) — normal_base unchanged")
        else:
            warnings.append(f"sizing_mode: '{payload['sizing_mode']}' is not NORMAL or COMPOUNDING — unchanged")

    # Trigger C: manual Refresh.
    if payload.get("refresh_normal_base"):
        try:
            _capture_normal_base()
        except Exception as e:
            warnings.append(f"refresh_normal_base: failed to fetch balance from MEXC ({e}) — normal_base unchanged")

    for k in ("notional_usd", "sl_atr_mult", "tp_atr_mult", "allocation_pct", "leverage", "max_live_notional_usd"):
        if k in payload and payload[k] is not None:
            try:
                v = float(payload[k])
            except (TypeError, ValueError):
                warnings.append(f"{k}: '{payload[k]}' is not a number — unchanged")
                continue
            if k == "allocation_pct" and not (0 < v <= 100):
                warnings.append(f"allocation_pct: must be between 0 and 100, got {v} — kept at {CONFIG['allocation_pct']}")
                continue
            if k == "leverage" and v <= 0:
                warnings.append(f"leverage: must be positive, got {v} — kept at {CONFIG['leverage']}")
                continue
            if k == "max_live_notional_usd" and v <= 0:
                warnings.append(f"max_live_notional_usd: must be positive, got {v} — kept at {CONFIG['max_live_notional_usd']}")
                continue
            CONFIG[k] = v

    result = status()
    result["warnings"] = warnings
    return result



# ---------------------------------------------------------------------------
# Position sizing — the one authoritative function.
# ---------------------------------------------------------------------------

def compute_sizing(override_price: Optional[float] = None) -> Dict:
    """Returns the full sizing breakdown for the CURRENT CONFIG/STATE. Never
    raises — on any failure (no price yet, MEXC unreachable, bad contract
    data) it returns a dict with a non-None "error" and whatever fields it
    managed to fill in. Callers (both the status()/preview path and the live
    order path) must check result["error"] before trusting final_quantity."""
    cfg = CONFIG
    mode = cfg.get("sizing_mode", "NORMAL")
    allocation_pct = float(cfg.get("allocation_pct", 10.0))
    leverage = float(cfg.get("leverage", 10.0))
    max_notional = float(cfg.get("max_live_notional_usd", 1000.0))

    result: Dict = {
        "sizing_mode": mode,
        "allocation_pct": allocation_pct,
        "leverage": leverage,
        "max_live_notional_usd": max_notional,
        "balance_used": None,
        "normal_base": STATE.get("normal_base"),
        "normal_base_captured_at": STATE.get("normal_base_captured_at"),
        "calculated_capital": None,
        "calculated_notional": None,
        "final_notional": None,
        "capped": False,
        "contract_size": None,
        "price_unit": None,
        "price_scale": None,
        "min_leverage": None,
        "max_leverage": None,
        "price": None,
        "raw_quantity": None,
        "final_quantity": None,
        "required_margin": None,
        "as_of": time.time(),
        "error": None,
    }

    # --- balance_used: the one place sizing_mode actually branches ---
    if mode == "COMPOUNDING":
        try:
            balance_used = _fetch_available_balance()
        except Exception as e:
            result["error"] = f"could not fetch MEXC balance for COMPOUNDING sizing: {e}"
            return result
    else:
        # Trigger A: lazy first-use capture — NORMAL active, no base yet.
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


    # --- leverage applied EXACTLY ONCE, right here ---
    capital_margin = balance_used * allocation_pct / 100.0
    position_notional = capital_margin * leverage
    result["calculated_capital"] = capital_margin
    result["calculated_notional"] = position_notional

    final_notional = min(position_notional, max_notional)
    result["final_notional"] = final_notional
    result["capped"] = final_notional < position_notional

    if final_notional <= 0:
        result["error"] = "calculated notional is zero or negative"
        return result

    # --- contract detail (public endpoint, no auth needed) ---
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

    # state==0 is "enabled" per MEXC docs; missing field (None) is treated as
    # unknown, not blocked — only an EXPLICIT non-zero state stops sizing.
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

    # --- price ---
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

    # --- quantity ---
    raw_qty = final_notional / (contract_size * price)
    result["raw_quantity"] = raw_qty

    steps = round(raw_qty / vol_unit)
    final_qty = steps * vol_unit
    result["final_quantity"] = final_qty

    if final_qty < min_vol:
        result["error"] = f"calculated quantity {final_qty} is below MEXC minVol {min_vol} for {SYMBOL}"
        return result
    if final_qty > max_vol:
        result["error"] = f"calculated quantity {final_qty} exceeds MEXC maxVol {max_vol} for {SYMBOL}"
        return result

    result["required_margin"] = final_notional / leverage if leverage else final_notional
    return result


def _snap_to_tick(value: float, price_unit: float, price_scale: int) -> float:
    """Rounds a price to the nearest valid MEXC tick (a multiple of
    price_unit) then to price_scale decimal places. MEXC rejects (error
    2007/2015) any price/SL/TP that doesn't land exactly on a valid tick —
    this is applied to every price field right before submission."""
    if price_unit and price_unit > 0:
        steps = round(value / price_unit)
        value = steps * price_unit
    return round(value, price_scale)


def _check_available_margin(required_margin: float) -> Dict:
    """Fresh balance check run immediately before EVERY live submit, regardless
    of sizing_mode — independent of compute_sizing()'s balance_used (the sizing
    INPUT); this is the final go/no-go GATE. Includes the 2% safety buffer
    (fees/slippage headroom, not a strategy parameter)."""
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
    """Audit trail for every live sizing attempt — computed, rejected, or submitted."""
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "sizing_mode": sizing.get("sizing_mode"),
            "balance_used": sizing.get("balance_used"),
            "normal_base": sizing.get("normal_base"),
            "allocation_pct": sizing.get("allocation_pct"),
            "calculated_capital": sizing.get("calculated_capital"),
            "leverage": sizing.get("leverage"),
            "calculated_notional": sizing.get("calculated_notional"),
            "max_live_notional_usd": sizing.get("max_live_notional_usd"),
            "final_notional": sizing.get("final_notional"),
            "capped": sizing.get("capped"),
            "contract_size": sizing.get("contract_size"),
            "price": sizing.get("price"),
            "raw_quantity": sizing.get("raw_quantity"),
            "final_quantity": sizing.get("final_quantity"),
            "required_margin": sizing.get("required_margin"),
            "min_leverage": sizing.get("min_leverage"),
            "max_leverage": sizing.get("max_leverage"),
            "price_unit": sizing.get("price_unit"),
            "price_scale": sizing.get("price_scale"),
            "submitted": submitted,
            "safety_buffer_pct": SAFETY_BUFFER_PCT,
            "available_balance": (sizing.get("_margin_check") or {}).get("available_balance"),
            "result": result,
            "rejection_reason": reason,
            "order_result": order_result,
        }
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.exception("failed to write live sizing audit log")


def _open_from_hunt(hunt: Dict, tf: str) -> None:
    """PAPER path — unchanged, still uses CONFIG['notional_usd'] directly."""
    side = hunt.get("direction")
    entry = float(hunt["entry"])
    sl = float(hunt["stop"])
    tp = float(hunt["target"])
    path = (hunt.get("hunt") or {}).get("m5_path") or hunt.get("v3a_path")
    gate = hunt.get("gate") or ""
    paper_trading.open_trade(
        symbol=SYMBOL, side=side, entry_price=entry, sl_price=sl, tp_price=tp,
        notional_usd=CONFIG["notional_usd"], timeframe=tf,
        brain_state=side, consensus=0, confidence=0,
        note=f"Hunt C-FI {gate} {path} {(hunt.get('event') or '')} {(hunt.get('why_state') or [''])[0]}",
        source="AUTO",
    )


def _fresh_price() -> Optional[float]:
    """Fetches ONE current ticker price directly from MEXC, right now — not
    the polling loop's cached live_price, which can be seconds to tens of
    seconds stale by the time an order actually reaches submit_order(). A
    market order priced outside MEXC's slippage band gets rejected, so this
    is the fix for that specific failure mode. Returns None on any failure —
    caller falls back to the cached price rather than blocking entirely."""
    try:
        from .market_data import mexc_market_data as mkt
        ticker = mkt.rest_get(f"/api/v1/contract/ticker?symbol={SYMBOL}")
        if ticker.get("success"):
            return float(ticker["data"]["lastPrice"])
    except Exception:
        logger.exception("fresh ticker fetch failed, falling back to cached price")
    return None


def _open_live_from_hunt(hunt: Dict, tf: str, live_price: Optional[float]) -> Dict:
    """LIVE path. Sizing comes entirely from compute_sizing() — the same
    function the UI preview uses — so what's shown and what fires can never
    diverge. Raises on any failure; caller (evaluate()) logs STATE and returns."""
    side = hunt.get("direction")
    entry = float(hunt["entry"])
    sl = float(hunt["stop"])
    tp = float(hunt["target"])

    # Fresh price fetched right here, immediately before sizing AND
    # submission both use it — so there is no gap between "what we sized
    # the order against" and "what we actually submit". Falls back to the
    # loop's cached live_price only if this fetch itself fails.
    price = _fresh_price() or float(live_price or entry)

    sizing = compute_sizing(override_price=price)

    if sizing.get("error"):
        _log_sizing_attempt(sizing, result="REJECTED", reason=sizing["error"])
        raise RuntimeError(f"sizing failed: {sizing['error']}")

    margin_check = _check_available_margin(sizing["required_margin"])
    sizing["_margin_check"] = margin_check
    if not margin_check["ok"]:
        _log_sizing_attempt(sizing, result="REJECTED", reason=margin_check["reason"])
        raise RuntimeError(margin_check["reason"])

    # Every price field MEXC sees must land on a valid tick, or the order
    # gets rejected (error 2007/2015) regardless of everything else above
    # being correct.
    price_unit = sizing.get("price_unit") or 0
    price_scale = sizing.get("price_scale") if sizing.get("price_scale") is not None else 2
    submit_price = _snap_to_tick(price, price_unit, price_scale)
    submit_sl = _snap_to_tick(sl, price_unit, price_scale)
    submit_tp = _snap_to_tick(tp, price_unit, price_scale)

    from .market_data import mexc_private
    vol = sizing["final_quantity"]
    mexc_side = mexc_private.SIDE_OPEN_LONG if side == "LONG" else mexc_private.SIDE_OPEN_SHORT
    order_result = mexc_private.submit_order(
        symbol=SYMBOL,
        side=mexc_side,
        vol=vol,
        price=submit_price,
        order_type=mexc_private.ORDER_TYPE_MARKET,
        open_type=mexc_private.OPEN_TYPE_CROSS,
        leverage=int(sizing["leverage"]),
        stop_loss_price=submit_sl,
        take_profit_price=submit_tp,
        external_oid=f"mib{int(time.time() * 1000)}",
    )
    _log_sizing_attempt(
        sizing, result="SUBMITTED", order_result=order_result,
        submitted={"price": submit_price, "stop_loss_price": submit_sl, "take_profit_price": submit_tp,
                   "vol": vol, "leverage": int(sizing["leverage"])},
    )
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
        rec = manage_open_on_5m(STATE, _open_auto())
        if rec:
            STATE["last_lifecycle"] = {k: rec.get(k) for k in ("action", "reason", "exit_kind", "sl")}
            if rec.get("action") == "EXIT":
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
    if (
        not force
        and hunt_5m_ts is not None
        and STATE.get("last_hunt_5m_ts") == hunt_5m_ts
    ):
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
        "event": hunt.get("event"),
        "gate": hunt.get("gate"),
        "why": (hunt.get("why_state") or [None])[0],
    }
    STATE["last_state"] = hunt.get("action")
    STATE["last_reason"] = (hunt.get("why_state") or [""])[0]

    live_mode = CONFIG.get("mode") == "LIVE"
    live_armed = os.environ.get("MEXC_LIVE_TRADING_ENABLED", "").lower() == "true"

    if live_mode and not live_armed:
        STATE["last_action"] = "LIVE MODE - NO ORDER"
        STATE["last_reason"] = "MEXC_LIVE_TRADING_ENABLED is not set to true — set it deliberately when ready"
        return STATE

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

    tf_seconds = 300
    if _in_cooldown(tf_seconds):
        STATE["last_action"] = "COOLDOWN"
        return STATE

    if live_mode and live_armed:
        try:
            order_result = _open_live_from_hunt(hunt, tf, live_price)
            STATE["last_action"] = f"LIVE OPEN {side} order {order_result.get('data')}"
        except Exception as e:
            STATE["last_action"] = "LIVE ORDER FAILED"
            STATE["last_reason"] = str(e)
            logger.exception("live order failed")
        return STATE

    _open_from_hunt(hunt, tf)
    STATE["last_action"] = f"OPEN {side} Hunt C-FI {hunt.get('gate') or ''}"
    return STATE