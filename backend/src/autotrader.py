"""Auto-trade: Hunt entry on 15m + V1b lifecycle on each closed 5m.
Paper only. LIVE mode still does not send exchange orders.
"""
import time
from typing import Dict, Optional

from .config import SYMBOL, ANALYSIS_LOOKBACK, TF_SECONDS
from .market_data import data_access as dao
from . import analysis_service
from . import paper_trading
from .brain.lifecycle_tick import manage_open_on_5m
from .brain.weather import side_allowed

CONFIG = {
    "enabled": True,
    "mode": "PAPER",
    "timeframe": "15m",
    "notional_usd": 1000.0,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    "cooldown_bars_normal": 1,
    "cooldown_bars_after_failure": 3,
}

STATE = {
    "last_candle_ts": None,
    "last_state": None,
    "last_action": None,
    "last_reason": None,
    "last_eval_at": None,
    "last_close_ts": None,
    "last_close_reason": None,
    "last_5m_ts": None,
    "last_lifecycle": None,
    "last_hunt": None,
}


def _open_auto() -> Optional[Dict]:
    for t in paper_trading.list_trades("OPEN"):
        if t.get("source") == "AUTO":
            return t
    return None


def status() -> Dict:
    return {"config": CONFIG, "state": STATE, "open_auto_trade": _open_auto()}


def update(payload: Dict) -> Dict:
    if "enabled" in payload:
        CONFIG["enabled"] = bool(payload["enabled"])
    if "mode" in payload:
        mode = str(payload["mode"]).upper()
        if mode in ("PAPER", "LIVE"):
            CONFIG["mode"] = mode
    if payload.get("timeframe"):
        CONFIG["timeframe"] = str(payload["timeframe"])
    for k in ("notional_usd", "sl_atr_mult", "tp_atr_mult"):
        if k in payload and payload[k] is not None:
            try:
                CONFIG[k] = float(payload[k])
            except (TypeError, ValueError):
                pass
    return status()


def _open_from_hunt(hunt: Dict, tf: str) -> None:
    side = hunt.get("direction")
    entry = float(hunt["entry"])
    sl = float(hunt["stop"])
    tp = float(hunt["target"])
    paper_trading.open_trade(
        symbol=SYMBOL, side=side, entry_price=entry, sl_price=sl, tp_price=tp,
        notional_usd=CONFIG["notional_usd"], timeframe=tf,
        brain_state=side, consensus=0, confidence=0,
        note=f"hunt {hunt.get('hunt', {}).get('m5_path')} {(hunt.get('why_state') or [''])[0]}",
        source="AUTO",
    )


def _in_cooldown(tf_seconds: int) -> bool:
    if STATE["last_close_ts"] is None:
        return False
    bars = CONFIG["cooldown_bars_after_failure"] if STATE["last_close_reason"] in (
        "SL", "BRAIN_EXIT", "FLY", "cancel"
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
    last_ts = candles[-1]["ts"]
    if not force and STATE["last_candle_ts"] == last_ts:
        return STATE
    STATE["last_candle_ts"] = last_ts
    STATE["last_eval_at"] = int(time.time())

    result = analysis_service.full_analysis(tf)
    hunt = result.get("hunt") or {}
    weather = result.get("weather") or {}
    STATE["last_hunt"] = {
        "action": hunt.get("action"),
        "path": (hunt.get("hunt") or {}).get("m5_path"),
        "why": (hunt.get("why_state") or [None])[0],
    }
    STATE["last_state"] = hunt.get("action")
    STATE["last_reason"] = (hunt.get("why_state") or [""])[0]

    if CONFIG.get("mode") == "LIVE":
        STATE["last_action"] = "LIVE MODE - NO ORDER"
        STATE["last_reason"] = "Live execution is not enabled"
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

    tf_seconds = TF_SECONDS.get(tf, 900)
    if _in_cooldown(tf_seconds):
        STATE["last_action"] = "COOLDOWN"
        return STATE

    _open_from_hunt(hunt, tf)
    STATE["last_action"] = f"OPEN {side} hunt"
    return STATE
