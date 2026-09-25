"""Typed features from a Hunt pack. Closed-bar fields only."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

FEATURE_KEYS = [
    "direction_long",
    "event_choch",
    "gate_cfast",
    "gate_internal",
    "gate_rearm",
    "timing_s1",
    "timing_s2",
    "timing_slot3",
    "slot",
    "weather_trend",
    "weather_chop",
    "utc_hour",
    "weekday",
    "atr_pct",
    "dist_thesis_atr",
    "armed",
    "timing_miss",
]


def _f(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def extract(hunt: Optional[Dict], weather: Optional[Dict], ts: int, extra: Optional[Dict] = None) -> Dict[str, float]:
    hunt = hunt or {}
    weather = weather or {}
    extra = extra or {}
    nested = hunt.get("hunt") if isinstance(hunt.get("hunt"), dict) else {}
    direction = str(hunt.get("direction") or "").upper()
    event = str(hunt.get("event") or nested.get("event") or "").upper()
    gate = str(hunt.get("gate") or "")
    timing = str(hunt.get("timing") or "")
    flag = str(weather.get("flag") or hunt.get("weather_flag") or "").upper()
    slot = hunt.get("slot")
    if slot is None:
        slot = nested.get("slot")
    atr = _f(hunt.get("atr_15m") or extra.get("atr"))
    price = _f(extra.get("price") or hunt.get("entry"))
    level = _f(hunt.get("thesis_level") or nested.get("level"))
    dist = abs(price - level) / atr if atr > 0 and level else 0.0
    hour = 0
    wd = 0
    if ts:
        import time as _t
        g = _t.gmtime(int(ts))
        hour = int(g.tm_hour)
        wd = int(g.tm_wday)
    return {
        "direction_long": 1.0 if direction == "LONG" else 0.0,
        "event_choch": 1.0 if "CHOCH" in event else 0.0,
        "gate_cfast": 1.0 if "cfast" in gate else 0.0,
        "gate_internal": 1.0 if gate == "internal" else 0.0,
        "gate_rearm": 1.0 if "rearm" in gate else 0.0,
        "timing_s1": 1.0 if timing == "S1" else 0.0,
        "timing_s2": 1.0 if timing == "S2" else 0.0,
        "timing_slot3": 1.0 if timing == "SLOT3" else 0.0,
        "slot": _f(slot),
        "weather_trend": 1.0 if "TREND" in flag or flag in ("BULL", "BEAR") else 0.0,
        "weather_chop": 1.0 if "CHOP" in flag else 0.0,
        "utc_hour": float(hour),
        "weekday": float(wd),
        "atr_pct": _f(extra.get("atr_pct")),
        "dist_thesis_atr": dist,
        "armed": 1.0 if hunt.get("armed") else 0.0,
        "timing_miss": 1.0 if hunt.get("timing_miss") else 0.0,
    }


def vector(feat: Dict[str, float]) -> List[float]:
    return [float(feat.get(k) or 0.0) for k in FEATURE_KEYS]


def dumps(feat: Dict[str, float]) -> str:
    return json.dumps(feat)


def loads(raw: Any) -> Dict[str, float]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
