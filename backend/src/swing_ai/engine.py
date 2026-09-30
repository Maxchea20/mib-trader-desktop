"""One AI review: build the messages, call the model with a strict schema, parse or reject, never guess."""
import time
from typing import Any, Dict, Optional

from . import prompts, schema
from .llm import LLMError


def _call(llm, messages, json_schema, parser):
    """Returns (parsed_or_None, raw, error_or_None, latency_ms).  One repair retry on a schema failure."""
    t0 = time.time()
    raw, err = "", None
    for _ in (0, 1):
        try:
            raw = llm.complete(messages, json_schema)
            return parser(raw), raw, None, int((time.time() - t0) * 1000)
        except schema.SchemaError as e:
            err = f"schema: {e}"
            messages = messages + [{"role": "assistant", "content": raw or "{}"},
                                   {"role": "user", "content": f"Your reply was rejected ({e}). Reply again with ONLY valid JSON matching the schema."}]
        except LLMError as e:
            return None, raw, f"llm: {e}", int((time.time() - t0) * 1000)
        except Exception as e:                      # network / SDK errors are never trades
            return None, raw, f"llm: {e}", int((time.time() - t0) * 1000)
    return None, raw, err, int((time.time() - t0) * 1000)


def review_entry(llm, cfg, snapshot: Dict[str, Any], wake: Optional[Dict[str, Any]], previous: Optional[Dict[str, Any]]):
    return _call(llm, prompts.entry_messages(cfg, snapshot, wake, previous), schema.entry_json_schema(), schema.parse_entry)


def review_manage(llm, cfg, snapshot: Dict[str, Any], wake: Optional[Dict[str, Any]]):
    return _call(llm, prompts.manage_messages(cfg, snapshot, wake), schema.manage_json_schema(), schema.parse_manage)
