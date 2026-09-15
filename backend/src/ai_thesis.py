"""AI Thesis — a read-only, OpenAI-generated plain-English read of the current
chart, compared against what Hunt C-FI/the Brain is currently doing.

HARD BOUNDARY: this module has no write path anywhere. It never calls
paper_trading.open_trade/close_trade, never calls autotrader.update(), never
touches MEXC order submission. It only reads analysis_service.full_analysis()
(the same read-only call BrainHeroPanel already renders from) and
autotrader.compute_sizing() (the sizing PREVIEW, not an order). If this
module crashed entirely, nothing about live trading would be affected.

The AI is explicitly instructed to form its own read first and state
disagreement with the Brain plainly when it has one — not to rationalize
whatever the Brain already concluded.
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
    "interval_seconds": 900,   # 15 min
    "model": os.environ.get("AI_THESIS_MODEL", "gpt-5.4-mini"),
}

STATE = {
    "thesis": None,
    "aligned_with_brain": None,   # True / False / None (model states this itself; None until first run)
    "generated_at": None,
    "processing": False,
    "error": None,
}

_PROMPT_INSTRUCTIONS = """You are a market-analysis assistant embedded in a trading terminal. You are \
shown the current BTC_USDT chart data and the terminal's own automated "Brain" state (Hunt C-FI). \
Your job is ONLY to observe and explain — you do not trade, you have no ability to place or affect \
any order, and nothing you say is executed automatically.

Form your OWN independent read of the price action first, from the candles and structure data given. \
THEN compare it to what the Brain currently says (its WAIT/FIRE state and its stated reasoning). \
State plainly whether you agree or disagree with the Brain. If you disagree, say so directly and \
explain why in plain English — do not soften a genuine disagreement just to sound aligned, and do not \
assume the Brain is right by default.

Write for someone who is NOT a trader — plain English, no jargon, 3-5 sentences.

Respond in exactly this format:
ALIGNED: yes|no
THESIS: <your plain-English read of what's happening and why, including whether/why you agree or \
disagree with the Brain>"""


def _recent_candles_summary(candles: List[Dict], n: int = 30) -> str:
    tail = candles[-n:] if len(candles) > n else candles
    lines = [f"{c['close']:.1f}" for c in tail]
    return ", ".join(lines)


def _gather_snapshot() -> Optional[Dict]:
    """Read-only. Returns None if there isn't enough data yet (mirrors the
    same guard full_analysis() itself uses)."""
    tf = autotrader.CONFIG.get("timeframe", "15m")
    result = analysis_service.full_analysis(tf)
    if result.get("error"):
        return None
    sizing = autotrader.compute_sizing()
    candles = dao.read_closed_candles(tf, limit=40)
    return {
        "timeframe": tf,
        "price": result.get("price"),
        "recent_closes": _recent_candles_summary(candles),
        "hunt_action": (result.get("hunt") or {}).get("action"),
        "hunt_why": (result.get("hunt") or {}).get("why_state"),
        "weather_flag": (result.get("weather") or {}).get("flag"),
        "market_regime": ((result.get("market_state") or {}).get("structure") or {}).get("regime"),
        "sizing_mode": sizing.get("sizing_mode"),
        "sizing_error": sizing.get("error"),
    }


def _build_prompt(snap: Dict) -> str:
    return (
        f"{_PROMPT_INSTRUCTIONS}\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {snap['timeframe']}\n"
        f"Current price: {snap['price']}\n"
        f"Recent closes (oldest to newest): {snap['recent_closes']}\n"
        f"Market regime (structure): {snap.get('market_regime')}\n"
        f"Brain's current action: {snap.get('hunt_action')}\n"
        f"Brain's stated reasoning: {snap.get('hunt_why')}\n"
        f"4H weather flag: {snap.get('weather_flag')}\n"
    )


def _call_openai(prompt: str) -> str:
    from openai import OpenAI  # imported lazily so a missing key/package never breaks startup
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=CONFIG["model"],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
    )
    return resp.choices[0].message.content or ""


def _parse_response(text: str) -> Dict:
    aligned = None
    thesis = text.strip()
    for line in text.splitlines():
        low = line.strip().lower()
        if low.startswith("aligned:"):
            v = low.split(":", 1)[1].strip()
            aligned = v.startswith("y")
        if line.strip().lower().startswith("thesis:"):
            thesis = line.split(":", 1)[1].strip()
    return {"aligned": aligned, "thesis": thesis}


def run_once() -> None:
    """One full cycle: gather -> prompt -> call -> store. Never raises —
    failures land in STATE["error"] so the loop keeps running on schedule."""
    if not CONFIG["enabled"]:
        return
    STATE["processing"] = True
    try:
        snap = _gather_snapshot()
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
    except Exception as e:
        STATE["error"] = str(e)
        logger.exception("ai_thesis run_once failed")
    finally:
        STATE["processing"] = False


def status() -> Dict:
    return {"config": CONFIG, "state": STATE}


async def loop() -> None:
    """Background task — started once from server.py's startup hook. Sleeps
    interval_seconds between runs; a slow/failed OpenAI call never blocks or
    crashes anything else, since run_once() catches everything and this is
    its own independent asyncio task."""
    await asyncio.sleep(10)  # let the rest of the app finish its own startup first
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            logger.exception("ai_thesis loop iteration failed")
        await asyncio.sleep(CONFIG["interval_seconds"])