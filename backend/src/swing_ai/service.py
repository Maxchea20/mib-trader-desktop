"""Glue for the running app: an opt-in background loop plus read-only views.  Nothing here can place a real order,
and nothing here produces market analysis: every analysis field shown in the UI is read from a stored GPT response."""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from . import manager as mgr, store
from .config import SwingConfig
from .llm import OpenAILLM

logger = logging.getLogger(__name__)
_manager: Optional[mgr.SwingManager] = None

AI_FIELDS = ("ts", "kind", "wake_kind", "wake_detail", "price", "model", "prompt_version", "decision", "confidence", "market_state",
             "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis", "structure_analysis", "entry_analysis", "entry_type",
             "entry", "sl", "tp", "thesis", "invalidation", "invalidation_price", "wake_levels", "risk_ok", "risk_reasons",
             "snapshot_id", "trade_id", "error", "id")


def enabled() -> bool:
    return os.environ.get("SWING_AI_ENABLED", "").strip().lower() in ("1", "true", "yes")


def get_manager() -> mgr.SwingManager:
    global _manager
    if _manager is None:
        cfg = SwingConfig()
        _manager = mgr.SwingManager(cfg, llm=OpenAILLM(cfg.model, cfg.llm_timeout_seconds))
    return _manager


def _view(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = {k: row.get(k) for k in AI_FIELDS}
    try:
        out["wake_levels"] = json.loads(row["wake_levels"]) if row.get("wake_levels") else []
    except Exception:
        out["wake_levels"] = []
    return out


def latest() -> Dict[str, Any]:
    """Everything the Swing AI panel shows.  Analysis text comes ONLY from stored GPT responses."""
    m = get_manager()
    trade = store.active_trade()
    mgmt = None
    if trade:
        rows = [d for d in store.decisions(50) if d["kind"] == "MANAGE" and d["trade_id"] == trade["id"] and d["valid"]]
        mgmt = _view(rows[0]) if rows else None
    return {"enabled": enabled(), "mode": "PAPER", "symbol": "BTC/USDT", "model": m.cfg.model, "state": m.status,
            "last_analysis": _view(store.latest_entry_decision()), "latest_management": mgmt, "active_trade": trade,
            "analytics": mgr.report()}


def status() -> Dict[str, Any]:
    m = get_manager()
    return {"enabled": enabled(), "mode": "PAPER", "model": m.cfg.model, "state": m.status,
            "active_trade": store.active_trade(), **mgr.report()}


async def loop() -> None:
    """Started from the app's startup hook only when SWING_AI_ENABLED=1."""
    from ..market_data import manager as md
    await asyncio.sleep(15)
    m = get_manager()
    while True:
        try:
            st = md.STATE
            q = dict(st.get("last_ticker") or {})
            q["age_seconds"] = (int(time.time()) - st["last_tick_ts"]) if st.get("last_tick_ts") else None
            await asyncio.to_thread(m.step, time.time(), st.get("last_price"), q, bool(st.get("connected")))
        except Exception:
            logger.exception("swing_ai step failed")
        await asyncio.sleep(5)
