"""The Anthropic adapter speaks the current Messages API surface.

Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5 answer a 400 to an assistant
prefill, to ``thinking.budget_tokens`` and to the lab's ``temperature: 0``, so
each of those is asserted absent, and the cap sent is the cap reserved.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pheasant_lab.models import anthropic as anthropic_module
from pheasant_lab.models.anthropic import AnthropicProvider
from pheasant_lab.models.base import ModelRequest, StructuredOutputError
from pheasant_lab.settings import RoleModel


def _provider(model: str = "claude-opus-5-5", effort: str | None = None) -> AnthropicProvider:
    spec = RoleModel(provider="anthropic", model=model, reasoning_effort=effort)
    return AnthropicProvider(spec=spec, role="planner", api_key="test-key")


def _request() -> ModelRequest:
    return ModelRequest(
        role="planner",
        schema="plan",
        system="Plan the research.",
        user="Return the JSON object described above and nothing else.",
        max_output_tokens=3000,
        temperature=0.0,
    )


def _serve(
    monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any], seen: list[httpx.Request]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply)

    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(anthropic_module.os, "environ", {})
    monkeypatch.setattr(httpx, "Client", client)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
def test_a_request_carries_nothing_the_5_5_family_refuses(model: str) -> None:
    body = _provider(model, effort="high").request_body(_request())

    assert body["messages"] == [{"role": "user", "content": _request().user}]
    assert "temperature" not in body
    assert "thinking" not in body
    assert body["output_config"] == {"effort": "high"}
    # The budget guard reserved max_output_tokens; thinking comes out of it.
    assert body["max_tokens"] == 3000


def test_no_effort_leaves_the_model_its_default() -> None:
    body = _provider(effort=None).request_body(_request())
    assert "output_config" not in body and "thinking" not in body


@pytest.mark.parametrize(
    ("model", "thinking"),
    [
        ("claude-sonnet-5-5", {"type": "between_tools"}),
        ("claude-haiku-5-5", {"type": "disabled"}),
    ],
)
def test_none_turns_thinking_off_in_each_models_own_spelling(
    model: str, thinking: dict[str, str]
) -> None:
    body = _provider(model, effort="none").request_body(_request())
    assert body["thinking"] == thinking
    assert "output_config" not in body


def test_a_level_no_claude_model_takes_is_refused_before_the_wire() -> None:
    with pytest.raises(ValueError, match="minimal"):
        _provider(effort="minimal").request_body(_request())


def test_a_reply_is_parsed_and_its_usage_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    reply = {
        "id": "msg_1",
        "model": "claude-opus-5-5",
        "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": '```json\n{"subtopics": ["a"]}\n```'},
        ],
        "usage": {"input_tokens": 120, "cache_read_input_tokens": 30, "output_tokens": 40},
    }
    _serve(monkeypatch, reply, seen)

    response = _provider(effort="medium").complete(_request())

    assert response.data == {"subtopics": ["a"]}
    assert (response.input_tokens, response.output_tokens) == (150, 40)
    assert response.finish_reason == "end_turn"
    sent = seen[0]
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == "test-key"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(sent.content)["model"] == "claude-opus-5-5"


def test_a_refusal_is_a_failure_with_its_category(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": "cyber"},
        "content": [],
    }
    _serve(monkeypatch, reply, [])
    with pytest.raises(StructuredOutputError, match="refusal: cyber"):
        _provider().complete(_request())


def test_a_cap_spent_on_thinking_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": ""}]}
    _serve(monkeypatch, reply, [])
    with pytest.raises(StructuredOutputError, match="3000-token cap"):
        _provider(effort="max").complete(_request())
