"""AI Thesis — read-only OpenAI observer of Hunt / Scenario state.

HARD BOUNDARY: no write path. Never calls paper_trading.open_trade/close_trade,
never calls autotrader.update(), never touches MEXC order submission.

Scheduled review every ~900s plus immediate wake on detector events.
"""
import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

from .config import SYMBOL
from .market_data import data_access as dao
from . import analysis_service
from . import autotrader

logger = logging.getLogger(__name__)

CONFIG = {
    "enabled": True,
    "interval_seconds": 900,
    "model": os.environ.get("AI_THESIS_MODEL", "gpt-5.4-mini"),
}

STATE = {
    "thesis": None,
    "aligned_with_brain": None,
    "generated_at": None,
    "processing": False,
    "error": None,
    "last_event": None,
    "last_fingerprint": None,
    "pending_event": None,
}

_PROMPT_INSTRUCTIONS = """You are a market-analysis assistant embedded in a trading terminal. You are \
shown structured BTC_USDT market state and the terminal's automated Brain (Hunt / Scenario). \
Your job is ONLY to observe and explain — you do not trade, you have no ability to place or affect \
any order, and nothing you say is executed automatically.

Form your OWN independent read first from the candles and structure. THEN compare it to the Brain. \
State plainly whether you agree or disagree. Do not assume the Brain is right by default.

If an EVENT woke you, explain: what changed, why it matters, which timeframe is the evidence, \
whether the setup got stronger/weaker/unchanged, whether the thesis is still valid, and what would \
invalidate it. If this is a scheduled REVIEW_15M and nothing material changed versus the previous \
thesis, say that in one sentence — do not repeat the same speech.

Write for someone who is NOT a trader — plain English, 3-6 sentences.

Respond in this format:
ALIGNED: yes|no
STRENGTH: stronger|weaker|unchanged
VALID: yes|no
INVALIDATE_IF: <one short clause>
THESIS: <plain-English read>"""


def _fmt_bar(c: Dict) -> str:
    return (
        f"O:{float(c.get('open', 0)):.1f} "
        f"H:{float(c.get('high', 0)):.1f} "
        f"L:{float(c.get('low', 0)):.1f} "
        f"C:{float(c.get('close', 0)):.1f} "
        f"V:{float(c.get('volume', 0)):.1f}"
    )


def _recent_ohlcv_summary(candles: List[Dict], n: int = 8) -> str:
    tail = candles[-n:] if len(candles) > n else candles
    if not tail:
        return "none"
    return " | ".join(_fmt_bar(c) for c in tail)


def _recent_candles_summary(candles: List[Dict], n: int = 30) -> str:
    tail = candles[-n:] if len(candles) > n else candles
    return ", ".join(f"{c['close']:.1f}" for c in tail)


def _levels_summary(agents: List[Dict], agent_name: str) -> str:
    for a in agents or []:
        if a.get("agent") == agent_name:
            levels = a.get("key_levels") or []
            if not levels:
                return "none"
            parts = []
            for lv in levels[:6]:
                label = lv.get("label") or "level"
                price = lv.get("price")
                parts.append(f"{label} @ {price}")
            return "; ".join(parts)
    return "none"


def _status_strip_summary(agents: List[Dict]) -> str:
    if not agents:
        return "none"
    parts = []
    for a in agents:
        name = a.get("agent")
        if not name:
            continue
        direction = a.get("direction") or "NEUTRAL"
        conf = a.get("confidence")
        if conf is None:
            parts.append(f"{name}: {direction}")
        else:
            parts.append(f"{name}: {direction} ({conf}%)")
    return "; ".join(parts) if parts else "none"


def _fvg_summary(agents: List[Dict]) -> str:
    return _levels_summary(agents, "fair_value_gap")


def _open_position_summary() -> str:
    try:
        from .autotrader_state import _open_auto
        t = _open_auto()
    except Exception:
        t = None
    if not t:
        return "none"
    return (
        f"side={t.get('side')} entry={t.get('entry')} sl={t.get('sl')} "
        f"tp={t.get('tp')} source={t.get('source')}"
    )


def _gather_snapshot(event: Optional[Dict] = None) -> Optional[Dict]:
    tf = autotrader.CONFIG.get("timeframe", "15m")
    result = analysis_service.full_analysis(tf)
    if result.get("error"):
        return None
    agents = result.get("agents") or []
    hunt = result.get("hunt") or {}
    weather = result.get("weather") or {}
    market_state = result.get("market_state") or {}
    structure = (market_state.get("structure") or {}) if isinstance(market_state, dict) else {}
    c15 = dao.read_closed_candles("15m", limit=16)
    c5 = dao.read_closed_candles("5m", limit=8)
    c1 = dao.read_closed_candles("1m", limit=8)
    at = autotrader.STATE
    sc = at.get("last_scenario_result") or {}
    lc = at.get("last_lifecycle") or {}
    prev_thesis = STATE.get("thesis")
    return {
        "timeframe": tf,
        "price": result.get("price"),
        "recent_ohlcv": _recent_ohlcv_summary(c15, 8),
        "m15_ohlcv": _recent_ohlcv_summary(c15, 8),
        "m5_ohlcv": _recent_ohlcv_summary(c5, 6),
        "m1_ohlcv": _recent_ohlcv_summary(c1, 5),
        "recent_closes": _recent_candles_summary(c15, 30),
        "support_resistance": _levels_summary(agents, "support_resistance"),
        "fibonacci": _levels_summary(agents, "fibonacci"),
        "fair_value_gaps": _fvg_summary(agents),
        "agent_status_strip": _status_strip_summary(agents),
        "hunt_why": hunt.get("why_state"),
        "hunt_event": hunt.get("event"),
        "hunt_direction": hunt.get("direction"),
        "thesis_ts": hunt.get("thesis_ts") or sc.get("origin_ts"),
        "thesis_level": hunt.get("thesis_level") or sc.get("origin_level"),
        "thesis_invalid": hunt.get("thesis_invalid"),
        "weather_flag": weather.get("flag"),
        "market_regime": structure.get("regime"),
        "m5_state": (hunt.get("hunt") or {}).get("m5_path") if isinstance(hunt.get("hunt"), dict) else hunt.get("v3a_path"),
        "slot": sc.get("m5_slot"),
        "thesis_id": sc.get("thesis_id"),
        "scenario_action": sc.get("action"),
        "scenario_reason": sc.get("reason"),
        "open_position": _open_position_summary(),
        "lifecycle": lc,
        "last_action": at.get("last_action"),
        "previous_thesis": prev_thesis,
        "event": event,
    }


def _build_prompt(snap: Dict) -> str:
    ev = snap.get("event") or {}
    ev_kind = ev.get("kind") if isinstance(ev, dict) else None
    lines = [
        _PROMPT_INSTRUCTIONS,
        "",
        f"Symbol: {SYMBOL}",
        f"Timeframe: {snap.get('timeframe')}",
        f"Current price: {snap.get('price')}",
        f"Wake reason: {ev_kind or 'REVIEW_15M'}",
        f"M15 OHLC (oldest to newest): {snap.get('m15_ohlcv') or snap.get('recent_ohlcv')}",
        f"M5 OHLC: {snap.get('m5_ohlcv')}",
        f"M1 OHLC (closed): {snap.get('m1_ohlcv')}",
        f"Market regime (structure): {snap.get('market_regime')}",
        f"M5 path/state: {snap.get('m5_state')}",
        f"Support/resistance: {snap.get('support_resistance')}",
        f"Fibonacci: {snap.get('fibonacci')}",
        f"Fair value gaps: {snap.get('fair_value_gaps')}",
        f"Agent strip: {snap.get('agent_status_strip')}",
        f"Brain event: {snap.get('hunt_event')}",
        f"Brain direction: {snap.get('hunt_direction')}",
        f"Brain reasoning: {snap.get('hunt_why')}",
        f"Thesis ts/level/id: {snap.get('thesis_ts')} / {snap.get('thesis_level')} / {snap.get('thesis_id')}",
        f"Thesis invalid flag: {snap.get('thesis_invalid')}",
        f"M5 slot: {snap.get('slot')}",
        f"Scenario action: {snap.get('scenario_action')} ({snap.get('scenario_reason')})",
        f"4H weather flag: {snap.get('weather_flag')}",
        f"Open position: {snap.get('open_position')}",
        f"Lifecycle: {snap.get('lifecycle')}",
        f"Engine last_action: {snap.get('last_action')}",
        f"Previous AI thesis: {snap.get('previous_thesis')}",
    ]
    if ev:
        lines.append(f"Event payload: {ev}")
    return "\n".join(lines)


def _call_openai(prompt: str) -> str:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=CONFIG["model"],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=420,
    )
    return resp.choices[0].message.content or ""


def _parse_response(text: str) -> Dict:
    aligned = None
    thesis = text.strip()
    strength = None
    valid = None
    invalidate_if = None
    for line in text.splitlines():
        raw = line.strip()
        low = raw.lower()
        if low.startswith("aligned:"):
            v = low.split(":", 1)[1].strip()
            aligned = v.startswith("y")
        elif low.startswith("strength:"):
            strength = raw.split(":", 1)[1].strip().lower()
        elif low.startswith("valid:"):
            v = low.split(":", 1)[1].strip()
            valid = v.startswith("y")
        elif low.startswith("invalidate_if:"):
            invalidate_if = raw.split(":", 1)[1].strip()
        elif low.startswith("thesis:"):
            thesis = raw.split(":", 1)[1].strip()
    return {
        "aligned": aligned,
        "thesis": thesis,
        "strength": strength,
        "valid": valid,
        "invalidate_if": invalidate_if,
    }


def run_once(reason: str = "REVIEW_15M", event: Optional[Dict] = None) -> None:
    if event is None:
        event = {"kind": reason}
    elif "kind" not in event:
        event = {**event, "kind": reason}
    if not CONFIG["enabled"]:
        return
    STATE["processing"] = True
    STATE["last_event"] = event.get("kind")
    try:
        snap = _gather_snapshot(event)
        if snap is None:
            STATE["error"] = "not enough candle data yet"
            return
        prompt = _build_prompt(snap)
        raw = _call_openai(prompt)
        parsed = _parse_response(raw)
        STATE["thesis"] = parsed["thesis"]
        STATE["aligned_with_brain"] = parsed["aligned"]
        STATE["generated_at"] = time.time()
        STATE["error"] = None
        STATE["last_fingerprint"] = (event or {}).get("fingerprint")
    except Exception as e:
        STATE["error"] = str(e)
        logger.exception("ai_thesis run_once failed")
    finally:
        STATE["processing"] = False
        pending = STATE.get("pending_event")
        if pending:
            STATE["pending_event"] = None
            run_once(reason=pending.get("kind") or "REVIEW_15M", event=pending)


def notify_event(event: Dict) -> None:
    if not event or not CONFIG["enabled"]:
        return
    if STATE["processing"]:
        STATE["pending_event"] = event
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(run_once, event.get("kind") or "REVIEW_15M", event))
    except RuntimeError:
        run_once(reason=event.get("kind") or "REVIEW_15M", event=event)


def after_evaluate() -> None:
    try:
        from . import ai_event_detector
        ev = ai_event_detector.inspect_and_maybe_emit(autotrader.STATE)
        if ev:
            notify_event(ev)
    except Exception:
        logger.exception("ai observer hook failed")


def status() -> Dict:
    return {"config": CONFIG, "state": STATE}


async def loop() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(run_once, "REVIEW_15M", {"kind": "REVIEW_15M"})
        except Exception:
            logger.exception("ai_thesis loop iteration failed")
        await asyncio.sleep(CONFIG["interval_seconds"])
