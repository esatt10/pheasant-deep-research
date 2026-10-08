"""Hosted requests satisfy JSON mode without hiding prompt transformations."""

import json

import httpx
import pytest

from pheasant_lab.models.base import ModelRequest
from pheasant_lab.models.openai import OpenAIProvider
from pheasant_lab.settings import RoleModel


def test_json_mode_requirement_is_sent_and_included_in_recorded_prompt(monkeypatch) -> None:
    request = ModelRequest(role="auditor", schema="audit", system="Summarise.", user="Audit this.")

    def respond(wire: httpx.Request) -> httpx.Response:
        body = json.loads(wire.content)
        assert body["text"]["format"]["type"] == "json_object"
        assert "json" in body["input"].lower()
        assert body["input"] in request.prompt_text
        return httpx.Response(200, json={"output_text": '{"narrative": "Coverage is incomplete."}'})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)
    )
    provider = OpenAIProvider(spec=RoleModel(model="gpt-6-luna"), role="auditor", api_key="fake")
    assert provider.complete(request).data["narrative"] == "Coverage is incomplete."


def test_api_refusal_exposes_the_server_explanation_and_preserves_status(monkeypatch) -> None:
    def refuse(wire: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid request parameter."}})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(refuse), **kwargs)
    )
    provider = OpenAIProvider(spec=RoleModel(model="gpt-6-luna"), role="auditor", api_key="fake")
    with pytest.raises(httpx.HTTPStatusError, match="Invalid request parameter") as caught:
        provider.complete(ModelRequest(role="auditor", schema="audit", system="s", user="u"))
    assert caught.value.response.status_code == 400
