"""LLM access.  One tiny interface so the pipeline is testable without a network; OpenAI is the live backend."""
import os
from typing import Any, Dict, List, Optional


class LLMError(RuntimeError):
    pass


class OpenAILLM:
    """Structured-output chat call (JSON schema, strict).  Returns the raw JSON string.

    GPT-5-class models spend part of `max_completion_tokens` on internal reasoning, so the cap must leave room for both the
    reasoning and the full JSON reply (about 1.5k tokens of analysis).  A reply that hit the cap is an error, never parsed."""

    def __init__(self, model: str, timeout: int = 180, max_tokens: Optional[int] = None):
        self.model, self.timeout = model, timeout
        self.reasoning: Optional[str] = None          # low | medium | high; None = env / model default
        self.last_usage: Optional[Dict[str, int]] = None
        self.max_tokens = int(max_tokens or os.environ.get("SWING_AI_MAX_TOKENS", "10000"))

    def complete(self, messages: List[Dict[str, str]], json_schema: Dict[str, Any]) -> str:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY not set")
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=self.timeout)
        kw = dict(model=self.model, messages=messages, response_format={"type": "json_schema", "json_schema": json_schema})
        self.last_usage = None
        effort = self.reasoning or os.environ.get("SWING_AI_REASONING")            # optional: low | medium | high
        if effort:
            kw["reasoning_effort"] = effort
        try:
            resp = client.chat.completions.create(max_completion_tokens=self.max_tokens, **kw)
        except TypeError:
            resp = client.chat.completions.create(max_tokens=self.max_tokens, **kw)
        except Exception as e:
            raise LLMError(str(e))
        u = getattr(resp, "usage", None)
        if u is not None:                                          # tokens actually billed, kept for the cost meter
            details = getattr(u, "prompt_tokens_details", None)
            self.last_usage = {"input": int(getattr(u, "prompt_tokens", 0) or 0), "cached": int(getattr(details, "cached_tokens", 0) or 0),
                               "output": int(getattr(u, "completion_tokens", 0) or 0)}
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMError(f"reply truncated at {self.max_tokens} tokens (finish_reason=length) - raise SWING_AI_MAX_TOKENS")
        return choice.message.content or ""


class FakeLLM:
    """Scripted responses for tests: pass a list of raw JSON strings / dicts, or a callable(messages, schema)."""

    def __init__(self, script, usage=None):
        self.script, self.calls, self.last_usage, self._usage = script, [], None, usage

    def complete(self, messages, json_schema):
        self.calls.append((messages, json_schema["name"]))
        self.last_usage = dict(self._usage) if self._usage else None
        if callable(self.script):
            return self.script(messages, json_schema)
        if not self.script:
            raise LLMError("script exhausted")
        return self.script.pop(0)
