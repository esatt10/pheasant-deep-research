"""OpenAI provider, over the Responses API.

Written against the HTTP surface with ``httpx`` rather than the SDK: the lab
measures a server, and inheriting an SDK's retry policy and error translation
would fold the SDK into the measurement. Retries live in the caller, where the
budget guard and the error ledger can see them.
"""

from __future__ import annotations

import os
from typing import Any

from .base import ModelProvider, ModelRequest, ModelResponse, StructuredOutputError

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.api_key or os.environ.get("OPENAI_API_KEY")

    def complete(self, request: ModelRequest) -> ModelResponse:
        import httpx

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set; doctor should have refused this run")

        body: dict[str, Any] = {
            "model": self.model,
            "instructions": request.system,
            "input": request.user,
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "text": {"format": {"type": "json_object"}},
        }
        if self.spec.reasoning_effort:
            body["reasoning"] = {"effort": self.spec.reasoning_effort}
            # Reasoning models reject an explicit temperature; the manifest
            # still records the configured value, and this is where it stops
            # being sent.
            body.pop("temperature", None)

        with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            response = client.post(
                f"{self.base_url}/responses",
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                json=body,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                try:
                    detail = response.json().get("error", {}).get("message")
                except (ValueError, AttributeError):
                    detail = None
                if detail:
                    raise httpx.HTTPStatusError(
                        f"OpenAI HTTP {response.status_code}: {detail}",
                        request=exc.request,
                        response=exc.response,
                    ) from exc
                raise
            payload = response.json()

        text = _output_text(payload)
        usage = payload.get("usage") or {}
        try:
            if payload.get("status") == "incomplete":
                reason = (payload.get("incomplete_details") or {}).get("reason", "unknown")
                raise StructuredOutputError(f"OpenAI response incomplete: {reason}")
            data = self.parse_structured(text)
        except StructuredOutputError as exc:
            # A paid response may be unusable. Keep its billed usage available
            # to the reservation guard even when parsing refuses it.
            exc.input_tokens = int(usage.get("input_tokens") or 0)
            exc.output_tokens = int(usage.get("output_tokens") or 0)
            raise
        return ModelResponse(
            data=data,
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=str(payload.get("status") or "completed"),
            raw={"id": payload.get("id"), "model": payload.get("model")},
        )


def _output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in payload.get("output") or []:
        for block in item.get("content") or []:
            if block.get("type") in {"output_text", "text"} and block.get("text"):
                chunks.append(str(block["text"]))
    return "\n".join(chunks)
