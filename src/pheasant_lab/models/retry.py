"""Retrying a model call that failed for a reason that will pass.

A hosted model answers ``429`` under load, ``5xx`` when it is having a bad
minute, and times out or drops a connection on a bad network. None of those
says anything about the question, and before this a single one failed the
branch or the answer it belonged to - so the flakier the provider was, the
emptier the corpus and the worse the arm looked, which is a measurement of the
provider's afternoon rather than of the arm.

So a transient failure is retried, with exponential backoff and a server's own
``Retry-After`` honoured when it gives one (a ``0`` included: "immediately" is
an instruction, not a missing value). Every retry is recorded in the error
ledger with its backoff, because a retry that worked is a fact about
reliability. What is *not* retried: an authentication failure, a malformed
request, or a reply in the wrong shape - asking again cannot fix any of them.

Budget: the retries happen inside the one reservation the caller already
holds, which was sized for the worst case of a single call. A provider that
bills a request it then fails with ``429``/``5xx`` is outside what this can
see; the ledger reconciles what each *successful* call reports.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .base import ModelProvider, ModelRequest, ModelResponse

#: Attempts in total, including the first.
MAX_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 30.0
TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def transient(exc: BaseException) -> bool:
    """Would the same request plausibly succeed if sent again?"""

    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a dependency
        return isinstance(exc, TimeoutError | ConnectionError)
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    return isinstance(exc, httpx.TimeoutException | httpx.TransportError | TimeoutError)


def retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class RetryingProvider(ModelProvider):
    """A provider with the lab's retry policy around it."""

    def __init__(
        self,
        inner: ModelProvider,
        *,
        on_retry: Callable[[BaseException, int, float], None] | None = None,
        attempts: int = MAX_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(spec=inner.spec, role=inner.role, api_key=inner.api_key)
        self.inner = inner
        self.name = inner.name
        self.on_retry = on_retry
        self.attempts = max(1, attempts)
        self.sleep = sleep

    def complete(self, request: ModelRequest) -> ModelResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self.inner.complete(request)
            except Exception as exc:
                if attempt >= self.attempts or not transient(exc):
                    raise
                hinted = retry_after(exc)
                wait = (
                    hinted
                    if hinted is not None
                    else min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2 ** (attempt - 1))
                )
                if self.on_retry is not None:
                    self.on_retry(exc, attempt, wait)
                self.sleep(wait)

    def __getattr__(self, name: str) -> Any:
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)
