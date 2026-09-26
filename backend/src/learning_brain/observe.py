"""Edge hook: record Hunt pack after Brain 1 already decided."""
from __future__ import annotations

from typing import Dict, Optional

from .isolation import fail_open
from . import snapshot

_last_fp = None


def _fp(hunt: Dict, last_action: Optional[str]) -> tuple:
    return (
        hunt.get("action"),
        hunt.get("timing_state"),
        hunt.get("timing"),
        hunt.get("thesis_ts"),
        hunt.get("direction"),
        last_action,
        hunt.get("timing_miss"),
    )


@fail_open(None)
def observe_hunt(
    hunt: Optional[Dict],
    weather: Optional[Dict] = None,
    extra: Optional[Dict] = None,
    last_action: Optional[str] = None,
    source: str = "live",
    trade_id: Optional[str] = None,
    ts: Optional[int] = None,
) -> Optional[str]:
    global _last_fp
    hunt = hunt or {}
    fp = _fp(hunt, last_action)
    if fp == _last_fp and hunt.get("action") != "FIRE":
        return None
    _last_fp = fp
    return snapshot.record(
        hunt, weather=weather, extra=extra, last_action=last_action,
        source=source, trade_id=trade_id, ts=ts,
    )
