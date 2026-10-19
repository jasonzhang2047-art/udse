"""Claude provider adapter: request shape, refusal handling and call records (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from udse.llm.provider import DEFAULT_MODEL, FALLBACK_BETA, AnthropicProvider, LLMCall, LLMRefused  # noqa: E402


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _FakeClient:
    def __init__(self, msg):
        self.calls = []
        outer = self

        class _Msgs:
            def stream(self, **kw):
                outer.calls.append(kw)
                return _Stream(msg)

        self.beta = SimpleNamespace(messages=_Msgs())


def _msg(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, model=DEFAULT_MODEL, stop_details=SimpleNamespace(category="cyber"),
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=120, output_tokens=30))


def test_request_uses_opus_effort_structured_output_and_fallbacks():
    fake = _FakeClient(_msg('{"answer": 1}'))
    p = AnthropicProvider(client=fake)
    res = p.complete_json(LLMCall(task="t", system="sys", user="u", schema={"type": "object"}, effort="high", tainted_by_simulation=True))
    kw = fake.calls[0]
    assert kw["model"] == DEFAULT_MODEL == "claude-opus-5-5"
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "thinking" not in kw  # adaptive thinking is the model default
    assert res.data == {"answer": 1} and res.usage["calls"] == 1
    assert res.record["tainted_by_simulation"] and res.record["prompt_hash"].startswith("sha256:")


def test_refusal_is_surfaced_not_parsed():
    p = AnthropicProvider(client=_FakeClient(_msg("", stop="refusal")))
    with pytest.raises(LLMRefused):
        p.complete_json(LLMCall(task="t", system="s", user="u"))
