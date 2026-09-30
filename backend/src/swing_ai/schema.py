"""Strict structured output.  The AI may only answer in these shapes; anything else is rejected."""
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

ENTRY_DECISIONS = ("LONG", "SHORT", "NO_TRADE")
ENTRY_TYPES = ("MARKET", "LIMIT")
CONTEXT_TFS = ("4H", "1H", "15M")
THESIS_STATUS = ("VALID", "WEAKENING", "INVALID", "OPPOSITE_STRONG")
MANAGE_ACTIONS = ("HOLD", "MOVE_SL", "EXIT")


class SchemaError(ValueError):
    pass


def entry_json_schema() -> Dict[str, Any]:
    """JSON Schema for OpenAI structured outputs (strict: every key required, nullable where optional)."""
    num_or_null = {"type": ["number", "null"]}
    return {
        "name": "swing_entry_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision", "confidence", "entry", "entry_type", "sl", "tp", "thesis", "invalidation",
                         "invalidation_price", "context_timeframe"],
            "properties": {
                "decision": {"type": "string", "enum": list(ENTRY_DECISIONS)},
                "confidence": {"type": "number"},
                "entry": num_or_null,
                "entry_type": {"type": ["string", "null"], "enum": list(ENTRY_TYPES) + [None]},
                "sl": num_or_null,
                "tp": num_or_null,
                "thesis": {"type": "string"},
                "invalidation": {"type": "string"},
                "invalidation_price": num_or_null,
                "context_timeframe": {"type": "string", "enum": list(CONTEXT_TFS)},
            },
        },
    }


def manage_json_schema() -> Dict[str, Any]:
    return {
        "name": "swing_management_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["thesis_status", "action", "new_sl", "confidence", "reason", "reversal_candidate"],
            "properties": {
                "thesis_status": {"type": "string", "enum": list(THESIS_STATUS)},
                "action": {"type": "string", "enum": list(MANAGE_ACTIONS)},
                "new_sl": {"type": ["number", "null"]},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
                "reversal_candidate": {"type": "boolean"},
            },
        },
    }


@dataclass
class EntryDecision:
    decision: str = "NO_TRADE"
    confidence: float = 0.0
    entry: Optional[float] = None
    entry_type: Optional[str] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    thesis: str = ""
    invalidation: str = ""
    invalidation_price: Optional[float] = None
    context_timeframe: str = "1H"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManageDecision:
    thesis_status: str = "VALID"
    action: str = "HOLD"
    new_sl: Optional[float] = None
    confidence: float = 0.0
    reason: str = ""
    reversal_candidate: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _loads(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise SchemaError("response is not JSON")
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        data = json.loads(text)
    except Exception as e:
        raise SchemaError(f"invalid JSON: {e}")
    if not isinstance(data, dict):
        raise SchemaError("JSON root must be an object")
    return data


def _num(data: Dict[str, Any], key: str, required: bool = False) -> Optional[float]:
    v = data.get(key)
    if v is None:
        if required:
            raise SchemaError(f"{key} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SchemaError(f"{key} must be a number")
    if v != v or v in (float("inf"), float("-inf")):
        raise SchemaError(f"{key} must be finite")
    return float(v)


def parse_entry(raw: Any) -> EntryDecision:
    d = _loads(raw)
    dec = d.get("decision")
    if dec not in ENTRY_DECISIONS:
        raise SchemaError(f"decision must be one of {ENTRY_DECISIONS}")
    conf = _num(d, "confidence", required=True)
    if not 0.0 <= conf <= 1.0:
        raise SchemaError("confidence must be within 0..1")
    tf = d.get("context_timeframe", "1H")
    if tf not in CONTEXT_TFS:
        raise SchemaError(f"context_timeframe must be one of {CONTEXT_TFS}")
    out = EntryDecision(decision=dec, confidence=conf, thesis=str(d.get("thesis") or "")[:1200],
                        invalidation=str(d.get("invalidation") or "")[:600], context_timeframe=tf,
                        invalidation_price=_num(d, "invalidation_price"))
    if dec == "NO_TRADE":
        return out                                         # NO_TRADE never needs levels
    out.entry, out.sl, out.tp = _num(d, "entry", True), _num(d, "sl", True), _num(d, "tp", True)
    et = d.get("entry_type")
    if et not in ENTRY_TYPES:
        raise SchemaError(f"entry_type must be one of {ENTRY_TYPES} for a trade")
    out.entry_type = et
    if not out.thesis.strip() or not out.invalidation.strip():
        raise SchemaError("a trade needs a thesis and an invalidation condition")
    return out


def parse_manage(raw: Any) -> ManageDecision:
    d = _loads(raw)
    ts, act = d.get("thesis_status"), d.get("action")
    if ts not in THESIS_STATUS:
        raise SchemaError(f"thesis_status must be one of {THESIS_STATUS}")
    if act not in MANAGE_ACTIONS:
        raise SchemaError(f"action must be one of {MANAGE_ACTIONS}")
    conf = _num(d, "confidence", required=True)
    if not 0.0 <= conf <= 1.0:
        raise SchemaError("confidence must be within 0..1")
    out = ManageDecision(thesis_status=ts, action=act, new_sl=_num(d, "new_sl"), confidence=conf,
                         reason=str(d.get("reason") or "")[:800], reversal_candidate=bool(d.get("reversal_candidate")))
    if act == "MOVE_SL" and out.new_sl is None:
        raise SchemaError("MOVE_SL needs new_sl")
    return out
