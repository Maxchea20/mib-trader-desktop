"""S1/S2 evaluate loop — same-bar WAIT may flip to FIRE."""
import time
from typing import Dict, Optional

from .config import ANALYSIS_LOOKBACK
from .market_data import data_access as dao
from . import analysis_service
from .brain.lifecycle_tick import manage_open_on_5m
from .brain.weather import side_allowed
from .brain.s1_engine import S1_VERSION
from .autotrader_state import CONFIG, STATE, logger, _live_armed, _open_auto
from .autotrader_exec import (
    _open_from_s1, _open_live_from_s1, _close_live_if_needed,
)
from .autotrader_live_sync import revive_shadow_if_mexc_open, flatten_mexc


def _in_cooldown(tf_seconds: int) -> bool:
    if STATE["last_close_ts"] is None:
        return False
    fail = ("SL", "BRAIN_EXIT", "FLY", "cancel", "HARD_SL")
    if STATE["last_close_reason"] not in fail:
        return False
    bars = CONFIG["cooldown_bars_after_failure"]
    return (time.time() - STATE["last_close_ts"]) < bars * tf_seconds


def _record_close(reason: str) -> None:
    STATE["last_close_ts"] = time.time()
    STATE["last_close_reason"] = reason


def evaluate(live_price: Optional[float], force: bool = False) -> Dict:
    if not CONFIG["enabled"]:
        STATE["last_action"] = "DISABLED"
        return STATE
    try:
        revived = revive_shadow_if_mexc_open()
        if revived:
            STATE["last_action"] = f"HOLD {revived.get('side')} (MEXC still open)"
    except Exception:
        logger.exception("revive shadow failed")
    try:
        open_before = _open_auto()
        rec = manage_open_on_5m(STATE, open_before)
        if rec:
            STATE["last_lifecycle"] = {k: rec.get(k) for k in ("action", "reason", "exit_kind", "sl")}
            if rec.get("action") == "EXIT":
                try:
                    flatten_mexc(open_before.get("side") if open_before else None,
                                 None, rec.get("exit_px") or live_price)
                except Exception:
                    logger.exception("MEXC flatten on lifecycle EXIT failed")
                _close_live_if_needed(open_before, rec.get("exit_px") or live_price)
                _record_close(rec.get("exit_kind") or "BRAIN_EXIT")
                STATE["normal_base"] = None
                STATE["normal_base_captured_at"] = None
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
    s1_5m_ts = c5[-1]["ts"] if c5 else None
    last_ts = candles[-1]["ts"]
    STATE["last_candle_ts"] = last_ts
    STATE["last_eval_at"] = int(time.time())
    result = analysis_service.full_analysis(tf)
    s1 = result.get("s1") or {}
    weather = result.get("weather") or {}
    STATE["last_s1"] = {
        "action": s1.get("action"),
        "version": s1.get("brain_version") or S1_VERSION,
        "path": s1.get("timing"),
        "event": s1.get("event"),
        "timing": s1.get("timing"),
        "timing_state": s1.get("timing_state"),
        "why": (s1.get("why_state") or [None])[0],
        "direction": s1.get("direction"),
        "entry": s1.get("entry"),
        "stop": s1.get("stop"),
        "target": s1.get("target"),
        "thesis_ts": s1.get("thesis_ts"),
        "thesis_level": s1.get("thesis_level"),
        "thesis_invalid": s1.get("thesis_invalid"),
        "slot": s1.get("slot"),
    }
    STATE["last_state"] = s1.get("action")
    STATE["last_reason"] = (s1.get("why_state") or [""])[0]
    already_opened_this_bar = (
        s1_5m_ts is not None and STATE.get("last_fired_5m_ts") == s1_5m_ts
    )
    STATE["last_s1_5m_ts"] = s1_5m_ts
    live_mode = CONFIG.get("mode") == "LIVE"
    live_armed = _live_armed()
    open_auto = _open_auto()
    if open_auto is not None:
        STATE["last_action"] = f"HOLD {open_auto.get('side')}"
        return STATE
    try:
        from .autotrader_exec import _mexc_open_position_vol
        if _mexc_open_position_vol() > 0:
            STATE["last_action"] = "HOLD MEXC Isolated still open"
            return STATE
    except Exception:
        pass
    if s1.get("action") != "FIRE":
        STATE["last_action"] = f"NO-TRADE ({s1.get('action') or 'WAIT'})"
        return STATE
    if already_opened_this_bar:
        STATE["last_action"] = "ALREADY FIRED THIS 5M"
        return STATE
    side = s1.get("direction")
    if not s1.get("entry") or not s1.get("stop") or not s1.get("target"):
        STATE["last_action"] = "FIRE BUT NO LEVELS"
        STATE["last_reason"] = "S1 printed FIRE without entry/stop/target — not sending"
        return STATE
    flag = weather.get("flag")
    if flag and not side_allowed(flag, side):
        STATE["last_action"] = "WEATHER_BLOCK"
        STATE["last_reason"] = f"{flag} blocks {side}"
        return STATE
    if _in_cooldown(300):
        STATE["last_action"] = "COOLDOWN"
        return STATE
    tag = s1.get("timing") or ""
    if live_mode and live_armed:
        try:
            order_result = _open_live_from_s1(s1, tf, live_price)
            STATE["last_fired_5m_ts"] = s1_5m_ts
            STATE["last_action"] = f"LIVE OPEN {side} Isolated order {order_result.get('data')} {tag}"
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
    _open_from_s1(s1, tf)
    STATE["last_fired_5m_ts"] = s1_5m_ts
    STATE["last_action"] = f"OPEN {side} S1/S2 {tag}"
    return STATE
