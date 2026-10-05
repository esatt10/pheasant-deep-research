"""Durability: a run survives the process that was running it.

The faults here are real ones. ``tests/fixtures/faults/crashy_cli.py`` runs
the real CLI and kills it with ``os._exit`` - no ``finally``, no flush, no
``atexit``, the way an out-of-memory kill or ``SIGKILL`` arrives - after
leaving half a JSON line at the end of ``events.jsonl``. The property asserted
is the strongest one available: a run crashed and resumed reaches the same
corpus and the same numbers as a run that never crashed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from pheasant_lab.lifecycle import durable_write_text, repair_torn_tail
from pheasant_lab.pheasant.receipts import ReceiptLedger, fold_receipts
from pheasant_lab.supervisor import RunLock, RunLocked, supervise
from pheasant_lab.tracing.events import read_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
CRASHY = REPO_ROOT / "tests" / "fixtures" / "faults" / "crashy_cli.py"


def _environment(marker: Path, where: str) -> dict[str, str]:
    return {
        **os.environ,
        "PHEASANT_LAB_FIXTURES": str(REPO_ROOT / "tests" / "fixtures" / "literature"),
        "PHEASANT_LAB_PROMPTS": str(REPO_ROOT / "prompts"),
        "CRASH_MARKER": str(marker),
        "CRASH_AT": where,
    }


def _crashed_run(tmp: Path, where: str, *, attempts: int = 3) -> tuple[int, Path, list[str]]:
    marker = tmp / "crashed-once"
    output = tmp / "runs"
    lines: list[str] = []
    previous = dict(os.environ)
    os.environ.update(_environment(marker, where))
    try:
        code, run_id = supervise(
            [
                "--config",
                "configs/demo.yaml",
                "--set",
                f"experiment.output_root={output}",
                "--project-root",
                str(REPO_ROOT),
            ],
            output_root=output,
            command=[sys.executable, str(CRASHY)],
            cwd=REPO_ROOT,
            attempts=attempts,
            out=lines.append,
            sleep=lambda _seconds: None,
        )
    finally:
        os.environ.clear()
        os.environ.update(previous)
    assert marker.exists(), "the fault never fired, so this test proved nothing"
    assert run_id, "\n".join(lines)
    return code, output / run_id, lines


def _rows(run: Path, name: str) -> list[dict]:
    return list(read_jsonl(run / "raw" / name))


@pytest.fixture(scope="module")
def collect_crash(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[int, Path, list[str]]]:
    yield _crashed_run(tmp_path_factory.mktemp("collect-crash"), "collect.persist1")


@pytest.fixture(scope="module")
def evaluate_crash(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[int, Path, list[str]]]:
    yield _crashed_run(tmp_path_factory.mktemp("evaluate-crash"), "evaluate.answer5")


# ---------------------------------------------------------------------------
# a crash mid-collection
# ---------------------------------------------------------------------------


def test_a_collection_killed_mid_round_resumes_to_the_same_corpus(
    collect_crash: tuple[int, Path, list[str]], demo_run: tuple[Path, str]
) -> None:
    code, run, lines = collect_crash
    clean, _ = demo_run
    assert code in (0, 1), "\n".join(lines)
    assert any("crashed (exit 137)" in line for line in lines)

    # The same sources and the same claims - and each exactly once.
    for name, key in (("sources.jsonl", "source_id"), ("claims.jsonl", "claim_id")):
        crashed_rows, clean_rows = _rows(run, name), _rows(clean, name)
        assert sorted({r[key] for r in crashed_rows}) == sorted({r[key] for r in clean_rows})
        assert len(crashed_rows) == len({r[key] for r in crashed_rows}), f"{name} doubled a row"


def test_documents_the_dead_process_submitted_still_cross_the_barrier(
    collect_crash: tuple[int, Path, list[str]],
) -> None:
    _code, run, _lines = collect_crash
    folded = fold_receipts(_rows(run, "ingest-receipts.jsonl"))
    assert folded and all(row["status"] == "indexed" for row in folded.values())


def test_the_torn_line_is_cut_kept_and_recorded(collect_crash: tuple[int, Path, list[str]]) -> None:
    _code, run, _lines = collect_crash
    events = _rows(run, "events.jsonl")  # parses: the torn tail is gone
    repaired = [e for e in events if e["event_type"] == "run.repaired"]
    assert repaired and repaired[0]["payload"]["file"] == "events.jsonl"
    fragment = run / "integrity" / "torn" / repaired[0]["payload"]["fragment"]
    assert fragment.read_text(encoding="utf-8").startswith('{"event_id": "event-torn"')
    resumed = [e for e in events if e["event_type"] == "collection.resumed"]
    assert resumed and resumed[0]["payload"]["sources_rehydrated"] > 0
    sequences = [e["sequence"] for e in events]
    assert sequences == list(range(1, len(events) + 1)), "the resumed stream renumbered"


def test_every_attempt_is_logged_outside_the_checksums(
    collect_crash: tuple[int, Path, list[str]],
) -> None:
    _code, run, _lines = collect_crash
    attempts = [
        json.loads(line)
        for line in (run / "supervisor" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [(a["stage"], a["outcome"]) for a in attempts[:2]] == [
        ("collect", "crashed"),
        ("collect", "exited"),
    ]
    assert attempts[0]["exit_code"] == 137
    checksummed = (run / "integrity" / "checksums.sha256").read_text(encoding="utf-8")
    assert "supervisor/" not in checksummed
    assert not (run / "supervisor" / "lock").exists(), "the lock outlived its supervisor"


# ---------------------------------------------------------------------------
# a crash mid-evaluation
# ---------------------------------------------------------------------------


def test_an_evaluation_killed_mid_way_reuses_what_it_had_answered(
    evaluate_crash: tuple[int, Path, list[str]], demo_run: tuple[Path, str]
) -> None:
    code, run, lines = evaluate_crash
    clean, _ = demo_run
    assert code in (0, 1), "\n".join(lines)
    answers = _rows(run, "answers.jsonl")
    keys = [(a["arm_id"], a["question_id"], a["repetition"]) for a in answers]
    assert len(keys) == len(set(keys)), "an answer was asked for twice"
    assert len(keys) == len(_rows(clean, "answers.jsonl"))
    resumed = [e for e in _rows(run, "events.jsonl") if e["event_type"] == "evaluation.resumed"]
    assert resumed and resumed[0]["payload"]["answers_reused"] == 5

    # And the numbers are the numbers a run that never crashed reports.
    def means(root: Path) -> dict[tuple[str, str], float | None]:
        aggregates = json.loads((root / "metrics" / "aggregates.json").read_text())
        return {
            (arm, metric): row.get("mean")
            for arm, rows in aggregates["by_arm"].items()
            for metric, row in rows.items()
        }

    assert means(run) == means(clean)


# ---------------------------------------------------------------------------
# the supervisor's policy, without the pipeline
# ---------------------------------------------------------------------------


def _fake_cli(tmp: Path, body: str) -> list[str]:
    script = tmp / "fake_cli.py"
    script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "LOG = Path(os.environ['FAKE_LOG'])\n"
        "with LOG.open('a') as h: h.write(json.dumps(sys.argv[1:]) + '\\n')\n" + body,
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_a_refusal_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls"))
    command = _fake_cli(tmp_path, "sys.exit(2)\n")
    code, run_id = supervise(
        [],
        output_root=tmp_path / "runs",
        command=command,
        out=lambda _l: None,
        sleep=lambda _s: None,
    )
    assert (code, run_id) == (2, None)
    assert len(_calls(tmp_path / "calls")) == 1


def test_a_crash_is_retried_with_backoff_then_left_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls"))
    runs = tmp_path / "runs"
    (runs / "run-x").mkdir(parents=True)
    (runs / "run-x" / "run-manifest.json").write_text("{}")
    waits: list[float] = []
    command = _fake_cli(tmp_path, "sys.exit(3 if sys.argv[1] == 'evaluate' else 0)\n")
    code, run_id = supervise(
        [],
        output_root=runs,
        run_id="run-x",
        attempts=3,
        backoff_seconds=2.0,
        command=command,
        out=lambda _l: None,
        sleep=waits.append,
    )
    assert (code, run_id) == (3, "run-x")
    stages = [call[0] for call in _calls(tmp_path / "calls")]
    assert stages == ["collect", "freeze-benchmark", "evaluate", "evaluate", "evaluate"]
    assert waits == [2.0, 4.0]


def test_completed_stages_are_skipped_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls"))
    run = tmp_path / "runs" / "run-y"
    run.mkdir(parents=True)
    (run / "run-manifest.json").write_text("{}")
    (run / "state.json").write_text(
        json.dumps(
            {"stages": {"collect": {"status": "completed"}, "evaluate": {"status": "running"}}}
        )
    )
    command = _fake_cli(tmp_path, "sys.exit(0)\n")
    code, _ = supervise(
        [], output_root=tmp_path / "runs", run_id="run-y", command=command, out=lambda _l: None
    )
    assert code == 0
    stages = [call[0] for call in _calls(tmp_path / "calls")]
    assert stages == ["freeze-benchmark", "evaluate", "replay", "report", "verify"]


def test_one_supervisor_per_run_and_a_dead_holder_is_taken_over(tmp_path: Path) -> None:
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "supervisor").mkdir()
        (tmp_path / "supervisor" / "lock").write_text(f"{holder.pid} now\n")
        with pytest.raises(RunLocked, match=str(holder.pid)):
            RunLock(tmp_path).acquire()
    finally:
        holder.kill()
        holder.wait()
    lock = RunLock(tmp_path)
    lock.acquire()  # the holder is dead: taken over
    assert lock.path.read_text().split()[0] == str(os.getpid())
    lock.release()
    assert not lock.path.exists()


# ---------------------------------------------------------------------------
# the write primitives
# ---------------------------------------------------------------------------


def test_only_a_torn_tail_is_repaired(tmp_path: Path) -> None:
    clean = tmp_path / "clean.jsonl"
    clean.write_text('{"a": 1}\n{"a": 2}\n')
    assert repair_torn_tail(clean, tmp_path / "q") is None

    torn = tmp_path / "torn.jsonl"
    torn.write_text('{"a": 1}\n{"a": 2}\n{"a": 3, "b": "hal')
    report = repair_torn_tail(torn, tmp_path / "q")
    assert report is not None and report["fragment_bytes"] == len('{"a": 3, "b": "hal')
    assert torn.read_text() == '{"a": 1}\n{"a": 2}\n'
    assert (tmp_path / "q" / report["fragment"]).read_text() == '{"a": 3, "b": "hal'

    # A malformed line in the *middle* is damage, not a crash: left for verify.
    damaged = tmp_path / "damaged.jsonl"
    damaged.write_text('{"a": 1}\nnot json\n{"a": 2}\n')
    assert repair_torn_tail(damaged, tmp_path / "q") is None
    with pytest.raises(ValueError, match="malformed"):
        list(read_jsonl(damaged))


def test_a_durable_write_leaves_the_old_file_or_the_new_one(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    durable_write_text(target, "old\n")
    durable_write_text(target, "new\n")
    assert target.read_text() == "new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"], "a temp file was left behind"


def test_a_resumed_ledger_knows_what_the_dead_process_submitted() -> None:
    receipts = [
        {
            "idempotency_key": "k1",
            "receipt_id": "r1",
            "run_id": "run",
            "source_id": "s1",
            "submission_id": "sub-1",
            "status": "accepted",
            "digest_matches": None,
        },
        {
            "idempotency_key": "k1",
            "receipt_id": "r1",
            "run_id": "run",
            "source_id": "s1",
            "submission_id": "sub-1",
            "status": "indexed",
        },
        {
            "idempotency_key": "k2",
            "receipt_id": "r2",
            "run_id": "run",
            "source_id": "s2",
            "submission_id": "sub-1",
            "status": "accepted",
        },
    ]
    ledger = ReceiptLedger.restore(receipts, [{"idempotency_key": "k1"}, {"idempotency_key": "k2"}])
    assert ledger.get("k1").status == "indexed"  # type: ignore[union-attr]
    assert ledger.get("k2").submission_id == "sub-1"  # type: ignore[union-attr]
    assert ledger.receipt_rate() == (2, 2)


# ---------------------------------------------------------------------------
# the console's launches outlive the console
# ---------------------------------------------------------------------------


def test_a_restarted_console_reattaches_or_marks_interrupted(tmp_path: Path) -> None:
    from pheasant_lab.console.launcher import Launcher

    home = tmp_path / ".console" / "launches"
    home.mkdir(parents=True)
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        for launch_id, pid in (("launch-dead", 999_999_9), ("launch-alive", alive.pid)):
            (home / f"{launch_id}.json").write_text(
                json.dumps(
                    {
                        "launch_id": launch_id,
                        "kind": "pipeline",
                        "argv": ["run"],
                        "config": "configs/demo.yaml",
                        "status": "running",
                        "started_at": time.time(),
                        "pid": pid,
                    }
                )
            )
        launcher = Launcher(REPO_ROOT, tmp_path)
        assert launcher.get("launch-dead").status == "interrupted"
        assert launcher.get("launch-alive").status == "running"
        saved = json.loads((home / "launch-dead.json").read_text())
        assert saved["status"] == "interrupted", "the verdict was not made durable"
    finally:
        alive.kill()
        alive.wait()
