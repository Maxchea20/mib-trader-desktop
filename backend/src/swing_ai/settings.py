"""Runtime settings for Swing AI, edited from the UI and persisted next to the market database.

  enabled     AI reviews on/off (paper positions are still tracked while off)
  mode        PAPER | LIVE  - LIVE is only a *requested* mode: there is no live order path in Swing AI, so the effective mode is
              always PAPER until a live executor is built, reviewed and explicitly enabled.
  risk_pct, max_leverage, max_position_usd   the limits the deterministic safety layer uses for sizing
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

MODES = ("PAPER", "LIVE")
BOUNDS = {"risk_pct": (0.1, 2.0), "max_leverage": (1.0, 10.0), "max_position_usd": (100.0, 1_000_000.0),
          "heartbeat_minutes": (5.0, 240.0), "management_minutes": (1.0, 60.0)}
REASONING = ("default", "low", "medium", "high")
CONTEXTS = ("FULL", "COMPACT", "LEAN")
SAFETY = ("STRICT", "RELAXED")
MODEL_RE = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$"
DEFAULTS = {"model": None, "enabled": None, "mode": "PAPER", "risk_pct": 1.0, "max_leverage": 5.0, "max_position_usd": 50_000.0,
            "heartbeat_minutes": 15.0, "management_minutes": 5.0, "reasoning": "default", "context": "FULL", "safety": "RELAXED"}
LIVE_EXECUTION_IMPLEMENTED = False          # flipped only by a reviewed live-executor change, never by a setting


def _path() -> Path:
    db = os.environ.get("MARKET_DB_PATH")
    base = Path(db).resolve().parent if db else Path(__file__).resolve().parent.parent.parent / "data"
    return base / "swing_ai_settings.json"


def load() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data and data[k] is not None:
                    out[k] = data[k]
    except Exception:
        pass
    if out["enabled"] is None:                                   # never touched in the UI: fall back to the env flag
        out["enabled"] = os.environ.get("SWING_AI_ENABLED", "").strip().lower() in ("1", "true", "yes")
    return out


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    if "enabled" in payload and payload["enabled"] is not None:
        if not isinstance(payload["enabled"], bool):
            raise ValueError("enabled must be true or false")
        clean["enabled"] = payload["enabled"]
    if "mode" in payload and payload["mode"] is not None:
        if payload["mode"] not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        clean["mode"] = payload["mode"]
    if "model" in payload and payload["model"] is not None:
        import re
        if not isinstance(payload["model"], str) or not re.match(MODEL_RE, payload["model"]):
            raise ValueError("model must be a model name such as gpt-5.4-mini")
        clean["model"] = payload["model"]
    if "reasoning" in payload and payload["reasoning"] is not None:
        if payload["reasoning"] not in REASONING:
            raise ValueError(f"reasoning must be one of {REASONING}")
        clean["reasoning"] = payload["reasoning"]
    if "safety" in payload and payload["safety"] is not None:
        if payload["safety"] not in SAFETY:
            raise ValueError(f"safety must be one of {SAFETY}")
        clean["safety"] = payload["safety"]
    if "context" in payload and payload["context"] is not None:
        if payload["context"] not in CONTEXTS:
            raise ValueError(f"context must be one of {CONTEXTS}")
        clean["context"] = payload["context"]
    for k, (lo, hi) in BOUNDS.items():
        if k in payload and payload[k] is not None:
            v = payload[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            clean[k] = float(v)
    return clean


def save(update: Dict[str, Any]) -> Dict[str, Any]:
    cur = load()
    cur.update(validate(update))
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return cur


def effective_mode(s: Optional[Dict[str, Any]] = None) -> str:
    """What the engine actually does.  Always PAPER while there is no live executor."""
    s = s or load()
    return "LIVE" if (s["mode"] == "LIVE" and LIVE_EXECUTION_IMPLEMENTED) else "PAPER"


def effective_safety(s: Optional[Dict[str, Any]] = None) -> str:
    """RELAXED exists for paper research only: whenever the effective mode is LIVE, STRICT is always enforced."""
    s = s or load()
    return "STRICT" if effective_mode(s) == "LIVE" else s["safety"]


def apply_to_config(cfg, s: Optional[Dict[str, Any]] = None) -> None:
    s = s or load()
    cfg.safety_mode = effective_safety(s)
    cfg.risk_pct, cfg.max_leverage, cfg.max_position_usd = float(s["risk_pct"]), float(s["max_leverage"]), float(s["max_position_usd"])
    cfg.heartbeat_seconds = int(float(s["heartbeat_minutes"]) * 60)
    cfg.management_seconds = int(float(s["management_minutes"]) * 60)
    cfg.reasoning = None if s["reasoning"] == "default" else s["reasoning"]
    cfg.context = s["context"]
    if s.get("model"):                                # a model picked in the panel overrides SWING_AI_MODEL / the default
        cfg.model = s["model"]
