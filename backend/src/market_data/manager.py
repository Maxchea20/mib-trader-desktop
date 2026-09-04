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


# --- MEXC live WebSocket -----------------------------------------------


async def _mexc_ws_loop():
    """Connect to MEXC Futures WS using resolved IPs and stream live data."""
    global _dirty

    while True:
        try:
            # Resolve MEXC through explicit DNS servers.
            ips = await asyncio.to_thread(resolve_mexc_ips)

            logger.info(f"MEXC resolved IPs: {ips}")

            connected = False

            # Try each resolved IP until one works.
            for ip in ips:
                try:
                    logger.info(f"Connecting to MEXC: {ip}")

                    async with websockets.connect(
                        MEXC_WS_URL,
                        host=ip,
                        port=443,
                        ping_interval=None,
                        open_timeout=12,
                        close_timeout=5,
                    ) as ws:

                        logger.info("MEXC WebSocket connected.")

                        await ws.send(json.dumps({
                            "method": "sub.ticker",
                            "param": {
                                "symbol": SYMBOL
                            }
                        }))

                        logger.info(
                            f"Subscribed to {SYMBOL} ticker."
                        )

                        await ws.send(json.dumps({
                            "method": "sub.deal",
                            "param": {
                                "symbol": SYMBOL
                            }
                        }))

                        logger.info(
                            f"Subscribed to {SYMBOL} trades."
                        )

                        async def _keepalive():
                            while True:
                                await asyncio.sleep(12)

                                try:
                                    await ws.send(
                                        json.dumps({
                                            "method": "ping"
                                        })
                                    )
                                except Exception:
                                    break

                        ka = asyncio.create_task(_keepalive())

                        STATE["ws_connected"] = True
                        STATE["connected"] = True
                        STATE["source"] = "mexc_ws"
                        connected = True

                        try:
                            while True:
                                raw = await asyncio.wait_for(
                                    ws.recv(),
                                    timeout=40
                                )

                                d = json.loads(raw)
                                last_tick = STATE.get("last_tick_ts")
                                if last_tick is not None and time.time() - last_tick > 45:
                                    raise ConnectionError(f"MEXC market data stale: {int(time.time() - last_tick)}s")
                                ch = d.get("channel")

                                if ch == "push.ticker":
                                    data = d.get("data", {})

                                    _update_ticker(data)
                                    _dirty = True

                                elif ch == "push.deal":
                                    deals = d.get("data")

                                    deal = (
                                        deals[0]
                                        if isinstance(deals, list)
                                        and deals
                                        else deals
                                    )

                                    if (
                                        isinstance(deal, dict)
                                        and deal.get("p") is not None
                                    ):
                                        _update_price(
                                            float(deal["p"])
                                        )
                                        _dirty = True

                        finally:
                            ka.cancel()

                            with contextlib.suppress(
                                asyncio.CancelledError,
                                Exception,
                            ):
                                await ka

                except Exception as e:
                    logger.error(
                        f"MEXC connection failed ({ip}): "
                        f"{type(e).__name__}: {e}"
                    )

                    STATE["ws_connected"] = False

                    continue

            # None of the resolved IPs worked.
            if not connected:
                STATE["connected"] = False
                STATE["ws_connected"] = False
                STATE["source"] = "disconnected"

                logger.error(
                    "MEXC WebSocket unavailable on all resolved IPs."
                )

                await asyncio.sleep(3)

        except Exception as e:
            logger.error(
                f"MEXC WebSocket/DNS error: "
                f"{type(e).__name__}: {e}"
            )

            STATE["ws_connected"] = False
            STATE["connected"] = False
            STATE["source"] = "disconnected"

            await asyncio.sleep(3)


# --- State updates ------------------------------------------------------


def _update_price(price: float):
    STATE["last_price"] = price
    STATE["last_tick_ts"] = int(time.time())

    if STATE["last_ticker"] is None:
        STATE["last_ticker"] = {
            "symbol": SYMBOL,
            "last": price,
        }
    else:
        STATE["last_ticker"]["last"] = price


def _update_ticker(data: dict):
    try:
        last = float(
            data.get(
                "lastPrice",
                STATE.get("last_price") or 0
            )
        )

        t = {
            "symbol": data.get("symbol", SYMBOL),
            "last": last,
            "bid": float(data.get("bid1", last)),
            "ask": float(data.get("ask1", last)),
            "high24": float(
                data.get("high24Price", 0)
            ),
            "low24": float(
                data.get("lower24Price", 0)
            ),
            "volume24": float(
                data.get("volume24", 0)
            ),
            "amount24": float(
                data.get("amount24", 0)
            ),
            "change_rate": float(
                data.get("riseFallRate", 0)
            ),
            "change_value": float(
                data.get("riseFallValue", 0)
            ),
            "funding_rate": float(
                data.get("fundingRate", 0)
            ),
            "index_price": float(
                data.get("indexPrice", last)
            ),
            "ts": int(
                data.get(
                    "timestamp",
                    int(time.time() * 1000)
                )
            ),
        }

        STATE["last_ticker"] = t
        STATE["last_price"] = last
        STATE["last_tick_ts"] = int(time.time())

    except (TypeError, ValueError):
        pass


# --- REST startup sync --------------------------------------------------


async def _startup_sync():
    try:
        ok = await mexc.ping()
        if not ok:
            if not STATE["ws_connected"]:
                STATE["connected"] = False
                STATE["source"] = "disconnected"
            STATE["startup_synced"] = False
            return
        for tf in TIMEFRAMES:
            try:
                await gap_sync.sync_timeframe(SYMBOL, tf)
            except Exception as exc:
                logger.error(f"Startup sync failed for {tf}: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.15)
        STATE["startup_synced"] = True
        STATE["connected"] = True
        STATE["source"] = "mexc_ws" if STATE["ws_connected"] else "mexc_rest"
    except Exception as exc:
        logger.error(f"Startup sync error: {type(exc).__name__}: {exc}")
        STATE["startup_synced"] = False
        if not STATE["ws_connected"]:
            STATE["connected"] = False
            STATE["source"] = "disconnected"


# --- Background candle polling -----------------------------------------


async def _poll_loop():
    """Low-frequency candle sync.

    Live price comes from MEXC WebSocket.
    REST is used only for candle persistence, gap recovery,
    and as a price fallback when WebSocket is unavailable.
    """
    last_1m_sync = 0
    last_5m_sync = 0
    last_15m_sync = 0
    last_full_sync = time.time()
    last_rest_ticker = 0

    while True:
        now = time.time()

        try:
            # 1m candles: refresh every 15 seconds
            if now - last_1m_sync >= 15:
                await gap_sync.sync_latest(SYMBOL, "1m")
                last_1m_sync = time.time()

            # 5m + 15m candles: refresh every 60 seconds
            if now - last_5m_sync >= 60:
                await gap_sync.sync_latest(SYMBOL, "5m")
                last_5m_sync = time.time()

            if now - last_15m_sync >= 60:
                await gap_sync.sync_latest(SYMBOL, "15m")
                last_15m_sync = time.time()

            # Full gap recovery every 5 minutes.
            # Do NOT run immediately after startup because
            # _startup_sync() already performed the initial sync.
            if now - last_full_sync >= 300:
                for tf in TIMEFRAMES:
                    await gap_sync.sync_timeframe(
                        SYMBOL,
                        tf,
                        limit=MAX_CANDLES,
                    )
                    await asyncio.sleep(0.1)

                last_full_sync = time.time()

            # REST price fallback only when WebSocket is unavailable.
            # Limit fallback requests to once every 30 seconds.
            if (
                not STATE["ws_connected"]
                and now - last_rest_ticker >= 30
            ):
                t = await mexc.get_ticker(SYMBOL)
                last_rest_ticker = time.time()

                if t:
                    STATE["connected"] = True
                    STATE["source"] = "mexc_rest"
                    STATE["last_ticker"] = t
                    STATE["last_price"] = t["last"]
                    STATE["last_tick_ts"] = int(time.time())
                else:
                    STATE["connected"] = False
                    STATE["source"] = "disconnected"

        except Exception as e:
            logger.error(
                f"Market candle sync error: "
                f"{type(e).__name__}: {e}"
            )

            if not STATE["ws_connected"]:
                STATE["connected"] = False
                STATE["source"] = "disconnected"

        await asyncio.sleep(5)

# --- Public status ------------------------------------------------------


def live_status() -> Dict:
    age = None

    if STATE["last_tick_ts"]:
        age = int(time.time()) - STATE["last_tick_ts"]

    return {
        "connected": STATE["connected"],
        "ws_connected": STATE["ws_connected"],
        "source": STATE["source"],
        "startup_synced": STATE["startup_synced"],
        "last_price": STATE["last_price"],
        "tick_age_seconds": age,
        "ticker": STATE["last_ticker"],
    }