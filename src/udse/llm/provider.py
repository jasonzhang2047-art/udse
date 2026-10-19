"""LLM provider adapters (§2.2 "provider adapters can wrap LLM APIs").

Every call is recorded with provider/model/prompt hash/output hash/usage (§6.2, §14.8). Calls whose
input contains Layer C simulation content are marked ``tainted_by_simulation``; anything derived
from such a call can never pass the evidence-ingestion gate (§3.3 poisoning test).

The default model is Claude Opus 5.5 with adaptive thinking (always on for this model), an explicit
effort level per task, streaming for long outputs, refusal handling, and server-side refusal
fallbacks (``fallbacks: "default"``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from ..common import canonical_json, new_id, sha256_hex

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(RuntimeError):
    pass


class LLMRefused(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    pass


@dataclass
class LLMCall:
    task: str
    system: str
    user: str
    schema: dict[str, Any] | None = None  # strict JSON schema → output_config.format
    effort: str = "medium"
    max_tokens: int = 16000
    tainted_by_simulation: bool = False
    input_refs: list[str] = field(default_factory=list)


@dataclass
class LLMResult:
    data: Any
    text: str
    usage: dict[str, int]
    record: dict[str, Any]


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, call: LLMCall) -> LLMResult: ...


def _record(provider: str, model: str, call: LLMCall, text: str, usage: dict[str, int]) -> dict[str, Any]:
    return {
        "call_id": new_id("llm"),
        "task": call.task,
        "provider": provider,
        "model": model,
        "prompt_hash": "sha256:" + sha256_hex(canonical_json({"system": call.system, "user": call.user, "schema": call.schema})),
        "output_hash": "sha256:" + sha256_hex(text),
        "effort": call.effort,
        "usage": usage,
        "input_refs": call.input_refs,
        "tainted_by_simulation": call.tainted_by_simulation,
        "output": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_json(text: str) -> Any:
    """Parse a JSON object from model text (tolerates a fenced block)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError as e:
                raise LLMOutputError(f"model output is not valid JSON: {e}") from e
        raise LLMOutputError("model output contained no JSON object")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, client: Any = None):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover - optional dependency
            raise LLMUnavailable("install the 'llm' extra: uv sync --extra llm") from e
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("UDSE_LLM_MODEL", DEFAULT_MODEL)

    def complete_json(self, call: LLMCall) -> LLMResult:
        anthropic = self._anthropic
        output_config: dict[str, Any] = {"effort": call.effort}
        if call.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": call.schema}
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=call.max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config=output_config,
                system=call.system,
                messages=[{"role": "user", "content": call.user}],
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise LLMUnavailable(f"rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMUnavailable(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailable(f"connection error: {e}") from e
        if msg.stop_reason == "refusal":
            category = getattr(getattr(msg, "stop_details", None), "category", None)
            raise LLMRefused(f"model declined the {call.task} request (category={category})")
        if msg.stop_reason == "max_tokens":
            raise LLMOutputError(f"{call.task}: output truncated at max_tokens={call.max_tokens}")
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        usage = {
            "input_tokens": int(getattr(msg.usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(msg.usage, "output_tokens", 0) or 0),
            "calls": 1,
        }
        data = extract_json(text)
        return LLMResult(data=data, text=text, usage=usage, record=_record(self.name, msg.model, call, text, usage))


class ScriptedProvider:
    """Deterministic provider for tests and offline demos: returns scripted outputs per task."""

    name = "scripted"

    def __init__(self, script: dict[str, list[Any] | Callable[[LLMCall], Any]], model: str = "scripted-model"):
        self.script = script
        self.model = model
        self.calls: list[LLMCall] = []

    def complete_json(self, call: LLMCall) -> LLMResult:
        self.calls.append(call)
        entry = self.script.get(call.task)
        if entry is None:
            raise LLMUnavailable(f"no scripted output for task {call.task}")
        if callable(entry):
            data = entry(call)
        else:
            if not entry:
                raise LLMUnavailable(f"scripted outputs for {call.task} exhausted")
            data = entry.pop(0)
        if isinstance(data, Exception):
            raise data
        text = data if isinstance(data, str) else json.dumps(data)
        usage = {"input_tokens": len(call.user) // 4, "output_tokens": len(text) // 4, "calls": 1}
        parsed = extract_json(text) if isinstance(data, str) else data
        return LLMResult(data=parsed, text=text, usage=usage, record=_record(self.name, self.model, call, text, usage))


def provider_from_env() -> LLMProvider | None:
    """Anthropic provider when explicitly enabled (UDSE_LLM=anthropic); otherwise offline mode."""
    if os.environ.get("UDSE_LLM", "").lower() != "anthropic":
        return None
    try:
        return AnthropicProvider()
    except LLMUnavailable:
        return None
