"""RAW MARKET EVIDENCE for the AI.  This module performs NO market analysis.

It only packages what MEXC published (as cached in the local candle table): the live quote, closed OHLCV candles for
1D / 4H / 1H / 15M / 5M / 1M, and the current paper position/order state.  There is deliberately no trend, structure,
swing, level, indicator, momentum, volatility, bias, confidence or signal in here - GPT does all interpretation.

Causality: only candles with ts + tf_seconds <= now are included (the forming bar is never sent); the live price is
sent separately.  Nothing after `now` can appear."""
from typing import Any, Dict, Optional

from .source import TF_SEC, closed_only

# How much raw history GPT receives per timeframe (closed candles, newest last).
LIMITS = {"1d": 90, "4h": 180, "1h": 240, "15m": 192, "5m": 144, "1m": 90}
LIMITS_COMPACT = {"1d": 45, "4h": 90, "1h": 120, "15m": 96, "5m": 72, "1m": 45}     # about 45% fewer tokens
LIMITS_LEAN = {"1d": 30, "4h": 60, "1h": 96, "15m": 64, "5m": 36, "1m": 30}          # about 65% fewer tokens than FULL
PROFILES = {"FULL": LIMITS, "COMPACT": LIMITS_COMPACT, "LEAN": LIMITS_LEAN}
MIN_ROWS = {"1d": 20, "4h": 30, "1h": 48, "15m": 48, "5m": 24, "1m": 20}
ORDER = ("1d", "4h", "1h", "15m", "5m", "1m")
COLUMNS = ["open", "high", "low", "close", "volume"]


def _vol(v: float):
    return int(round(v)) if v >= 100 else round(v, 2)


def _row(c: dict):
    return [round(float(c["open"]), 2), round(float(c["high"]), 2), round(float(c["low"]), 2), round(float(c["close"]), 2), _vol(float(c["volume"]))]


def _frame(rows, step: int) -> Dict[str, Any]:
    """Token-lean candle block: no timestamp per row.  Row i opens at first_open_ts + i*step_seconds, except that any missing
    candles are declared in time_breaks as [row_index, open_ts] (that row, and the ones after it, restart from that time)."""
    breaks = [[i, int(r["ts"])] for i, r in enumerate(rows) if i and int(r["ts"]) != int(rows[i - 1]["ts"]) + step]
    return {"step_seconds": step, "first_open_ts": int(rows[0]["ts"]), "last_open_ts": int(rows[-1]["ts"]), "time_breaks": breaks,
            "candles": [_row(r) for r in rows]}


def build_raw_snapshot(source, now: float, live_price: Optional[float], ticker: Optional[Dict[str, Any]] = None,
                       position: Optional[Dict[str, Any]] = None, symbol: str = "BTC_USDT",
                       profile: str = "FULL") -> Optional[Dict[str, Any]]:
    """Returns the raw snapshot, or None when a timeframe has too little closed history.
    Key order is deliberate: slow-changing data (daily/4H candles) first and fast-changing data (live quote, time, position) last,
    so the start of the prompt is identical between consecutive calls and OpenAI's prompt cache can discount it."""
    limits = PROFILES.get(profile, LIMITS)
    frames: Dict[str, Any] = {}
    for tf in ORDER:
        rows = closed_only(source.closed(tf, limits[tf], now), tf, now)
        if len(rows) < MIN_ROWS[tf]:
            return None
        frames[tf] = _frame(rows, TF_SEC[tf])
    q = ticker or {}
    bid, ask = q.get("bid"), q.get("ask")
    price = float(live_price) if live_price else frames["1m"]["candles"][-1][3]
    live = {"price": price, "bid": bid, "ask": ask, "spread": (ask - bid) if bid and ask else None,
            "exchange_24h_volume": q.get("volume24")}
    return {"symbol": symbol, "source": "MEXC", "candle_columns": COLUMNS,
            "candle_note": "Only fully CLOSED candles, oldest first, columns per candle_columns. No per-row timestamp: row i opened at first_open_ts + i*step_seconds "
                           "(unix seconds, UTC) unless time_breaks says otherwise ([row_index, open_ts] restarts the clock at that row); last_open_ts is the newest candle.",
            "timeframes": frames, "live": live, "position_or_order": position, "as_of_unix": int(now)}


def position_state(pos: Dict[str, Any], price: float, now: float) -> Dict[str, Any]:
    """Current paper position/order facts (accounting only) plus the AI's OWN stored thesis for continuity.

    A pending LIMIT order is described as NOT filled and carries no entry price or P&L, so it can never be mistaken for an open trade."""
    common = {"trade_id": pos["id"], "side": pos["side"], "stop_loss": pos["sl"], "take_profit": pos["tp"], "quantity_btc": pos["qty"],
              "your_thesis": pos["thesis"], "your_invalidation": pos["invalidation"], "your_invalidation_price": pos["invalidation_price"]}
    if pos["status"] == "PENDING":
        return {**common, "status": "PENDING_NOT_FILLED", "order_type": pos["entry_type"], "trigger_price": pos["plan_entry"],
                "placed_unix": pos["created_ts"], "expires_unix": pos["expires_ts"],
                "note": "You are NOT in a trade. This pending order has not filled: a LIMIT fills if price trades back to trigger_price, a STOP fills if price trades through it."}
    d = 1 if pos["side"] == "LONG" else -1
    fill = pos["fill_price"] or pos["plan_entry"]
    rd = pos["risk_dist"] or 1.0
    return {**common, "status": "OPEN", "entry_price": fill, "initial_stop_loss": pos["sl0"], "opened_unix": pos["opened_ts"] or pos["created_ts"],
            "unrealized_r": round(d * (price - fill) / rd, 3), "max_favorable_r": round(pos["mfe_r"] or 0, 3),
            "max_adverse_r": round(pos["mae_r"] or 0, 3)}
