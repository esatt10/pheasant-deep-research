"""Anthropic provider, over the Messages API.

Same posture as the OpenAI adapter: the HTTP surface directly, so the lab's
retry policy, budget reservation and error ledger are the only ones in play.

Written against the current request surface (Claude Opus 5.5, Sonnet 5.5,
Haiku 5.5), which refuses three things the first version of this adapter sent:

* an assistant prefill - every current model answers a trailing assistant
  turn with a 400, so the JSON object is asked for in the prompt (every role
  already does) and :meth:`parse_structured` reads it;
* ``thinking.budget_tokens`` - thinking is adaptive, and depth is
  ``output_config.effort``;
* ``temperature`` - Opus 5.5 rejects any sampling parameter and Sonnet/Haiku
  5.5 any non-default one, and the lab's 0.0 is not the default. The manifest
  still records the configured value; this is where it stops being sent.

``max_tokens`` is exactly the role's ``max_output_tokens``. Thinking is billed
as output and comes out of the same cap, so the budget guard's reservation
(rule 6) is the worst case as sent; the old adapter raised the cap above what
had been reserved whenever thinking was on.
"""

from __future__ import annotations

import os
from typing import Any

from .base import ModelProvider, ModelRequest, ModelResponse, StructuredOutputError

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

#: Levels sent as ``output_config.effort``. Which ones a model takes is in
#: ``catalog.MODEL_REASONING``, which ``doctor`` checks before any spend.
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
#: How "no thinking" is spelled per model. Opus 5.5 cannot turn thinking off
#: at all (doctor refuses ``none`` for it); Sonnet 5.5 refuses ``disabled``
#: and takes ``between_tools`` instead.
_THINKING_OFF: dict[str, dict[str, str]] = {
    "claude-sonnet-5-5": {"type": "between_tools"},
}


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")

    def request_body(self, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "system": request.system,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": request.user}],
        }
        effort = self.spec.reasoning_effort
        if effort == "none":
            body["thinking"] = _THINKING_OFF.get(self.model, {"type": "disabled"})
        elif effort in EFFORT_LEVELS:
            body["output_config"] = {"effort": effort}
        elif effort is not None:
            raise ValueError(f"Anthropic models take no reasoning effort {effort!r}")
        return body

    def complete(self, request: ModelRequest) -> ModelResponse:
        import httpx

        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; doctor should have refused this run")

        body = self.request_body(request)
        with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            response = client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()

        stop_reason = str(payload.get("stop_reason") or "end_turn")
        if stop_reason == "refusal":
            # A 200 with no answer: a safety classifier declined. Not
            # retryable - the same request is declined again - and not
            # rerouted to another model, which would bill at a price the
            # budget guard never reserved.
            details = payload.get("stop_details") or {}
            category = details.get("category") or "unspecified"
            raise StructuredOutputError(f"{self.model} declined the request (refusal: {category})")
        text = "".join(
            str(block.get("text", ""))
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        if stop_reason == "max_tokens" and not text.strip():
            raise StructuredOutputError(
                f"{self.model} spent its {request.max_output_tokens}-token cap before answering; "
                "thinking bills from the same cap, so raise max_output_tokens or lower "
                "reasoning_effort"
            )
        usage = payload.get("usage") or {}
        return ModelResponse(
            data=self.parse_structured(text),
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=stop_reason,
            raw={"id": payload.get("id"), "model": payload.get("model")},
        )
