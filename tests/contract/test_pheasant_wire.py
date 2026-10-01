"""The adapters against what pheasant actually sends.

Every payload read here was captured from a running pheasant 0.12.16
(``tests/fixtures/pheasant/responses-0.12.16.json``). The mock region is kept
faithful to the same shapes, but a mock is only as faithful as whoever last
checked it - the receipts, the acknowledgement counts, the hit shape and the
nested memory record were each read wrongly here for as long as only the mock
was asked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from pheasant_lab.pheasant.capabilities import resolve
from pheasant_lab.pheasant.client import PheasantClient
from pheasant_lab.pheasant.ingestion import (
    Ingestor,
    IngestPayload,
    IngestRequest,
    IngestSource,
    RegistrationRefused,
)
from pheasant_lab.pheasant.mock import MockPheasantServer, _Refusal
from pheasant_lab.pheasant.protocol import McpToolError
from pheasant_lab.pheasant.receipts import parse_receipts
from pheasant_lab.pheasant.retrieval import Retriever, SearchRequest, normalise_results

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pheasant"

# pheasant/memory/steering.py's preference grammar, verbatim. A record it does
# not match is ignored by the region without a word.
PHEASANT_PREFERENCE = re.compile(
    r"\bwhen\s*:\s*(?P<when>[^\n]*?)\s*(?:->|→|=>)\s*prefer\s*:\s*(?P<prefer>[^\n]+)", re.I
)


@pytest.fixture(scope="module")
def wire() -> dict:
    return json.loads((FIXTURES / "responses-0.12.16.json").read_text())


def search_request() -> SearchRequest:
    return SearchRequest(
        run_id="run-1", arm_id="P0", question_id="q-1", query="dsup", namespace="pheasant-lab"
    )


# -- receipts --------------------------------------------------------------


def test_real_receipts_are_read_from_the_accepted_and_rejected_lists(wire):
    payload = wire["submit_documents"]
    accepted = payload["accepted"][0]
    receipts = parse_receipts(
        payload,
        run_id="run-1",
        key_to_source={
            "pheasant-lab:source-aaa:1": "source-aaa",
            "pheasant-lab:source-bbb:2": "source-bbb",
        },
        requested_digests={"pheasant-lab:source-aaa:1": "sha256:" + accepted["content_sha256"]},
    )
    by_key = {r.idempotency_key: r for r in receipts}
    assert set(by_key) == {"pheasant-lab:source-aaa:1", "pheasant-lab:source-bbb:2"}

    good = by_key["pheasant-lab:source-aaa:1"]
    assert good.accepted and not good.indexed
    assert good.artifact_id == accepted["artifact_id"]
    # Verified against the digest the region computed, not the `content_digest`
    # this lab put in the metadata and the region echoed back in `detail`.
    assert good.accepted_content_digest == "sha256:" + accepted["content_sha256"]
    assert good.digest_matches is True

    bad = by_key["pheasant-lab:source-bbb:2"]
    assert bad.status == "rejected" and bad.accepted is False
    assert bad.error_code == "INVALID_REQUEST"
    assert bad.error_message and "empty" in bad.error_message


# -- search hits -----------------------------------------------------------


def test_a_real_hit_resolves_to_the_labs_source_not_the_regions(wire):
    hit = wire["search_context"]["results"][0]
    results = normalise_results(
        wire["search_context"],
        search_request(),
        artifact_sources={hit["node_id"]: "source-from-receipts"},
    )
    first = results[0]
    assert first.artifact_id == hit["node_id"]
    assert first.source_id == "source-from-receipts"
    assert first.region_source == hit["provenance"]["source_id"]
    assert first.locator == hit["relative_path"]
    assert first.retrieval_arm == hit["retrieved_by"]
    assert first.matched_text, "the preview is the passage until it is hydrated"


def test_without_receipts_a_hit_falls_back_to_the_front_matter_not_the_region(wire):
    results = normalise_results(wire["search_context"], search_request())
    region_sources = {r["provenance"]["source_id"] for r in wire["search_context"]["results"]}
    for result in results:
        assert result.source_id not in region_sources, (
            "the region's source name read as the lab's collapses every hit onto one source"
        )
        assert result.source_id and result.source_id.startswith("source-")


# -- memory ----------------------------------------------------------------


def test_the_record_id_is_read_from_pheasants_nested_record(wire, config):
    from pheasant_lab.arms.pheasant_memory import MemorySeeder

    payload = wire["memory_write"]
    client = SimpleNamespace(
        call=lambda *a, **k: SimpleNamespace(result=SimpleNamespace(payload=lambda: payload))
    )
    capabilities = SimpleNamespace(has=lambda name: True, tool=lambda name: "memory_write")
    seeder = MemorySeeder(client, capabilities, config.pheasant)
    written = seeder._write({"text": "x", "kind": "fact", "scope": "org"}, "q-1")
    assert written is not None
    assert written["record_id"] == payload["record"]["record_id"]


def test_seeded_preferences_parse_under_pheasants_rule_grammar(config):
    from pheasant_lab.arms.pheasant_memory import MemorySeeder

    seeder = MemorySeeder(None, None, config.pheasant)
    entry = {
        "question_text": "How does Dsup protect chromatin from radiation?",
        "answer_text": "",
        "queries_used": ["dsup"],
        "retrieved_artifact_ids": ["file:src:topic/a.md:branch=none"],
        "search_calls": [
            {
                "results": [
                    {"artifact_id": "file:src:topic/a.md:branch=none", "locator": "topic/a.md"}
                ]
            }
        ],
    }
    preferences = [c for c in seeder._candidates(entry) if c["kind"] == "preference"]
    assert len(preferences) == 1
    match = PHEASANT_PREFERENCE.search(preferences[0]["text"])
    assert match, f"pheasant would ignore {preferences[0]['text']!r}"
    assert match.group("prefer").strip() == "topic/a.md"


# -- the index barrier, through the faithful mock ---------------------------


def request_for(text: str, source_id: str = "source-1") -> IngestRequest:
    return IngestRequest(
        run_id="run-1",
        topic_id="topic-1",
        source_id=source_id,
        namespace="pheasant-lab",
        source=IngestSource(title="t", stable_identifier=f"10.1/{source_id}"),
        payload=IngestPayload(text=text),
    )


def ingestor_for(config, server, *, drop: tuple[str, ...] = ()) -> Ingestor:
    client = PheasantClient.in_process(config.pheasant, server)
    session = client.connect()
    tools = {name: tool for name, tool in session.tools.items() if name not in drop}
    return Ingestor(client, resolve(config.pheasant, tools), config.pheasant, run_id="run-1")


def test_a_submission_is_unsearchable_until_its_directory_is_registered(config):
    server = MockPheasantServer()
    ingestor = ingestor_for(config, server, drop=("register_source",))
    ingestor.submit([request_for("body about dsup")])
    with pytest.raises(McpToolError, match="Unknown source"):
        ingestor.sync()


def test_sync_registers_where_the_region_landed_the_submission(config):
    server = MockPheasantServer()
    ingestor = ingestor_for(config, server)
    ingestor.submit([request_for("body about dsup")])
    ingestor.sync()
    assert server.sources == {config.pheasant.source_name: ingestor.landing_directory}
    ingestor.acknowledge()
    assert all(receipt.indexed for receipt in ingestor.ledger.receipts)


def test_a_refused_registration_names_the_setting_that_fixes_it(config):
    class Strict(MockPheasantServer):
        def _tool_register_source(self, arguments):
            raise _Refusal("Path is outside the allowed workspace roots")

    ingestor = ingestor_for(config, Strict())
    ingestor.submit([request_for("body")])
    with pytest.raises(RegistrationRefused, match="allow_workspace_roots"):
        ingestor.sync()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_the_barrier_waits_for_an_indexer_that_runs_elsewhere(config):
    """On a fleet, `sync_source` publishes; the indexer catches up later."""

    class Fleet(MockPheasantServer):
        lag = 2

        def _tool_sync_source(self, arguments):
            return {"source_id": arguments.get("source_name"), "status": "queued"}

        def _tool_acknowledge_ingest(self, arguments):
            self.lag -= 1
            if self.lag <= 0:
                for document in self.documents.values():
                    document.indexed = True
            return super()._tool_acknowledge_ingest(arguments)

    server = Fleet()
    server.sources[config.pheasant.source_name] = "/state/uploads/x"
    ingestor = ingestor_for(config, server)
    ingestor._clock = FakeClock()
    ingestor.submit([request_for("body")])
    ingestor.sync()
    result = ingestor.acknowledge()
    assert result["still_accepted"] == 0
    assert ingestor._clock.slept, (
        "the barrier polled instead of declaring an unindexed item indexed"
    )
    assert all(receipt.indexed for receipt in ingestor.ledger.receipts)


def test_a_barrier_that_never_clears_says_so(config):
    class Stuck(MockPheasantServer):
        def _tool_sync_source(self, arguments):
            return {"status": "queued"}

    server = Stuck()
    server.sources[config.pheasant.source_name] = "/state/uploads/x"
    ingestor = ingestor_for(config, server)
    ingestor._clock = FakeClock()
    ingestor.submit([request_for("body")])
    ingestor.sync()
    result = ingestor.acknowledge(wait_seconds=1.0)
    assert result["barrier"] == "timed_out"
    assert not any(receipt.indexed for receipt in ingestor.ledger.receipts)


# -- hydration -------------------------------------------------------------


def test_passages_are_read_back_whole_rather_than_as_previews(config):
    from pheasant_lab.arms.base import passages_from

    server = MockPheasantServer()
    ingestor = ingestor_for(config, server)
    body = "dsup " + "chromatin shielding " * 60 + "the decisive sentence is at the end."
    ingestor.submit([request_for(body)])
    ingestor.sync()

    client = PheasantClient.in_process(config.pheasant, server)
    retriever = Retriever(client, resolve(config.pheasant, client.connect().tools), config.pheasant)
    response = retriever.search(search_request())
    passages = passages_from([response], limit=5)
    assert "decisive sentence" not in passages[0]["text"], "the fixture must exceed the preview"
    retriever.hydrate(passages)
    assert passages[0]["text_source"] == "document"
    assert passages[0]["text"] == body


def test_a_graph_relationship_hit_is_hydrated_from_the_labs_own_submission(config):
    """Pheasant's graph arm returns `X —mentions→ Y` hits with no path at all."""

    from pheasant_lab.arms.base import passages_from

    server = MockPheasantServer()
    ingestor = ingestor_for(config, server)
    request = request_for("dsup shields chromatin, the whole abstract")
    ingestor.submit([request])
    ingestor.sync()
    artifact_id = ingestor.ledger.receipts[0].artifact_id

    client = PheasantClient.in_process(config.pheasant, server)
    retriever = Retriever(client, resolve(config.pheasant, client.connect().tools), config.pheasant)
    relationship = {
        "kind": "relationship",
        "node_id": artifact_id,
        "type": "relationship",
        "edge_type": "mentions",
        "title": f"{request.path()} —mentions→ Chromatin",
        "summary": f"{request.path()} mentions Chromatin",
        "score": 0.016,
        "rank": 1,
        "retrieved_by": "graph",
    }
    response = SimpleNamespace(
        results=normalise_results({"results": [relationship]}, search_request())
    )
    passages = passages_from([response], limit=5)
    assert passages[0]["locator"] is None
    retriever.hydrate(passages)
    assert passages[0]["text_source"] == "preview", "no path, and nothing told it one"

    retriever.artifact_paths = {artifact_id: request.path()}
    passages = passages_from([response], limit=5)
    retriever.hydrate(passages)
    assert passages[0]["text_source"] == "document"
    assert "the whole abstract" in passages[0]["text"]
