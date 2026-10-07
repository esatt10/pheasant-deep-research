"""What the configured Pheasant region says about itself, right now.

The run's trace says what the region told *the run*. This asks the region
directly, over its HTTP surface, for the two facts a run cannot see between
calls: whether this replica is ready (``/ready`` - draining, standby, an
unreachable store, a stale graph generation), what is waiting in its index
queue (``/queue``) and what it holds for the lab's source
(``/sources/{name}/overview``, pheasant >= 0.13.3). Read-only, best-effort, and never part of a run's record:
a probe from the console measures the console's view, at the console's time.

Each answer is reduced to notices with a tone, because what a person needs
from a 503 body is which of four different things is wrong.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx

PROBE_TIMEOUT = 2.5


def base_url(mcp_url: str) -> str | None:
    """``http://host:8765/mcp`` -> ``http://host:8765``; ``None`` if not HTTP."""

    parsed = urlparse(mcp_url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def probe_region(
    pheasant: dict[str, Any],
    *,
    token_env: str = "PHEASANT_API_TOKEN",
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    transport = pheasant.get("transport")
    if transport == "mock":
        return {
            "reachable": True,
            "transport": "mock",
            "mock": True,
            "ready": {"status": "ready", "role": "mock"},
            "queue": None,
            "notices": [
                {
                    "tone": "info",
                    "title": "Mock region",
                    "detail": "The in-process mock answers for this config. A mock result "
                    "measures the mock.",
                    "code": "mock",
                }
            ],
        }
    base = base_url(str(pheasant.get("url") or ""))
    if base is None:
        return {
            "reachable": None,
            "transport": transport,
            "notices": [
                {
                    "tone": "info",
                    "title": "Region not probed",
                    "detail": f"transport {transport!r} has no HTTP surface to ask.",
                    "code": "unprobed",
                }
            ],
        }
    headers = {}
    token = (environ if environ is not None else os.environ).get(token_env)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    result: dict[str, Any] = {"transport": transport, "base_url": base, "notices": []}
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT, headers=headers) as client:
            ready = client.get(f"{base}/ready")
            try:
                result["ready"] = ready.json()
            except ValueError:
                result["ready"] = {"status": "not_ready", "reason": f"HTTP {ready.status_code}"}
            queue = client.get(
                f"{base}/queue", params={"knowledge_base": pheasant.get("knowledge_base")}
            )
            result["queue"] = queue.json() if queue.status_code == 200 else None
            if queue.status_code == 404 and "Unknown knowledge base" not in queue.text:
                result["queue_unsupported"] = True
            result["inventory"] = _source_inventory(client, base, pheasant)
    except httpx.HTTPError as exc:
        result["reachable"] = False
        result["notices"].append(
            {
                "tone": "danger",
                "title": "Region unreachable",
                "detail": f"{base}: {exc.__class__.__name__}",
                "code": "unreachable",
            }
        )
        return result
    result["reachable"] = True
    result["notices"] = notices_for(result.get("ready") or {}, result.get("queue"))
    if queue.status_code == 401:
        # `/ready` is public; everything else on a region with
        # `security.api_auth` is not. A probe that swallowed the 401 would
        # show a green chip over a region every run is about to be refused by.
        result["auth_required"] = True
        result["notices"].insert(
            0,
            {
                "tone": "danger",
                "title": "Region refused the token" if token else "Region needs a token",
                "detail": (
                    f"{token_env} was rejected (HTTP 401); it must equal the region's own."
                    if token
                    else f"This region requires a bearer token. Set {token_env} in .env "
                    "to the value of the region's security.api_auth.token_env."
                ),
                "code": "auth",
            },
        )
    return result


def _source_inventory(client: Any, base: str, pheasant: dict[str, Any]) -> dict[str, Any] | None:
    """What the region holds for the lab's source (pheasant >= 0.13.3), or ``None``.

    ``/sources/{name}/overview`` reads the region's own index, so it is the
    region's count rather than the run's. A 404 is either an older region or a
    source no run has registered yet; both mean "nothing to show", not an error.
    """

    name = str(pheasant.get("source_name") or "")
    if not name:
        return None
    try:
        response = client.get(f"{base}/sources/{quote(name, safe='')}/overview")
    except httpx.HTTPError:  # best-effort, like every probe here
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    totals = payload.get("totals") or {}
    source = payload.get("source") or {}
    return {
        "source_name": name,
        "documents": totals.get("documents"),
        "size_bytes": totals.get("size_bytes"),
        "status": source.get("status"),
        "last_indexed_at": source.get("last_indexed_at"),
    }


def notices_for(ready: dict[str, Any], queue: dict[str, Any] | None) -> list[dict[str, Any]]:
    notices: list[dict[str, Any]] = []
    status = ready.get("status")
    if status == "draining":
        notices.append(
            {
                "tone": "danger",
                "title": "Region draining",
                "detail": f"This replica is shutting down (draining "
                f"{ready.get('draining_for_seconds', 0)}s). Calls may be refused until "
                "another replica answers; a run is not failed by this.",
                "code": "draining",
            }
        )
    elif status == "standby":
        notices.append(
            {
                "tone": "info",
                "title": "Standby replica",
                "detail": "Another replica holds the leader lease; queued work runs there.",
                "code": "standby",
            }
        )
    elif status and status != "ready":
        notices.append(
            {
                "tone": "danger",
                "title": "Region not ready",
                "detail": str(ready.get("reason") or status),
                "code": "not_ready",
            }
        )
    generation = ready.get("graph_generation") or {}
    loaded, published = generation.get("loaded"), generation.get("published")
    if loaded and published and loaded != published:
        notices.append(
            {
                "tone": "info",
                "title": "Replica behind",
                "detail": f"Answering from graph {loaded} while {published} is published; "
                "results may lag one commit.",
                "code": "stale_generation",
            }
        )
    for task in (queue or {}).get("tasks") or []:
        state = task.get("state")
        if state == "awaiting_claim":
            notices.append(
                {
                    "tone": "warn",
                    "title": "Sync awaiting an indexer",
                    "detail": f"{task.get('source')}: queued {task.get('waiting_seconds')}s, "
                    f"position {task.get('position')}. No indexer has claimed it.",
                    "code": f"queue:{task.get('task_id')}",
                }
            )
        elif state == "claim_lapsed":
            notices.append(
                {
                    "tone": "warn",
                    "title": "Indexer stopped heartbeating",
                    "detail": f"{task.get('source')}: the claim lapsed; it will be redelivered.",
                    "code": f"queue:{task.get('task_id')}",
                }
            )
        elif state == "dead":
            notices.append(
                {
                    "tone": "danger",
                    "title": "Sync dead-lettered",
                    "detail": f"{task.get('source')}: {task.get('last_error') or 'out of attempts'}.",
                    "code": f"queue:{task.get('task_id')}",
                }
            )
    return notices
