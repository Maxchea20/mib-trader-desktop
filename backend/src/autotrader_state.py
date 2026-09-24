"""Shared Hunt autotrader CONFIG / STATE."""
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

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
    # Which engine's entry decision is authoritative for opening NEW
    # trades. "legacy" (default, unchanged behavior) = Hunt C-FI, as
    # today. "scenario" = scenario_engine.py + Case-1 C entry timing via
    # scenario_live_bridge.py. Only one can ever open a new trade at a
    # time -- see autotrader_loop.py::evaluate(). Switching this does
    # NOT affect management of a trade already open; that always goes
    # through the existing, unchanged brain/lifecycle_tick.py regardless
    # of which engine opened it.
    "entry_engine": "scenario",
    # S1/S2 timing is fixed by design in scenario_live_bridge.py: S1's
    # 5M BOS/CHoCH and M1 trigger must both fall in the FIRST 5 minutes
    # of the breakout M15 candle; S2 has no slot limit.
}

STATE = {
    "last_candle_ts": None,
    "last_hunt_5m_ts": None,
    "last_fired_5m_ts": None,
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
    from . import paper_trading
    for t in paper_trading.list_trades("OPEN"):
        if t.get("source") == "AUTO":
            return t
    return None