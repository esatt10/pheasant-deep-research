"""Logging control: view, filter, delete and retain what the console accumulates.

The rule these hold is the repository's rule 5 from the other side: a run's
raw trace is never trimmed, so retention deletes whole launch logs, whole runs
or the rebuildable projection - and nothing a launch is still writing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from pheasant_lab.console.launcher import Launch, Launcher
from pheasant_lab.console.logs import LogControl, RetentionPolicy, read_lines
from pheasant_lab.logsetup import configure_logging
from pheasant_lab.settings import LoggingSection

DAY = 86400.0


def _run(root: Path, run_id: str, *, age_days: float = 0.0, reported: bool = False) -> Path:
    run = root / run_id
    (run / "raw").mkdir(parents=True)
    (run / "projections").mkdir()
    (run / "run-manifest.json").write_text("{}")
    (run / "raw" / "events.jsonl").write_text('{"sequence": 1}\n')
    (run / "projections" / "run.duckdb").write_bytes(b"x" * 4096)
    if reported:
        (run / "reports").mkdir()
        (run / "reports" / "summary.md").write_text("# summary\n")
    stamp = time.time() - age_days * DAY
    for path in [run, *run.rglob("*")]:
        os.utime(path, (stamp, stamp))
    return run


def _launch(launcher: Launcher, launch_id: str, *, status: str, age_days: float, text: str) -> None:
    launcher.home.mkdir(parents=True, exist_ok=True)
    log = launcher.home / f"{launch_id}.log"
    log.write_text(text)
    started = time.time() - age_days * DAY
    launch = Launch(
        launch_id=launch_id,
        kind="demo",
        argv=["demo"],
        started_at=started,
        config="configs/demo.yaml",
        status=status,
        finished_at=None if status == "running" else started + 5,
        log=str(log),
        pid=None,
    )
    launcher._launches[launch_id] = launch
    launcher._save(launch)


@pytest.fixture
def control(tmp_path: Path) -> LogControl:
    root = tmp_path / "runs"
    root.mkdir()
    launcher = Launcher(tmp_path, root)
    return LogControl(root, launcher)


def test_the_inventory_sizes_runs_by_what_their_bytes_are(control: LogControl) -> None:
    _run(control.output_root, "run-a")
    _launch(
        control.launcher, "launch-1", status="succeeded", age_days=0, text="$ pheasant-lab demo\n"
    )
    inventory = control.inventory()
    (run,) = inventory["runs"]
    assert run["categories"]["projection"] == 4096
    assert run["categories"]["trace"] > 0
    assert inventory["totals"]["projections"] == 4096
    assert inventory["launches"][0]["deletable"] is True
    assert {"path": "raw/events.jsonl", "size_bytes": 16} in run["files"]


def test_a_log_is_read_in_pages_filtered_and_numbered(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text(
        "INFO pheasant_lab: started\n"
        "DEBUG httpx: chatter\n"
        "WARNING pheasant_lab: slow region\n"
        "Traceback (most recent call last):\n"
        "ERROR pheasant_lab: crashed\n"
    )
    page = read_lines(log, level="WARNING")
    assert [row["n"] for row in page["lines"]] == [3, 4, 5]
    assert read_lines(log, query="SLOW")["lines"] == [
        {"n": 3, "text": "WARNING pheasant_lab: slow region"}
    ]
    tail = read_lines(log, limit=2, tail=True)
    assert [row["n"] for row in tail["lines"]] == [4, 5] and tail["has_more"] is False
    first = read_lines(log, limit=2)
    assert first["has_more"] is True and first["matched"] == 5
    with pytest.raises(ValueError, match="level must be one of"):
        read_lines(log, level="LOUD")


def test_a_json_log_line_is_filtered_by_its_level_field(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text('{"level": "INFO", "message": "a"}\n{"level": "ERROR", "message": "b"}\n')
    assert [row["n"] for row in read_lines(log, level="ERROR")["lines"]] == [2]


def test_a_run_file_outside_the_run_cannot_be_read(control: LogControl) -> None:
    _run(control.output_root, "run-a")
    with pytest.raises(KeyError):
        control.read_run_file("run-a", "../../etc/passwd")
    with pytest.raises(KeyError):
        control.read_run_file("../outside", "x.log")
    with pytest.raises(ValueError, match="not a text log"):
        control.read_run_file("run-a", "projections/run.duckdb")


def test_nothing_a_launch_is_writing_can_be_deleted(control: LogControl) -> None:
    _launch(control.launcher, "launch-1", status="running", age_days=0, text="")
    with pytest.raises(ValueError, match="still running"):
        control.delete_launch("launch-1")
    _run(control.output_root, "run-live")
    control.launcher.live_runs = lambda: {"run-live"}  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="running launch"):
        control.delete_run("run-live")
    with pytest.raises(ValueError, match="running launch"):
        control.drop_projection("run-live")


def test_deleting_is_whole_and_audited(control: LogControl) -> None:
    run = _run(control.output_root, "run-a")
    _launch(control.launcher, "launch-1", status="failed", age_days=0, text="boom\n")
    control.drop_projection("run-a")
    assert not (run / "projections").exists() and (run / "raw" / "events.jsonl").is_file()
    control.delete_launch("launch-1")
    assert not (control.launcher.home / "launch-1.log").exists()
    assert not (control.launcher.home / "launch-1.json").exists()
    with pytest.raises(KeyError):
        control.launcher.get("launch-1")
    control.delete_run("run-a")
    assert not run.exists()
    audit = control.audit()
    assert [row["kind"] for row in audit] == ["run", "launch_log", "projection"]
    assert audit[2]["freed_bytes"] == 4096


def test_the_default_policy_deletes_nothing(control: LogControl) -> None:
    _run(control.output_root, "run-old", age_days=400)
    _launch(control.launcher, "launch-old", status="succeeded", age_days=400, text="x")
    assert control.plan() == []


def test_retention_previews_then_applies_and_spares_what_it_must(control: LogControl) -> None:
    _run(control.output_root, "run-old", age_days=40)
    _run(control.output_root, "run-old-reported", age_days=40, reported=True)
    _run(control.output_root, "run-old-kept", age_days=40)
    _run(control.output_root, "run-mid", age_days=10)
    _run(control.output_root, "run-new", age_days=0)
    _launch(control.launcher, "launch-old", status="succeeded", age_days=40, text="x")
    _launch(control.launcher, "launch-running", status="running", age_days=40, text="x")
    _launch(control.launcher, "launch-new", status="succeeded", age_days=0, text="x")
    control.keep("run-old-kept", True)
    control.set_policy({"run_days": 30, "launch_log_days": 7, "projection_days": 5})

    plan = control.plan()
    targets = {(row["kind"], row["target"]) for row in plan}
    assert targets == {
        ("run", "run-old"),
        ("launch_log", "launch-old"),
        # the reported and kept runs keep their trace; only the rebuildable
        # projection goes once they are past the projection window
        ("projection", "run-old-reported"),
        ("projection", "run-old-kept"),
        ("projection", "run-mid"),
    }
    assert all(row["reason"] for row in plan)
    assert (control.output_root / "run-old").exists(), "a preview deleted something"

    applied = control.apply()
    assert {row["target"] for row in applied} == {t for _, t in targets}
    assert not (control.output_root / "run-old").exists()
    assert (control.output_root / "run-old-reported" / "raw" / "events.jsonl").is_file()
    assert (control.output_root / "run-new" / "projections").is_dir()
    assert control.launcher.get("launch-running").status == "running"
    assert control.plan() == []
    assert all("retention policy" in row["reason"] for row in control.audit()[:5])


def test_a_count_limit_keeps_the_newest(control: LogControl) -> None:
    for index in range(4):
        _run(control.output_root, f"run-{index}", age_days=4 - index)
    control.set_policy({"max_runs": 2})
    assert {row["target"] for row in control.plan()} == {"run-0", "run-1"}


@pytest.mark.parametrize(
    "payload",
    [{"run_days": -1}, {"max_runs": 0}, {"max_launch_logs": 1.5}, {"forever": True}],
)
def test_a_nonsense_policy_is_refused_not_stored(control: LogControl, payload) -> None:
    with pytest.raises(ValueError):
        control.set_policy(payload)
    assert control.policy() == RetentionPolicy()


def test_logging_level_format_and_file_are_applied(tmp_path: Path) -> None:
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    try:
        section = LoggingSection(level="WARNING", format="json", file="logs/lab.log")
        target = configure_logging(section, tmp_path)
        assert target == tmp_path / "logs" / "lab.log"
        logging.getLogger("pheasant_lab").info("quiet")
        logging.getLogger("pheasant_lab").warning("loud")
        # A second stage in the same process replaces the handler, not doubles it.
        configure_logging(section, tmp_path)
        logging.getLogger("pheasant_lab").error("once")
        rows = [json.loads(line) for line in target.read_text().splitlines()]
        assert [row["message"] for row in rows] == ["loud", "once"]
        assert rows[0]["level"] == "WARNING"
    finally:
        for handler in list(root.handlers):
            if handler not in before[1]:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(before[0])


def test_verbose_wins_over_the_configured_level(tmp_path: Path) -> None:
    root = logging.getLogger()
    level = root.level
    try:
        configure_logging(LoggingSection(level="ERROR"), tmp_path, verbose=True)
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(level)
