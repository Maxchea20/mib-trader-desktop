"""HTTP handlers. Safe to call if tables empty."""
from __future__ import annotations

from typing import Any, Dict

from .isolation import fail_open
from . import model_store, replay, status, train


@fail_open({"available": False, "stage": "OFFLINE", "veto": False, "live_impact": "NONE"})
def http_status() -> Dict[str, Any]:
    return status.payload()


@fail_open({"models": []})
def http_models() -> Dict[str, Any]:
    return {"models": model_store.list_models()}


@fail_open({"ok": False, "error": "replay_unavailable"})
def http_replay(max_bars: int = 250) -> Dict[str, Any]:
    return replay.run(max_bars=max_bars)


@fail_open({"ok": False, "error": "train_unavailable"})
def http_train() -> Dict[str, Any]:
    return train.run()
