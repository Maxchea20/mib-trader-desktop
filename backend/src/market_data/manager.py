"""Live-first market-data manager: startup sync + background poller.

Startup behavior is LIVE-FIRST:
 1. Serve live price/current candle immediately (ticker).
 2. Load existing local SQLite candles immediately (chart renders from local).
 3. Run MEXC REST sync in the background.
 4. Detect + fill historical gaps, save back to SQLite.

NO SYNTHETIC / FALLBACK DATA. If MEXC (WS and REST) is unreachable, the app
reports itself as disconnected rather than fabricating prices. Trading logic
must treat "not connected" as "do nothing", never as "pretend and continue".
"""

import asyncio
import time
import json
import contextlib
import logging
import websockets
import dns.resolver
from typing import Dict, Optional, Set

from ..config import SYMBOL, TIMEFRAMES, TF_SECONDS, MAX_CANDLES
from . import database as db
from . import mexc_market_data as mexc
from . import gap_sync


logger = logging.getLogger(__name__)

MEXC_WS_URL = "wss://contract.mexc.com/edge"
MEXC_HOST = "contract.mexc.com"
DNS_SERVERS = ["8.8.8.8", "8.8.4.4"]


def resolve_mexc_ips():
    """Resolve MEXC using explicit DNS servers."""
    resolver = dns.resolver.Resolver()
    resolver.nameservers = DNS_SERVERS

    answers = resolver.resolve(MEXC_HOST, "A")
    return [str(r) for r in answers]


STATE = {
    "connected": False,
    "ws_connected": False,
    "source": "unknown",       # mexc_ws | mexc_rest | disconnected
    "last_ticker": None,
    "last_price": None,
    "last_tick_ts": None,
    "startup_synced": False,
}

_clients: Set = set()
_dirty = False


async def initial_load():
    """Non-blocking: kick everything off. Returns immediately after DB init."""
    db.init_db()

    from .. import paper_trading
    paper_trading.init_db()

    asyncio.create_task(_startup_sync())
    asyncio.create_task(_mexc_ws_loop())
    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(_poll_loop())
    asyncio.create_task(_paper_monitor_loop())
    asyncio.create_task(_autotrade_loop())


async def _autotrade_loop():
    """Hands-free: evaluate the Brain on each new candle and manage AUTO trades.

    Only acts while genuinely connected to MEXC (WS or REST). If the feed is
    down, this loop does nothing rather than trading on stale/fabricated data.
    """
    from .. import autotrader

    await asyncio.sleep(9)

    while True:
        try:
            if STATE.get("connected"):
                autotrader.evaluate(STATE.get("last_price"))
                try:
                    from .. import ai_thesis
                    ai_thesis.after_evaluate()
                except Exception:
                    pass
            else:
                autotrader.STATE["last_action"] = "PAUSED (disconnected)"
                autotrader.STATE["last_reason"] = "No live MEXC feed"
        except Exception:
            pass

        await asyncio.sleep(5)


async def _paper_monitor_loop():
    """Auto-close paper trades when the live price hits SL/TP (persists history).

    Skipped entirely while disconnected — never marks a trade SL/TP-hit
    against a price that isn't actually live from MEXC.
    """
    from .. import paper_trading

    while True:
        await asyncio.sleep(1)

        try:
            if STATE.get("connected"):
                paper_trading.check_open_trades(STATE.get("last_price"))
        except Exception:
            pass


# --- Frontend WebSocket fan-out -----------------------------------------


async def register(ws) -> None:
    _clients.add(ws)


def unregister(ws) -> None:
    _clients.discard(ws)


def _snapshot_msg() -> str:
    t = STATE["last_ticker"] or {}

    return json.dumps({
        "type": "tick",
        "price": STATE["last_price"],
        "ticker": t,
        "source": STATE["source"],
        "ws_connected": STATE["ws_connected"],
        "ts": STATE["last_tick_ts"],
    })


async def _broadcast_loop():
    """Push the latest price to all connected frontend clients (throttled)."""
    global _dirty

    while True:
        await asyncio.sleep(0.15)

        if not _dirty or not _clients:
            _dirty = False
            continue

        _dirty = False
        msg = _snapshot_msg()
        dead = []

        for ws in list(_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)

        for ws in dead:
            _clients.discard(ws)
