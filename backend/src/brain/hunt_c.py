"""Hunt C timing only: 1m confirmation inside one qualifying 5m candle."""
from __future__ import annotations

from typing import Dict, List
from .entry_timing_c import find_m5_2_intrabar_entry
from ..contract import LONG, SHORT


def start_c_watch(
    state: dict,
    *,
    path: str,
    thesis_id: str,
    m5_ts: int,
    level: float,
    direction: str,
) -> None:
    state["phase"] = "C_WATCH"
    state["path"] = path
    state["c_watch"] = {
        "path": path,
        "thesis_id": thesis_id,
        "window_open_ts": int(m5_ts),
        "structural_level": float(level),
        "direction": direction,
        "checked": set(),
    }


def tick_c(state: dict, one_minute_candles: List[dict]) -> str:
    """Return SUCCESS/MISS/NONE. This module never creates thesis/FIRE/order."""
    watch = state.get("c_watch") or {}
    open_ts = watch.get("window_open_ts")
    level = watch.get("structural_level")
    side = watch.get("direction")
    if open_ts is None or level is None or side not in (LONG, SHORT):
        return "NONE"

    window = [
        c for c in (one_minute_candles or [])
        if int(open_ts) <= int(c["ts"]) < int(open_ts) + 300
    ]
    window.sort(key=lambda c: int(c["ts"]))
    if not window:
        return "NONE"

    got = find_m5_2_intrabar_entry(
        side,
        float(level),
        int(open_ts),
        window,
    )
    if got:
        watch["result"] = got
        return "SUCCESS"

    have = {int(c["ts"]) for c in window}
    if all((int(open_ts) + i * 60) in have for i in range(5)):
        return "MISS"

    last_1m = int(open_ts) + 4 * 60
    if int(window[-1]["ts"]) >= last_1m:
        return "MISS"

    return "NONE"
