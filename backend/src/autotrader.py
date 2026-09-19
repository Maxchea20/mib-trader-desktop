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
