"""Capture pheasant's wire shapes into ``tests/fixtures/pheasant/``.

    uv run python scripts/capture_pheasant_fixtures.py \\
        --url http://127.0.0.1:8765/mcp --state-path /path/to/region/state

The contract tests read these captures rather than the mock, because a mock is
only as faithful as whoever last checked it. Run this against every pheasant
release the lab is meant to support, then point the tests at the new files.

What the region needs before a capture:

* the lab's configuration (``readiness.enabled``, the state path allow-listed,
  a ``memory`` source, the API token in ``PHEASANT_API_TOKEN``);
* the lab's corpus already indexed - one ``pheasant-lab collect`` against it -
  because ``search_context`` and ``get_file_summary`` are captured over the
  real fixture literature, not over the two documents submitted here.

``--queued-sync`` (pheasant >= 0.13.2, a role-split region, **no indexer
running**) also registers the scratch submission's landing directory and
syncs it, so ``sync_source`` answers ``queued`` and ``get_index_queue`` is
captured holding an unclaimed task: the pre-claim interval's two shapes.
Without it, ``get_index_queue`` is captured as the region answers it now.

It writes to the region: one submission under ``fixture-wire`` and one memory
record. Use a scratch region.

Host paths are rewritten to ``/state`` (and ``/memory``) so a capture does not depend on where
the region happened to keep its state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from pheasant_lab.pheasant.client import PheasantClient
from pheasant_lab.settings import PheasantFile, interpolate

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "pheasant"
KNOWLEDGE_BASE = "pheasant-lab"
QUERY = "Dsup nucleosome"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument(
        "--state-path", required=True, help="the region's pheasant.state_path, rewritten to /state"
    )
    parser.add_argument(
        "--memory-path", default=None, help="the memory source's path, rewritten to /memory"
    )
    parser.add_argument(
        "--label",
        default=None,
        help="fixture suffix; defaults to the version the server reports. Use a "
        "local-version suffix (0.13.1+abc1234) for an unreleased build.",
    )
    parser.add_argument(
        "--queued-sync",
        action="store_true",
        help="also register and sync the scratch submission, to capture a queued sync and an "
        "unclaimed index task (a role-split region with no indexer running)",
    )
    parser.add_argument(
        "--tools-only",
        action="store_true",
        help="capture tools/list and nothing else (writes nothing to the region)",
    )
    args = parser.parse_args(argv)

    body = yaml.safe_load((REPO / "configs/pheasant-mcp.example.yaml").read_text())
    config = PheasantFile.model_validate(
        interpolate(body, {**os.environ, "PHEASANT_MCP_URL": args.url}, [])
    )
    client = PheasantClient(config, token=os.environ.get(config.token_env) or None)
    session = client.connect()
    label = args.label or session.server_version

    named = {str(spec.tool) for spec in config.capabilities.values()}
    # The graph-read tools are not in the capability map, but an arm that
    # follows `expand` neighbours would call them; capture them alongside.
    named |= {"search_context_batch", "get_graph_neighbors", "get_graph_slice"}
    tools = {name: tool for name, tool in sorted(session.tools.items()) if name in named}
    _write(
        FIXTURES / f"tools-{label}.json",
        {
            "note": (
                "tools/list entries for the tools the lab's capability map names, captured "
                "from a running pheasant. Regenerate against a new release rather than "
                "editing by hand (scripts/capture_pheasant_fixtures.py)."
            ),
            "server": {
                "name": session.server_name,
                "protocol_version": session.protocol_version,
                "version": session.server_version,
            },
            "tools": tools,
        },
        _rewrites(args),
    )
    if args.tools_only:
        return 0

    def call(tool: str, arguments: dict[str, Any]) -> Any:
        outcome = client.call(tool, arguments, idempotent=True, allow_error=True)
        assert outcome.result is not None, tool
        if outcome.result.is_error:
            raise SystemExit(f"{tool} refused: {outcome.result.text}")
        return outcome.result.payload()

    responses: dict[str, Any] = {
        "note": (
            f"Responses captured from a running pheasant {label}; host paths normalised to /state."
        ),
        "server_version": session.server_version,
    }
    # One accepted and one rejected item, so the receipt reader sees both lists.
    responses["submit_documents"] = call(
        "submit_documents",
        {
            "knowledge_base": KNOWLEDGE_BASE,
            "source_name": "fixture-wire",
            "submission_id": "submission-fixture",
            "documents": [
                {
                    "relative_path": "topic-x/10.1_a.md",
                    "text": "# A\n\nfixture body about dsup\n",
                    "idempotency_key": "pheasant-lab:source-aaa:1",
                    "metadata": {
                        "lab_source_id": "source-aaa",
                        "content_digest": "sha256:not-the-region-s",
                    },
                },
                {
                    "relative_path": "topic-x/empty.md",
                    "text": "",
                    "idempotency_key": "pheasant-lab:source-bbb:2",
                },
            ],
        },
    )
    # Before any sync of `fixture-wire`, so the one accepted item is still out.
    responses["acknowledge_ingest"] = call(
        "acknowledge_ingest", {"knowledge_base": KNOWLEDGE_BASE, "source_name": "fixture-wire"}
    )
    search = {
        "knowledge_base": KNOWLEDGE_BASE,
        "query": QUERY,
        "max_results": 2,
        "mode": "hybrid",
        "memory": "off",
    }
    responses["search_context"] = call("search_context", search)
    expand = session.tools.get("search_context", {}).get("inputSchema", {}).get("properties", {})
    if "expand" in expand:
        responses["search_context_expanded"] = call(
            "search_context", {**search, "expand": {"depth": 2, "max_neighbors": 6}}
        )
    first = (responses["search_context"].get("results") or [{}])[0]
    provenance = first.get("provenance") or {}
    responses["get_file_summary"] = call(
        "get_file_summary",
        {
            "knowledge_base": KNOWLEDGE_BASE,
            "path": provenance.get("relative_path"),
            "source_name": provenance.get("source_id"),
        },
    )
    responses["memory_write"] = call(
        "memory_write",
        {
            "knowledge_base": KNOWLEDGE_BASE,
            "text": "fixture fact",
            "scope": "org",
            "kind": "fact",
            "principal": "pheasant-swarm-lab",
            "sync": True,
        },
    )
    if "get_index_queue" in session.tools:
        if args.queued_sync:
            responses["register_source"] = call(
                "register_source",
                {
                    "knowledge_base": KNOWLEDGE_BASE,
                    "name": "fixture-wire",
                    "source_type": "document_folder",
                    "path": responses["submit_documents"]["directory"],
                    "include": ["**/*"],
                },
            )
            responses["sync_source_queued"] = call(
                "sync_source", {"knowledge_base": KNOWLEDGE_BASE, "source_name": "fixture-wire"}
            )
        responses["get_index_queue"] = call("get_index_queue", {"knowledge_base": KNOWLEDGE_BASE})
    if args.queued_sync:
        responses["note"] += (
            " Role-split region: an api replica with a graph-service replica and a local index "
            "queue, no indexer running during the capture, so every sync - the memory "
            "write's included - is queued and the index tasks are unclaimed."
        )
    _write(FIXTURES / f"responses-{label}.json", responses, _rewrites(args))
    return 0


def _rewrites(args: argparse.Namespace) -> dict[str, str]:
    pairs = {args.state_path: "/state", args.memory_path: "/memory"}
    return {str(Path(host).resolve()): stand_in for host, stand_in in pairs.items() if host}


def _write(path: Path, payload: Any, rewrites: dict[str, str]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for host, stand_in in rewrites.items():
        text = text.replace(host, stand_in)
    path.write_text(text)
    print(f"wrote {path.relative_to(REPO)}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
