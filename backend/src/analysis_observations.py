"""Collect AnalysisObservation adapters + Hunt C-FI snapshot for the API/UI."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .breakout.observe import observe as obs_breakout
from .fair_value_gap.observe import observe as obs_fvg
from .momentum.observe import observe as obs_mom
from .structure.observe import observe as obs_structure
from .support_resistance.observe import observe as obs_sr
from .trend.observe import observe as obs_trend
from .volume.observe import observe as obs_vol
from .brain.observation_hunt_c import parent_open
from .brain.observation_hunt_c_fi import evaluate_hunt_c_fi, HUNT_VERSION_C_FI
from .brain.weather import classify
from .market_state.builder import build_market_state


def _live_5ms(candles_5m: List[dict]) -> List[dict]:
    if not candles_5m:
        return []
    po = parent_open(candles_5m[-1]["ts"])
    return [c for c in candles_5m if parent_open(c["ts"]) == po]


def _map_15(
    candles_15m: List[dict], candles_5m: Optional[List[dict]]
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    """Prior closed 15m high/low/ts — the bar 5m #3 must close through."""
    if not candles_15m:
        return None, None, None
    last15 = candles_15m[-1]
    forming = bool(
        candles_5m
        and parent_open(candles_5m[-1]["ts"]) == int(last15["ts"])
    )
    prior = candles_15m[-2] if forming and len(candles_15m) >= 2 else last15
    try:
        return float(prior["high"]), float(prior["low"]), int(prior["ts"])
    except (TypeError, ValueError, KeyError):
        return None, None, None


def _hunt_breaks(candles_15m: List[dict]) -> List[dict]:
    """Last swing high/low Hunt treats as the next CHoCH or BOS break.

    C-fast (default 15m structure) is first. Break up through last swing
    high is BOS if Hunt is already bullish, CHoCH if Hunt is bearish.
    Break down is the mirror.
    """
    if not candles_15m or len(candles_15m) < 30:
        return []
    try:
        ms = build_market_state(candles_15m, symbol="UNKNOWN", timeframe="15m")
    except Exception:
        return []
    last = None
    for e in ms.structure.events or []:
        if (e.event or "") in ("CHoCH", "CHOCH", "BOS"):
            last = e
    bull = bool(last and (last.direction or "") in ("LONG", "BULLISH"))
    bear = bool(last and (last.direction or "") in ("SHORT", "BEARISH"))
    out: List[dict] = []
    if ms.swing_highs:
        sh = ms.swing_highs[-1]
        kind = "BOS" if bull else ("CHoCH" if bear else "CHoCH/BOS")
        out.append({
            "side": "up", "kind": kind,
            "price": float(sh.price), "ts": int(sh.timestamp),
        })
    if ms.swing_lows:
        sl = ms.swing_lows[-1]
        kind = "CHoCH" if bull else ("BOS" if bear else "CHoCH/BOS")
        out.append({
            "side": "down", "kind": kind,
            "price": float(sl.price), "ts": int(sl.timestamp),
        })
    return out


def _events_15m(candles_15m: List[dict]) -> List[dict]:
    out = []
    seen = set()
    for override in (None, 2):
        try:
            st = obs_structure(candles_15m, "15m", pivot_window_override=override) if override else obs_structure(candles_15m, "15m")
        except Exception:
            continue
        for e in st.history or []:
            et = (e.event_type or "").upper()
            if et not in ("BOS", "CHOCH", "CHoCH"):
                continue
            key = (et, e.timestamp, round(float(e.price or 0), 1))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "event": "BOS" if et == "BOS" else "CHoCH",
                "direction": e.direction,
                "timestamp": e.timestamp,
                "price": e.price,
                "reference_price": e.reference_price or e.price,
                "distance_atr": e.distance_atr,
            })
    return out[-16:]


def collect_observations(
    candles: List[dict],
    timeframe: str,
    candles_5m: Optional[List[dict]] = None,
    candles_4h: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
    candles_15m: Optional[List[dict]] = None,
) -> Dict:
    rows = []
    watchers = [
        ("market_structure", obs_structure),
        ("breakout", obs_breakout),
        ("support_resistance", obs_sr),
        ("fair_value_gap", obs_fvg),
        ("volume", obs_vol),
        ("trend", obs_trend),
        ("momentum", obs_mom),
    ]
    for name, fn in watchers:
        try:
            obs = fn(candles, timeframe)
            rows.append(obs.to_dict())
        except Exception as e:
            rows.append({"source": name, "state": "ERROR", "notes": [str(e)], "valid": False})

    hunt = None
    weather = None
    hunt_15 = candles_15m if candles_15m else (candles if timeframe == "15m" else None)
    if hunt_15 and candles_5m:
        fill = candles_5m[-1]
        live = _live_5ms(candles_5m)
        try:
            hunt = evaluate_hunt_c_fi(
                hunt_15,
                fill,
                live_5ms=live,
                candles_4h=candles_4h,
                candles_1h=candles_1h,
                candles_5m=candles_5m,
            )
        except Exception as e:
            hunt = {
                "action": "WAIT",
                "why_state": [f"hunt C-FI error: {e}"],
                "brain_version": HUNT_VERSION_C_FI,
                "ok": True,
            }
        if hunt is not None:
            hunt["structure_events_15m"] = _events_15m(hunt_15)
            hunt["breaks"] = _hunt_breaks(hunt_15)
            map_high, map_low, map_ts = _map_15(hunt_15, candles_5m)
            pack = dict(hunt.get("hunt") or {})
            pack["map_high"] = map_high
            pack["map_low"] = map_low
            pack["map_ts"] = map_ts
            hunt["hunt"] = pack
            hunt["map_high"] = map_high
            hunt["map_low"] = map_low
            hunt["map_ts"] = map_ts
    if candles_4h:
        try:
            weather = classify(candles_4h, candles_1h or [])
        except Exception:
            weather = None
    return {"observations": rows, "hunt": hunt, "weather": weather}
