"""A transient model failure is retried; a permanent one is not."""

from __future__ import annotations

import httpx
import pytest

from pheasant_lab.models.base import ModelProvider, ModelRequest, ModelResponse
from pheasant_lab.models.retry import RetryingProvider
from pheasant_lab.settings import RoleModel


def _status(code: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    request = httpx.Request("POST", "https://models.example/v1")
    response = httpx.Response(code, headers=headers, request=request)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


class Flaky(ModelProvider):
    name = "flaky"

    def __init__(self, failures: list[BaseException]) -> None:
        super().__init__(spec=RoleModel(provider="flaky", model="flaky-1"), role="planner")
        self.failures = list(failures)
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ModelResponse(
            data={"ok": True},
            text="{}",
            provider="flaky",
            model="flaky-1",
            input_tokens=1,
            output_tokens=1,
        )


REQUEST = ModelRequest(role="planner", schema="plan", system="s", user="u")


def test_rate_limits_and_outages_are_retried_with_backoff() -> None:
    inner = Flaky([_status(429), _status(503), httpx.ReadTimeout("slow")])
    waits: list[float] = []
    seen: list[tuple[str, int, float]] = []
    provider = RetryingProvider(
        inner, sleep=waits.append, on_retry=lambda e, a, w: seen.append((type(e).__name__, a, w))
    )
    assert provider.complete(REQUEST).data == {"ok": True}
    assert inner.calls == 4
    assert waits == [2.0, 4.0, 8.0]
    assert [a for _n, a, _w in seen] == [1, 2, 3]


def test_a_retry_after_of_zero_means_now() -> None:
    inner = Flaky([_status(429, retry_after="0")])
    waits: list[float] = []
    RetryingProvider(inner, sleep=waits.append).complete(REQUEST)
    assert waits == [0.0]


@pytest.mark.parametrize("failure", [_status(401), _status(400), ValueError("not the shape")])
def test_what_asking_again_cannot_fix_is_not_retried(failure: BaseException) -> None:
    inner = Flaky([failure])
    with pytest.raises(type(failure)):
        RetryingProvider(inner, sleep=lambda _s: None).complete(REQUEST)
    assert inner.calls == 1


def test_the_attempt_limit_holds() -> None:
    inner = Flaky([_status(503)] * 10)
    with pytest.raises(httpx.HTTPStatusError):
        RetryingProvider(inner, attempts=4, sleep=lambda _s: None).complete(REQUEST)
    assert inner.calls == 4
