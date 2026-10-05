"""The console: one fold over a run's raw files, served over stdlib HTTP.

What is held here is what a person watching a run relies on: that the live
view is a fold of the append-only trace and nothing else, that the pre-claim
interval a fleet introduces is shown as its own state rather than as an
unexplained wait, and that the server cannot be walked out of the run and
config directories it was pointed at.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from pheasant_lab.console.launcher import Launcher
from pheasant_lab.console.projection import LiveModel, RunWatcher, _Tail, list_runs
from pheasant_lab.console.region import base_url, notices_for
from pheasant_lab.console.server import serve
from pheasant_lab.settings import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fleet_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A demo run against a mock region that behaves like a role-split fleet."""

    root = tmp_path_factory.mktemp("console")
    environment = {
        **os.environ,
        "PHEASANT_LAB_FIXTURES": str(REPO_ROOT / "tests" / "fixtures" / "literature"),
        "PHEASANT_LAB_PROMPTS": str(REPO_ROOT / "prompts"),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pheasant_lab.cli",
            "demo",
            "--config",
            str(REPO_ROOT / "configs" / "demo.yaml"),
            "--set",
            # The barrier polls with doubling backoff capped at 1s (the mock
            # file's retry_backoff_max_seconds): polls near 0.75s and 1.55s
            # land either side of a 1s claim, and the 2s finish lands before
            # the next one, so every state is observed once at least.
            "mock_claim_seconds=1.0",
            "--output-root",
            str(root / "runs"),
            "--project-root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    runs = sorted((root / "runs").glob("run-*"))
    assert runs, "the fleet demo produced no run"
    yield runs[-1]


def _events(run: Path, kind: str) -> list[dict]:
    lines = (run / "raw" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [row for row in map(json.loads, lines) if row["event_type"] == kind]


# ---------------------------------------------------------------------------
# the lab records the pre-claim interval
# ---------------------------------------------------------------------------


def test_a_queued_sync_is_recorded_as_queued_not_as_indexed(fleet_run: Path) -> None:
    (sync,) = _events(fleet_run, "ingest.sync")
    assert sync["payload"]["disposition"] == "queued"
    assert sync["payload"]["region"]["task_id"].startswith("idx-")


def test_the_barrier_reports_the_claim_before_it_crosses(fleet_run: Path) -> None:
    barrier = _events(fleet_run, "ingest.barrier")
    states = [{task["state"] for task in row["payload"].get("queue") or []} for row in barrier[:-1]]
    assert {"awaiting_claim"} in states
    assert {"claimed"} in states
    assert states.index({"awaiting_claim"}) < states.index({"claimed"})
    assert barrier[-1]["payload"]["disposition"] == "crossed"
    assert barrier[-1]["status"] == "succeeded"


def test_a_standalone_region_reports_an_in_call_sync(demo_run: tuple[Path, str]) -> None:
    run, _ = demo_run
    (sync,) = _events(run, "ingest.sync")
    assert sync["payload"]["disposition"] == "completed"
    # No queue to read, and nothing was ever awaiting a claim.
    assert all("queue" not in row["payload"] for row in _events(run, "ingest.barrier"))


# ---------------------------------------------------------------------------
# the projection
# ---------------------------------------------------------------------------


def test_the_projection_folds_a_finished_fleet_run(fleet_run: Path) -> None:
    watcher = RunWatcher(fleet_run)
    watcher.refresh()
    view = watcher.model.snapshot()

    custody = view["custody"]
    assert custody["discovered"] >= custody["acquired"] > 0
    assert custody["indexed"] == custody["accepted"]
    assert custody["awaiting_claim"] == 0 and custody["claimed"] == 0

    indexer = next(lane for lane in view["lanes"] if lane["id"] == "indexer")
    labels = [bar["label"] for bar in indexer["bars"]]
    assert labels == ["awaiting claim", "indexing"]
    assert all(bar["end"] is not None for bar in indexer["bars"])

    history = {notice["code"]: notice for notice in view["notice_history"]}
    assert history["queued"].get("closed") is True
    assert history["claimed"].get("closed") is True
    assert "crossed" in {notice["code"] for notice in view["notices"]}

    assert [phase["state"] for phase in view["run"]["phases"]] == ["done"] * 5
    assert {arm["arm_id"] for arm in view["arms"]} == set(view["run"]["arms_configured"])
    researchers = [agent for agent in view["agents"] if agent["role"] == "researcher"]
    assert researchers and all(agent["subtopic_label"] for agent in researchers)


def test_a_document_mid_claim_is_shown_awaiting_its_claim(fleet_run: Path) -> None:
    """Stop the fold at the first barrier poll: the live view a person saw then."""

    model = LiveModel(run_id=fleet_run.name)
    for row in map(json.loads, (fleet_run / "raw/events.jsonl").read_text().splitlines()):
        model.apply_event(row)
        if row["event_type"] == "ingest.barrier":
            break
    for row in map(json.loads, (fleet_run / "raw/ingest-receipts.jsonl").read_text().splitlines()):
        if row["status"] == "accepted":
            model.apply_receipt(row)

    view = model.snapshot()
    assert view["custody"]["awaiting_claim"] == view["custody"]["accepted"] > 0
    assert view["custody"]["indexed"] == 0
    assert view["notices"][-1]["code"] == "queued"
    assert [phase["state"] for phase in view["run"]["phases"]] == [
        "done",
        "active",
        "pending",
        "pending",
        "pending",
    ]
    indexer = next(lane for lane in view["lanes"] if lane["id"] == "indexer")
    assert indexer["bars"][-1]["status"] == "pre_claim" and indexer["bars"][-1]["end"] is None


def test_a_standalone_funnel_never_claims_a_wait(demo_run: tuple[Path, str]) -> None:
    watcher = RunWatcher(demo_run[0])
    watcher.refresh()
    custody = watcher.model.snapshot()["custody"]
    assert custody["awaiting_claim"] == 0
    assert custody["indexed"] == custody["accepted"] > 0


def test_the_tail_never_reads_half_a_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"a": 1}\n{"b": ', encoding="utf-8")
    tail = _Tail(path)
    assert tail.read() == [{"a": 1}]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("2}\n")
    assert tail.read() == [{"b": 2}]
    assert tail.read() == []


# ---------------------------------------------------------------------------
# the region probe
# ---------------------------------------------------------------------------


def test_the_region_base_url_is_the_mcp_url_without_its_mount() -> None:
    assert base_url("http://127.0.0.1:8765/mcp") == "http://127.0.0.1:8765"
    assert base_url("https://kb.example/region/mcp/") == "https://kb.example/region"
    assert base_url("") is None


def test_each_readiness_state_is_its_own_notice() -> None:
    assert (
        notices_for({"status": "draining", "draining_for_seconds": 3}, None)[0]["code"]
        == "draining"
    )
    assert notices_for({"status": "standby"}, None)[0]["tone"] == "info"
    assert (
        notices_for({"status": "not_ready", "reason": "state store unreachable"}, None)[0]["detail"]
        == "state store unreachable"
    )
    stale = notices_for(
        {"status": "ready", "graph_generation": {"loaded": "a", "published": "b"}}, None
    )
    assert stale[0]["code"] == "stale_generation"
    assert notices_for({"status": "ready"}, None) == []


def test_a_pre_claim_task_is_a_warning_and_a_claimed_one_is_not() -> None:
    queue = {
        "tasks": [
            {
                "task_id": "idx-1",
                "source": "lit",
                "state": "awaiting_claim",
                "waiting_seconds": 42,
                "position": 1,
            },
            {"task_id": "idx-2", "source": "lit", "state": "claimed"},
            {"task_id": "idx-3", "source": "lit", "state": "dead", "last_error": "boom"},
        ]
    }
    notices = notices_for({"status": "ready"}, queue)
    assert [(n["tone"], n["code"]) for n in notices] == [
        ("warn", "queue:idx-1"),
        ("danger", "queue:idx-3"),
    ]


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_neither_console_setting_moves_the_config_digest() -> None:
    """A run started before either existed must still resume without --fork."""

    base = load_config(REPO_ROOT / "configs/demo.yaml", project_root=REPO_ROOT)
    plain = base.model_copy(deep=True)
    plain.pheasant.capabilities.pop("index_queue", None)
    assert base.digest() == plain.digest()
    simulated = load_config(
        REPO_ROOT / "configs/demo.yaml",
        project_root=REPO_ROOT,
        overrides={"mock_claim_seconds": "0"},
    )
    assert simulated.digest() == base.digest()
    moved = load_config(
        REPO_ROOT / "configs/demo.yaml",
        project_root=REPO_ROOT,
        overrides={"mock_claim_seconds": "2"},
    )
    assert moved.digest() != base.digest()


def test_the_launcher_refuses_what_it_was_not_built_to_run(tmp_path: Path) -> None:
    launcher = Launcher(REPO_ROOT, tmp_path)
    with pytest.raises(ValueError, match="not a config file"):
        launcher.launch(kind="demo", config="../../etc/passwd")
    with pytest.raises(ValueError, match="unknown launch kind"):
        launcher.launch(kind="rm", config="configs/demo.yaml")
    with pytest.raises(ValueError, match="needs a run"):
        launcher.launch(kind="evaluate", config="configs/demo.yaml")
    with pytest.raises(ValueError, match=r"a\.b=value"):
        launcher.launch(kind="demo", config="configs/demo.yaml", overrides=["oops"])


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------


@pytest.fixture
def console(fleet_run: Path, tmp_path: Path) -> Iterator[str]:
    server = serve(
        host="127.0.0.1",
        port=0,
        project_root=REPO_ROOT,
        output_root=fleet_run.parent,
        default_config="configs/demo.yaml",
        ui_dist=tmp_path / "no-ui",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as error:
        body, status = error.read().decode(), error.code
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def test_the_api_lists_and_folds_runs(console: str, fleet_run: Path) -> None:
    status, runs = _get(f"{console}/api/runs")
    assert status == 200 and runs[0]["run_id"] == fleet_run.name
    assert runs == list_runs(fleet_run.parent)

    status, view = _get(f"{console}/api/runs/{fleet_run.name}")
    assert status == 200 and view["run"]["run_id"] == fleet_run.name

    status, page = _get(f"{console}/api/runs/{fleet_run.name}/events?after=0&limit=5")
    assert [row["sequence"] for row in page["events"]] == [1, 2, 3, 4, 5]


def test_the_stream_opens_with_the_backlog_then_the_model(console: str, fleet_run: Path) -> None:
    with urllib.request.urlopen(
        f"{console}/api/runs/{fleet_run.name}/stream", timeout=10
    ) as stream:
        assert stream.headers["Content-Type"] == "text/event-stream"
        kinds: list[str] = []
        while "model" not in kinds:
            line = stream.readline().decode().strip()
            if line.startswith("event: "):
                kinds.append(line.removeprefix("event: "))
    assert kinds[0] == "events" and kinds[-1] == "model"


def test_the_server_cannot_be_walked_out_of_its_directories(console: str, fleet_run: Path) -> None:
    assert _get(f"{console}/api/runs/..%2F..%2Fetc")[0] == 404
    assert _get(f"{console}/api/runs/{fleet_run.name}/reports/..%2Frun-manifest.json")[0] == 404
    status, _ = _get(f"{console}/api/region?config=../pyproject.toml")
    assert status == 400


def test_an_unbuilt_ui_says_how_to_build_it(console: str) -> None:
    status, body = _get(f"{console}/live")
    assert status == 200 and "make ui" in str(body)


def test_a_replay_position_shows_the_run_as_it_stood(fleet_run: Path) -> None:
    from pheasant_lab.console.projection import fold_until

    (sync,) = _events(fleet_run, "ingest.sync")
    then = fold_until(fleet_run, int(sync["sequence"])).snapshot()
    watcher = RunWatcher(fleet_run)
    watcher.refresh()
    now = watcher.model.snapshot()

    assert then["run"]["sequence"] == sync["sequence"] < now["run"]["sequence"]
    assert then["custody"]["indexed"] == 0 and now["custody"]["indexed"] > 0
    assert then["custody"]["awaiting_claim"] > 0
    assert not then["arms"] and now["arms"]
