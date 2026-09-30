"""Glue for the running app: an opt-in background loop plus read-only views.  Nothing here can place a real order,
and nothing here produces market analysis: every analysis field shown in the UI is read from a stored GPT response."""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from . import manager as mgr, settings as sett, store
from .config import SwingConfig
from .llm import OpenAILLM
from .schema import AI_EXIT_REASONS

logger = logging.getLogger(__name__)
_manager: Optional[mgr.SwingManager] = None

AI_FIELDS = ("ts", "kind", "wake_kind", "wake_detail", "price", "model", "prompt_version", "decision", "confidence", "headline", "market_state",
             "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis", "structure_analysis", "entry_analysis", "entry_type",
             "entry", "sl", "tp", "expected_hold_hours", "thesis", "invalidation", "invalidation_price", "wake_levels", "risk_ok", "risk_reasons",
             "snapshot_id", "trade_id", "error", "id")


def key_tail() -> Optional[str]:
    """Last 4 characters of the OpenAI key this process is using (never the key), to spot a wrong-key mix-up."""
    k = os.environ.get("OPENAI_API_KEY") or ""
    return ("..." + k[-4:]) if k else None


def enabled() -> bool:
    return bool(sett.load()["enabled"])


def settings_view() -> Dict[str, Any]:
    s = sett.load()
    return {**s, "effective_mode": sett.effective_mode(s), "live_execution_implemented": sett.LIVE_EXECUTION_IMPLEMENTED,
            "env_live_armed": os.environ.get("MEXC_LIVE_TRADING_ENABLED", "").lower() == "true", "bounds": sett.BOUNDS}


def update_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Saves and applies to the running engine.  Raises ValueError on a bad value."""
    sett.save(payload)
    m = get_manager()
    sett.apply_to_config(m.cfg)
    if hasattr(m.llm, "model"):
        m.llm.model = m.cfg.model
    return settings_view()


def get_manager() -> mgr.SwingManager:
    global _manager
    if _manager is None:
        cfg = SwingConfig()
        sett.apply_to_config(cfg)
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
    return {"enabled": enabled(), "mode": sett.effective_mode(), "settings": settings_view(), "symbol": "BTC/USDT", "model": m.cfg.model,
            "openai_key_tail": key_tail(), "state": m.status,
            "last_analysis": _view(store.latest_entry_decision()), "last_review": _view(store.latest_decision()),
            "latest_management": mgmt, "active_trade": trade,
            "analytics": mgr.report()}


_models_cache: Dict[str, Any] = {"at": 0.0, "data": None}
_SKIP = ("audio", "realtime", "image", "tts", "transcribe", "search", "embedding", "instruct", "moderation", "whisper", "dall", "codex")


def accessible_models(force: bool = False) -> Dict[str, Any]:
    """Chat models the CURRENT OpenAI key/project can use (cached 10 minutes).  Never returns the key."""
    now = time.time()
    if not force and _models_cache["data"] is not None and now - _models_cache["at"] < 600:
        return _models_cache["data"]
    out: Dict[str, Any] = {"models": [], "error": None, "key_tail": key_tail()}
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        out["error"] = "OPENAI_API_KEY not set"
    else:
        try:
            from openai import OpenAI
            ids = sorted({m.id for m in OpenAI(api_key=key, timeout=30).models.list()})
            out["models"] = [i for i in ids if (i.startswith("gpt-") or i.startswith("o")) and not any(x in i for x in _SKIP)]
        except Exception as e:
            out["error"] = str(e)[:300]
    _models_cache.update({"at": now, "data": out})
    return out


def trades_view(limit: int = 200) -> list:
    """Paper trades with GPT's own words attached: why it entered, and what it said when it managed or exited (all stored text)."""
    rows = store.trades(None, limit)
    decs = store.decisions(5000)
    by_id = {d["id"]: d for d in decs}
    manage: Dict[int, list] = {}
    for d in decs:                                              # newest first
        if d["kind"] == "MANAGE" and d.get("trade_id") and d["valid"]:
            manage.setdefault(d["trade_id"], []).append(d)
    out = []
    for t in rows:
        e = by_id.get(t.get("decision_id")) or {}
        ms = manage.get(t["id"], [])
        last = ms[0] if ms else None
        end = t.get("closed_ts")
        start = t.get("opened_ts") or t.get("created_ts")
        out.append({**t, "entry_headline": e.get("headline"), "entry_thesis": e.get("thesis"),
                    "exit_note": last["thesis"] if last else None,
                    "exit_wake": (f"{last['wake_kind']}: {last['wake_detail']}" if last and last.get("wake_detail") else (last or {}).get("wake_kind")),
                    "management_reviews": len(ms),
                    "if_held": ({"status": t.get("cf_status"), "r_net": t.get("cf_r_net"), "ended_by": t.get("cf_exit_reason")}
                                if t.get("exit_reason") in AI_EXIT_REASONS else None),
                    "held_minutes": round((end - start) / 60, 1) if end and start else None})
    return out


def status() -> Dict[str, Any]:
    m = get_manager()
    return {"enabled": enabled(), "mode": sett.effective_mode(), "settings": settings_view(), "model": m.cfg.model,
            "openai_key_tail": key_tail(), "state": m.status,
            "active_trade": store.active_trade(), **mgr.report()}


def run_once(m: mgr.SwingManager, st: Dict[str, Any]) -> Dict[str, Any]:
    """One engine tick from the live market state.  The AI is called only when enabled in the settings."""
    q = dict(st.get("last_ticker") or {})
    q["age_seconds"] = (int(time.time()) - st["last_tick_ts"]) if st.get("last_tick_ts") else None
    return m.step(time.time(), st.get("last_price"), q, bool(st.get("connected")), ai_enabled=enabled())


async def loop() -> None:
    """Always running (cheap); the AI only acts while Swing AI is switched ON in the panel."""
    from ..market_data import manager as md
    await asyncio.sleep(15)
    m = get_manager()
    while True:
        try:
            await asyncio.to_thread(run_once, m, md.STATE)
        except Exception:
            logger.exception("swing_ai step failed")
        await asyncio.sleep(5)
