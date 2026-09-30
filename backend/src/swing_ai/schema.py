"""Strict structured output.  GPT does all market analysis and may only answer in these shapes."""
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

ENTRY_DECISIONS = ("LONG", "SHORT", "NO_TRADE")
ENTRY_TYPES = ("MARKET", "LIMIT", "STOP")
MARKET_STATES = ("TRENDING_UP", "TRENDING_DOWN", "RANGE", "TRANSITION", "UNCLEAR")
THESIS_STATUS = ("VALID", "WEAKENING", "INVALID", "OPPOSITE_STRONG")
# why the AI closed a trade, taken from the thesis_status GPT reported with its EXIT (AI_EXIT = legacy rows written before this split)
AI_EXIT_BY_STATUS = {"INVALID": "AI_INVALID", "OPPOSITE_STRONG": "AI_OPPOSITE", "WEAKENING": "AI_WEAKENING", "VALID": "AI_EXIT_VALID"}
AI_EXIT_REASONS = ("AI_EXIT",) + tuple(AI_EXIT_BY_STATUS.values())
MANAGE_ACTIONS = ("HOLD", "MOVE_SL", "EXIT")
WAKE_DIRECTIONS = ("ABOVE", "BELOW")
MAX_WAKE_LEVELS = 4
ANALYSIS_FIELDS = ("daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis", "structure_analysis", "entry_analysis")


class SchemaError(ValueError):
    pass


def _wake_schema() -> Dict[str, Any]:
    return {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                       "required": ["price", "direction", "reason"],
                                       "properties": {"price": {"type": "number"},
                                                      "direction": {"type": "string", "enum": list(WAKE_DIRECTIONS)},
                                                      "reason": {"type": "string"}}}}


def entry_json_schema() -> Dict[str, Any]:
    """JSON Schema for OpenAI structured outputs (strict: every key required, nullable where optional)."""
    n = {"type": ["number", "null"]}
    props = {
        "decision": {"type": "string", "enum": list(ENTRY_DECISIONS)},
        "confidence": {"type": "number"},
        "headline": {"type": "string"},
        "market_state": {"type": "string", "enum": list(MARKET_STATES)},
        "daily_analysis": {"type": "string"}, "h4_analysis": {"type": "string"}, "h1_analysis": {"type": "string"},
        "m15_analysis": {"type": "string"}, "structure_analysis": {"type": "string"}, "entry_analysis": {"type": "string"},
        "entry_type": {"type": ["string", "null"], "enum": list(ENTRY_TYPES) + [None]},
        "entry": n, "sl": n, "tp": n, "expected_hold_hours": n,
        "thesis": {"type": "string"}, "invalidation": {"type": "string"}, "invalidation_price": n,
        "wake_levels": _wake_schema(),
    }
    return {"name": "swing_entry_decision", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}}


def manage_json_schema() -> Dict[str, Any]:
    props = {"thesis_status": {"type": "string", "enum": list(THESIS_STATUS)},
             "action": {"type": "string", "enum": list(MANAGE_ACTIONS)},
             "new_sl": {"type": ["number", "null"]}, "confidence": {"type": "number"},
             "reason": {"type": "string"}, "reversal_candidate": {"type": "boolean"}, "wake_levels": _wake_schema()}
    return {"name": "swing_management_decision", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}}


@dataclass
class EntryDecision:
    decision: str = "NO_TRADE"
    confidence: float = 0.0
    headline: str = ""
    market_state: str = "UNCLEAR"
    daily_analysis: str = ""
    h4_analysis: str = ""
    h1_analysis: str = ""
    m15_analysis: str = ""
    structure_analysis: str = ""
    entry_analysis: str = ""
    entry_type: Optional[str] = None
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    expected_hold_hours: Optional[float] = None
    thesis: str = ""
    invalidation: str = ""
    invalidation_price: Optional[float] = None
    wake_levels: List[Dict[str, Any]] = field(default_factory=list)

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
    wake_levels: List[Dict[str, Any]] = field(default_factory=list)

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


def _text(d: Dict[str, Any], key: str, required: bool, limit: int = 1500) -> str:
    v = d.get(key)
    if not isinstance(v, str) or (required and not v.strip()):
        raise SchemaError(f"{key} must be a non-empty string" if required else f"{key} must be a string")
    return v.strip()[:limit]


def _wake_levels(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for it in (d.get("wake_levels") or [])[:MAX_WAKE_LEVELS]:
        if not isinstance(it, dict) or it.get("direction") not in WAKE_DIRECTIONS:
            raise SchemaError("wake_levels items need price, direction (ABOVE|BELOW) and reason")
        out.append({"price": _num(it, "price", True), "direction": it["direction"], "reason": str(it.get("reason") or "")[:200]})
    return out


def parse_entry(raw: Any) -> EntryDecision:
    d = _loads(raw)
    dec = d.get("decision")
    if dec not in ENTRY_DECISIONS:
        raise SchemaError(f"decision must be one of {ENTRY_DECISIONS}")
    conf = _num(d, "confidence", required=True)
    if not 0.0 <= conf <= 1.0:
        raise SchemaError("confidence must be within 0..1")
    if d.get("market_state") not in MARKET_STATES:
        raise SchemaError(f"market_state must be one of {MARKET_STATES}")
    out = EntryDecision(decision=dec, confidence=conf, market_state=d["market_state"], headline=_text(d, "headline", True, 200),
                        thesis=_text(d, "thesis", True), invalidation=_text(d, "invalidation", dec != "NO_TRADE", 800),
                        invalidation_price=_num(d, "invalidation_price"), wake_levels=_wake_levels(d))
    for f in ANALYSIS_FIELDS:                        # the AI must always show its analysis, trade or not
        setattr(out, f, _text(d, f, True))
    if dec == "NO_TRADE":
        return out                                    # NO_TRADE never needs levels (0 / null both mean "none")
    out.entry, out.sl, out.tp = _num(d, "entry", True), _num(d, "sl", True), _num(d, "tp", True)
    out.expected_hold_hours = _num(d, "expected_hold_hours", True)
    if out.expected_hold_hours <= 0:
        raise SchemaError("expected_hold_hours must be positive for a trade")
    et = d.get("entry_type")
    if et not in ENTRY_TYPES:
        raise SchemaError(f"entry_type must be one of {ENTRY_TYPES} for a trade")
    out.entry_type = et
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
                         reason=_text(d, "reason", True, 1200), reversal_candidate=bool(d.get("reversal_candidate")),
                         wake_levels=_wake_levels(d))
    if act == "MOVE_SL" and out.new_sl is None:
        raise SchemaError("MOVE_SL needs new_sl")
    return out
