"""LLM access.  One tiny interface so the pipeline is testable without a network; OpenAI is the live backend."""
import os
from typing import Any, Dict, List, Optional


class LLMError(RuntimeError):
    pass


class OpenAILLM:
    """Structured-output chat call (JSON schema, strict).  Returns the raw JSON string."""

    def __init__(self, model: str, timeout: int = 90):
        self.model, self.timeout = model, timeout

    def complete(self, messages: List[Dict[str, str]], json_schema: Dict[str, Any]) -> str:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY not set")
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=self.timeout)
        try:
            resp = client.chat.completions.create(
                model=self.model, messages=messages,
                response_format={"type": "json_schema", "json_schema": json_schema},
                max_completion_tokens=900)
        except TypeError:
            resp = client.chat.completions.create(model=self.model, messages=messages,
                                                  response_format={"type": "json_schema", "json_schema": json_schema},
                                                  max_tokens=900)
        except Exception as e:
            raise LLMError(str(e))
        return resp.choices[0].message.content or ""


class FakeLLM:
    """Scripted responses for tests: pass a list of raw JSON strings / dicts, or a callable(messages, schema)."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def complete(self, messages, json_schema):
        self.calls.append((messages, json_schema["name"]))
        if callable(self.script):
            return self.script(messages, json_schema)
        if not self.script:
            raise LLMError("script exhausted")
        return self.script.pop(0)
