"""Glue for the running app: an opt-in background loop plus read-only status.  Nothing here can place a real order."""
import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

from . import manager as mgr, store
from .config import SwingConfig
from .llm import OpenAILLM

logger = logging.getLogger(__name__)
_manager: Optional[mgr.SwingManager] = None


def enabled() -> bool:
    return os.environ.get("SWING_AI_ENABLED", "").strip().lower() in ("1", "true", "yes")


def get_manager() -> mgr.SwingManager:
    global _manager
    if _manager is None:
        cfg = SwingConfig()
        _manager = mgr.SwingManager(cfg, llm=OpenAILLM(cfg.model, cfg.llm_timeout_seconds))
    return _manager


def status() -> Dict[str, Any]:
    m = get_manager()
    return {"enabled": enabled(), "mode": "PAPER", "model": m.cfg.model, "state": m.status,
            "active_position": store.active_position(), **mgr.report()}


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
