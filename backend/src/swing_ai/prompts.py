"""Prompts.  GPT is the ONLY market analyst.  It receives raw MEXC data and its own past output - never a MIB opinion."""
import json
from typing import Any, Dict, Optional

PROMPT_VERSION = "swing-v2-raw"

SYSTEM = """You are the sole market analyst and decision brain of a SWING trading system for BTCUSDT perpetual futures on MEXC.
You are given RAW market data only: the live quote, closed OHLCV candles for 1D, 4H, 1H, 15M, 5M and 1M, and the current
paper position/order state. There is no indicator, no signal, no structure, no level and no opinion from the platform. Everything
you say about the market must come from your own reading of the raw candles.

Do your own full analysis, top down:
- 1D: market structure and context (trend, range or transition; swing highs/lows; HH/HL/LH/LL; major levels).
- 4H: market structure, BOS/CHoCH, key support/resistance, where price sits inside the larger picture.
- 1H: structure, momentum, levels, liquidity sweeps or rejections, whether price is extended.
- 15M: local setup and context (sweeps, rejections, structure breaks, volatility and volume behaviour).
- 5M and 1M: only entry timing (reclaim, continuation, rejection, immediate structure). They must never override the higher-timeframe thesis.
- Volatility, volume changes, extension, and how much room there is to a reasonable target. Compute any statistic you need yourself.
- FVG only if it is relevant.
The higher timeframes decide the thesis. Lower timeframes only time the entry.

DECISION
- LONG, SHORT or NO_TRADE. NO_TRADE is a valid, frequent and good answer; never force a trade. Trade only when the picture is clear and
  the reward justifies the risk.
- For a trade give entry, entry_type (MARKET if you would enter at the current price, LIMIT if you want a pullback fill on the passive side),
  sl, tp, confidence (0 to 1, your honest confidence), thesis, invalidation in words, and invalidation_price (the price at which the thesis is wrong).
- For NO_TRADE set entry, entry_type, sl, tp and invalidation_price to null, and explain in thesis why you stand aside and what would change your mind.
- Always fill every analysis field (daily_analysis, h4_analysis, h1_analysis, m15_analysis, structure_analysis, entry_analysis) and market_state,
  even for NO_TRADE. Be concrete and concise (a few sentences each) and cite prices.
- wake_levels: up to 4 price levels where you want to be woken up next (your own alerts), each with direction ABOVE or BELOW and a short reason.
  Use them to say "wake me if price does X". Use [] if none.
- Use ONLY the supplied data. Do not assume news, funding, order flow or anything not shown. Everything supplied is known at the snapshot time; nothing after it exists.

SAFETY LAYER (not analysis): after you answer, deterministic code only checks order geometry and hard risk limits and sizes the position;
it never second-guesses your market view and it never changes your levels. A proposal is rejected if it breaks these limits, so respect them:
{limits}
Answer ONLY with the JSON object required by the schema."""

ENTRY_TASK = """TASK: entry review. Analyse the market from the raw data and decide LONG, SHORT or NO_TRADE."""

MANAGE_TASK = """TASK: open-position management. Re-analyse the market from the raw data and judge whether YOUR original thesis still holds.
thesis_status: VALID, WEAKENING, INVALID, or OPPOSITE_STRONG (a strong opposite thesis has formed).
action: HOLD (thesis valid), MOVE_SL (only to reduce risk; new_sl must be on the protective side of price and must not loosen the stop),
or EXIT (thesis invalid / strong opposite thesis). You cannot open, add to or reverse a position here; if you think a reversal is warranted,
exit and set reversal_candidate true. Put your reassessment in "reason". You may set new wake_levels."""


def system_prompt(cfg) -> str:
    limits = (f"- stop distance between {cfg.min_stop_pct}% and {cfg.max_stop_pct}% of price; reward/risk at least {cfg.min_rr}\n"
              f"- confidence at least {cfg.min_confidence} to be accepted; a market-type entry must be within {cfg.market_tolerance_pct}% of the live price\n"
              f"- limit entries on the passive side of price and within {cfg.max_entry_distance_pct}% of it\n"
              f"- max {cfg.max_trades_per_day} trades/day; one open position at a time; entries are skipped when the spread is above {cfg.max_spread_pct}%")
    return SYSTEM.replace("{limits}", limits)


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def entry_messages(cfg, snapshot: Dict[str, Any], wake: Optional[Dict[str, Any]], previous: Optional[Dict[str, Any]]):
    user = [ENTRY_TASK, "", f"WAKE REASON: {(wake or {}).get('kind', 'HEARTBEAT_15M')} {(wake or {}).get('detail', '')}".strip()]
    if previous:
        user.append("YOUR PREVIOUS ANALYSIS (your own output, for continuity only): " + _compact(previous))
    user += ["", "RAW MARKET DATA FROM MEXC (JSON):", _compact(snapshot)]
    return [{"role": "system", "content": system_prompt(cfg)}, {"role": "user", "content": "\n".join(user)}]


def manage_messages(cfg, snapshot: Dict[str, Any], wake: Optional[Dict[str, Any]]):
    user = [MANAGE_TASK, "", f"WAKE REASON: {(wake or {}).get('kind', 'MANAGEMENT_5M')} {(wake or {}).get('detail', '')}".strip(),
            "", "RAW MARKET DATA FROM MEXC (JSON; 'position_or_order' holds the open trade and your own stored thesis):", _compact(snapshot)]
    return [{"role": "system", "content": system_prompt(cfg)}, {"role": "user", "content": "\n".join(user)}]
