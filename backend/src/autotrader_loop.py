"""Hunt evaluate loop — same-bar WAIT may flip to FIRE."""
import time
from typing import Dict, Optional

from .config import ANALYSIS_LOOKBACK
from .market_data import data_access as dao
from . import analysis_service
from .brain.lifecycle_tick import manage_open_on_5m
from .brain.weather import side_allowed
from .brain.observation_hunt_c_fi import HUNT_VERSION_C_FI
from .autotrader_state import CONFIG, STATE, logger, _live_armed, _open_auto
from .autotrader_exec import (
    _open_from_hunt, _open_live_from_hunt, _close_live_if_needed,
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


def _evaluate_scenario_entry(tf: str, live_price: Optional[float],
                              live_mode: bool, live_armed: bool) -> Dict:
    """Scenario-engine entry path, parallel to the legacy FIRE block in
    evaluate() below. Only reached when CONFIG["entry_engine"] ==
    "scenario". The shared pre-checks (existing-position lifecycle
    management, open_auto / MEXC-still-open guards) already ran in
    evaluate() before this is called.

    Deliberately does NOT apply the legacy 4h "weather" gate
    (brain.weather.side_allowed) -- that is legacy-specific context
    scenario_engine.py was never validated against this session; adding
    it here would be a new, untested filter bolted onto the tested
    engine, not a safety measure. Everything else that IS a shared
    account-level safety control (cooldown, live/paper gate, MEXC
    margin/position checks inside the existing exec functions) is reused
    unchanged.
    """
    from . import scenario_live_bridge
    from .brain import lifecycle
    from .autotrader_exec import _open_from_hunt, _open_live_from_hunt

    result = scenario_live_bridge.evaluate_scenario(live_price)
    STATE["last_scenario_result"] = result
    action = result.get("action")

    if action != "FIRE":
        STATE["last_action"] = f"SCENARIO {action} ({result.get('reason') or ''})"
        return STATE

    if _in_cooldown(300):
        STATE["last_action"] = "COOLDOWN"
        return STATE

    try:
        pos = lifecycle.position_from_scenario_fire(
            result,
            trade_id=f"scn-{result['thesis_id']}-{result['entry_ts']}",
            equity=CONFIG["notional_usd"],
            risk_pct=CONFIG["risk_pct"] / 100.0,
            sl_atr_mult=CONFIG["sl_atr_mult"],
            tp_atr_mult=CONFIG["tp_atr_mult"],
            opened_ts=result["entry_ts"],
        )
    except Exception as e:
        STATE["last_action"] = "SCENARIO FIRE BUT SIZING FAILED"
        STATE["last_reason"] = str(e)
        logger.exception("scenario position sizing failed")
        return STATE

    side = result["direction"]
    hunt_like = {
        "direction": side, "entry": pos.entry, "stop": pos.sl, "target": pos.tp,
        "event": result.get("origin_event"), "gate": f"scenario/{result.get('scenario')}",
        "why_state": [result.get("reason")],
        "thesis_ts": result.get("origin_ts"), "thesis_level": result.get("origin_level"),
    }
    # S1/S2 + the M5 slot of the M15 candle; every entry goes through C.
    case_label = f"{result.get('setup') or 'S?'} M5#{result.get('m5_slot') or '?'}"
    entry_method = "C"
    scenario_thesis = {
        "thesis_id": result.get("thesis_id"), "case": case_label, "m5_slot": result.get("m5_slot"),
        "entry_method": entry_method, "engine": "scenario",
        # used by rule (b) in evaluate(): S1 entered before its M15 closed
        "setup": result.get("setup"), "origin_ts": result.get("origin_ts"),
        "provisional": bool(result.get("provisional")),
        "m5_event": result.get("m5_event"), "m5_level": result.get("m5_level"),
        "trigger_level": result.get("trigger_level"),
        "c_intended_price": result.get("c_intended_price"), "c_intended_ts": result.get("c_intended_ts"),
        "atr15": result.get("atr15"), "scenario_class": result.get("scenario"),
    }


    STATE["last_scenario_trade_log"] = {
        "thesis_id": result.get("thesis_id"), "direction": side,
        "origin_event": result.get("origin_event"), "origin_level": result.get("origin_level"),
        "origin_ts": result.get("origin_ts"), "m5_confirmation_ts": result.get("m5_confirmation_ts"),
        "m5_slot": result.get("m5_slot"), "case": case_label, "entry_method": entry_method,
        "scenario_class": result.get("scenario"),
        "c_intended_price": result.get("c_intended_price"), "c_intended_ts": result.get("c_intended_ts"),
        "actual_entry_price": pos.entry, "atr15": result.get("atr15"),
        "sl": pos.sl, "tp": pos.tp, "qty": pos.qty, "risk_usd": pos.risk_usd,
        "reason": result.get("reason"), "eval_at": int(time.time()),
    }

    if live_mode and live_armed:
        try:
            t0 = time.time()
            order_result = _open_live_from_hunt(hunt_like, tf, live_price, extra_thesis=scenario_thesis)
            STATE["last_scenario_trade_log"]["execution_delay_s"] = round(time.time() - t0, 3)
            STATE["last_scenario_trade_log"]["order_result"] = order_result
            STATE["last_action"] = f"SCENARIO LIVE OPEN {side} {order_result.get('data')}"
        except Exception as e:
            STATE["last_action"] = "SCENARIO LIVE ORDER FAILED"
            STATE["last_reason"] = str(e)
            logger.exception("scenario live order failed")
        return STATE

    if live_mode and not live_armed:
        STATE["last_reason"] = (
            "LIVE toggle is on but MEXC_LIVE_TRADING_ENABLED is not true — paper fill only."
        )
    _open_from_hunt(hunt_like, tf, thesis=scenario_thesis)
    STATE["last_action"] = f"SCENARIO OPEN {side} {result.get('scenario')}"
    return STATE


def _exit_if_m15_break_failed(open_trade: Optional[Dict], live_price: Optional[float]) -> bool:
    """Rule (b): an S1 trade entered inside its M15 candle is exited if
    that candle CLOSES back inside the breakout level. Checked once per
    trade, as soon as that M15 candle is in storage. Returns True when
    the trade was exited."""
    if not open_trade:
        return False
    from . import paper_trading, scenario_live_bridge
    th = paper_trading._thesis(open_trade) or {}
    if th.get("engine") != "scenario" or th.get("setup") != "S1" or not th.get("provisional"):
        return False
    if th.get("origin_ts") is None or STATE.get("m15_break_checked_trade") == open_trade["id"]:
        return False
    failed = scenario_live_bridge.break_failed(int(th["origin_ts"]), open_trade["side"])
    if failed is None:
        return False  # the M15 candle has not closed / synced yet
    if not failed:
        STATE["m15_break_checked_trade"] = open_trade["id"]
        return False
    from .autotrader_exec import _fresh_price
    px = _fresh_price() or live_price or float(open_trade["entry_price"])
    try:
        flatten_mexc(open_trade["side"], None, px)
    except Exception as e:
        # Keep the shadow open so this retries next tick.
        STATE["last_action"] = "M15 BREAK FAILED -- MEXC EXIT FAILED, RETRYING"
        STATE["last_reason"] = str(e)
        logger.exception("MEXC flatten on failed M15 break failed")
        return True
    paper_trading.close_trade(open_trade["id"], px, "M15_BREAK_FAILED")
    STATE["m15_break_checked_trade"] = open_trade["id"]
    _record_close("M15_BREAK_FAILED")
    STATE["normal_base"] = None
    STATE["normal_base_captured_at"] = None
    STATE["last_action"] = "EXIT M15_BREAK_FAILED"
    STATE["last_reason"] = "S1 entered early, but the M15 candle closed back inside the breakout level"
    return True


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
                # Force a fresh balance capture for the NEXT trade's sizing
                # instead of reusing a now-stale snapshot -- this trade's
                # close just changed the real account balance. Existing
                # "capture if missing" logic in compute_sizing() picks this
                # up automatically; nothing else needs to change.
                STATE["normal_base"] = None
                STATE["normal_base_captured_at"] = None
                STATE["last_action"] = f"LIFECYCLE_EXIT {rec.get('exit_kind')}"
                STATE["last_reason"] = rec.get("reason")
            elif rec.get("action") == "TRAIL":
                STATE["last_action"] = "LIFECYCLE_TRAIL"
                STATE["last_reason"] = rec.get("reason")
    except Exception:
        pass
    try:
        if _exit_if_m15_break_failed(_open_auto(), live_price):
            return STATE
    except Exception:
        logger.exception("M15 failed-break check failed")
    tf = CONFIG["timeframe"]
    candles = dao.read_closed_candles(tf, limit=ANALYSIS_LOOKBACK)
    if len(candles) < 30:
        return STATE
    c5 = dao.read_closed_candles("5m", limit=6)
    hunt_5m_ts = c5[-1]["ts"] if c5 else None
    last_ts = candles[-1]["ts"]
    STATE["last_candle_ts"] = last_ts
    STATE["last_eval_at"] = int(time.time())
    result = analysis_service.full_analysis(tf)
    hunt = result.get("hunt") or {}
    weather = result.get("weather") or {}
    nested = hunt.get("hunt") if isinstance(hunt.get("hunt"), dict) else {}
    STATE["last_hunt"] = {
        "action": hunt.get("action"),
        "version": hunt.get("brain_version") or HUNT_VERSION_C_FI,
        "path": nested.get("m5_path") or hunt.get("v3a_path"),
        "event": hunt.get("event") or nested.get("event"),
        "gate": hunt.get("gate"),
        "why": (hunt.get("why_state") or [None])[0],
        "direction": hunt.get("direction"),
        "entry": hunt.get("entry") or nested.get("level") or nested.get("entry"),
        "stop": hunt.get("stop") or nested.get("stop"),
        "target": hunt.get("target") or nested.get("target"),
        "thesis_ts": hunt.get("thesis_ts"),
        "thesis_level": hunt.get("thesis_level"),
        "thesis_invalid": hunt.get("thesis_invalid"),
        "rearm": hunt.get("rearm"),
    }
    STATE["last_state"] = hunt.get("action")
    STATE["last_reason"] = (hunt.get("why_state") or [""])[0]
    already_opened_this_bar = (
        hunt_5m_ts is not None and STATE.get("last_fired_5m_ts") == hunt_5m_ts
    )
    STATE["last_hunt_5m_ts"] = hunt_5m_ts
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
    if CONFIG.get("entry_engine", "legacy") != "legacy":
        # Scenario-engine entry path. Only the legacy FIRE decision below
        # this point is skipped -- everything above (revive-shadow,
        # existing-position lifecycle management, open_auto / MEXC-still-
        # open guards) already ran unconditionally and applies to BOTH
        # engines equally, so only one can ever open a new trade.
        return _evaluate_scenario_entry(tf, live_price, live_mode, live_armed)
    if hunt.get("action") != "FIRE":
        STATE["last_action"] = f"NO-TRADE ({hunt.get('action') or 'WAIT'})"
        return STATE
    if already_opened_this_bar:
        STATE["last_action"] = "ALREADY FIRED THIS 5M"
        return STATE
    side = hunt.get("direction")
    if not hunt.get("entry"):
        hunt["entry"] = nested.get("level") or nested.get("entry")
    if not hunt.get("stop"):
        hunt["stop"] = nested.get("stop")
    if not hunt.get("target"):
        hunt["target"] = nested.get("target")
    if not hunt.get("entry") or not hunt.get("stop") or not hunt.get("target"):
        STATE["last_action"] = "FIRE BUT NO LEVELS"
        STATE["last_reason"] = "Hunt printed FIRE without entry/stop/target — not sending"
        return STATE
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
            STATE["last_fired_5m_ts"] = hunt_5m_ts
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
    STATE["last_fired_5m_ts"] = hunt_5m_ts
    STATE["last_action"] = f"OPEN {side} Hunt C-FI {hunt.get('gate') or ''}"
    return STATE