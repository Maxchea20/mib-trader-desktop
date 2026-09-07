"""Gap detection & historical gap recovery for persisted candles."""

from typing import List, Dict
import time

from . import database as db
from . import mexc_market_data as mexc
from ..config import TF_SECONDS


# M15 history requirement
M15_HISTORY_DAYS = 60
M15_HISTORY_SECONDS = M15_HISTORY_DAYS * 24 * 60 * 60


def detect_gaps(symbol: str, timeframe: str) -> List[Dict]:
    """Return missing candle ranges based on expected interval spacing."""

    candles = db.get_candles(
        symbol,
        timeframe,
        limit=db.count_candles(symbol, timeframe) or 1,
    )

    step = TF_SECONDS[timeframe]
    gaps = []

    for i in range(1, len(candles)):
        prev = candles[i - 1]["ts"]
        cur = candles[i]["ts"]

        delta = cur - prev

        if delta > step * 1.5:
            missing = int(round(delta / step)) - 1

            gaps.append(
                {
                    "from_ts": prev,
                    "to_ts": cur,
                    "missing": missing,
                }
            )

    return gaps


async def sync_timeframe(
    symbol: str,
    timeframe: str,
    limit: int = 1000,
) -> Dict:
    """
    Fetch and persist market history.

    M15:
        Maintain at least 60 days of historical candles.
        MEXC is queried in batches because a single request is limited.

    Other timeframes:
        Preserve the existing behavior of fetching the latest batch.
    """

    try:
        now = int(time.time())

        # ---------------------------------------------------------------
        # Always refresh the latest batch first.
        # ---------------------------------------------------------------
        latest = await mexc.get_klines(
            symbol,
            timeframe,
            limit=limit,
        )

        saved = 0

        if latest:
            saved += db.upsert_candles(
                symbol,
                timeframe,
                latest,
            )

        # ---------------------------------------------------------------
        # M15: backfill until we have 60 days.
        # ---------------------------------------------------------------
        if timeframe == "15m":
            step = TF_SECONDS[timeframe]

            target_start = now - M15_HISTORY_SECONDS

            existing = db.get_candles(
                symbol,
                timeframe,
                limit=db.count_candles(symbol, timeframe) or 1,
            )

            if existing:
                oldest_ts = existing[0]["ts"]
            elif latest:
                oldest_ts = latest[0]["ts"]
            else:
                oldest_ts = now

            batches = 0
            max_batches = 10

            while oldest_ts > target_start:
                batch_end = oldest_ts - step

                batch_start = max(
                    target_start,
                    batch_end - step * (limit + 2),
                )

                if batch_start >= batch_end:
                    break

                older = await mexc.get_klines(
                    symbol,
                    timeframe,
                    limit=limit,
                    start=batch_start,
                    end=batch_end,
                )

                if not older:
                    break

                batch_saved = db.upsert_candles(
                    symbol,
                    timeframe,
                    older,
                )

                saved += batch_saved
                batches += 1

                new_oldest = min(
                    candle["ts"]
                    for candle in older
                )

                # Safety against an API returning the same window repeatedly.
                if new_oldest >= oldest_ts:
                    break

                oldest_ts = new_oldest

                # Prevent an accidental endless loop.
                if batches >= max_batches:
                    break

            status = "ok"

            candles_now = db.get_candles(
                symbol,
                timeframe,
                limit=db.count_candles(symbol, timeframe) or 1,
            )

            history_days = 0.0

            if candles_now:
                oldest = candles_now[0]["ts"]
                newest = candles_now[-1]["ts"]

                history_days = (
                    newest - oldest
                ) / 86400.0

            db.set_sync_meta(
                symbol,
                timeframe,
                now,
                status,
            )

            return {
                "timeframe": timeframe,
                "status": status,
                "saved": saved,
                "history_days": round(history_days, 2),
                "target_days": M15_HISTORY_DAYS,
                "batches": batches,
                "gaps": len(
                    detect_gaps(symbol, timeframe)
                ),
            }

        # ---------------------------------------------------------------
        # Other timeframes: existing behavior.
        # ---------------------------------------------------------------
        if not latest:
            db.set_sync_meta(
                symbol,
                timeframe,
                now,
                "empty",
            )

            return {
                "timeframe": timeframe,
                "status": "empty",
                "saved": 0,
            }

        db.set_sync_meta(
            symbol,
            timeframe,
            now,
            "ok",
        )

        return {
            "timeframe": timeframe,
            "status": "ok",
            "saved": saved,
            "gaps": len(
                detect_gaps(symbol, timeframe)
            ),
        }

    except Exception as e:
        db.set_sync_meta(
            symbol,
            timeframe,
            int(time.time()),
            "error",
        )

        return {
            "timeframe": timeframe,
            "status": "error",
            "error": str(e),
            "saved": 0,
        }


async def sync_latest(
    symbol: str,
    timeframe: str,
) -> Dict:
    """Latest-candle synchronization — small window for live forming candle."""

    try:
        candles = await mexc.get_klines(
            symbol,
            timeframe,
            limit=5,
        )

        saved = db.upsert_candles(
            symbol,
            timeframe,
            candles,
        )

        db.set_sync_meta(
            symbol,
            timeframe,
            int(time.time()),
            "ok",
        )

        return {
            "timeframe": timeframe,
            "status": "ok",
            "saved": saved,
        }

    except Exception as e:
        return {
            "timeframe": timeframe,
            "status": "error",
            "error": str(e),
        }