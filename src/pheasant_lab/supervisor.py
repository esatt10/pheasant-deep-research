"""``pheasant-lab run``: the whole pipeline, durable to failure.

The pipeline is the CLI's own sequence - collect, freeze, evaluate, replay,
report, verify - and every stage already resumes: collection from its last
round boundary, evaluation from the answers it recorded, freezing and the rest
because they are idempotent over the raw trace. What was missing is something
to *notice* a stage died and resume it. This is that, and nothing more.

Each stage runs as its own child process, so a crash in one - an unhandled
exception, an out-of-memory kill, a ``SIGKILL`` - is an exit status here
rather than the end of the run. The exit status decides:

* ``0`` and ``1`` - done ("1" is the CLI's *completed, and a decision
  failed*: a gate, a non-inferiority test - a result, not a crash). Next stage.
* ``2`` - refused before doing work (a config, a missing capability, a
  drifted snapshot). Asking again cannot change the answer. Stop.
* ``130`` - interrupted by a person. Stop; ``run --resume`` continues.
* anything else - a crash. Resume the same stage, with backoff, up to the
  attempt limit. A stage that crashes every time stops the run *resumable*
  rather than marked failed, because the fix is usually outside the lab.

Stages the checkpoint records as completed are skipped, so ``run --resume``
picks up wherever any previous process - this one, a console, a terminal -
left off. Every attempt is appended to ``supervisor/attempts.jsonl`` in the
run directory, and ``supervisor/lock`` stops two supervisors driving one run
at once (a lock whose holder is dead is taken over).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

STAGES = ("collect", "freeze-benchmark", "evaluate", "replay", "report", "verify")
#: Stages whose completion the checkpoint records, and which are skipped once done.
CHECKPOINTED = ("collect", "freeze-benchmark", "evaluate")
EXIT_OK, EXIT_FINDINGS, EXIT_REFUSED, EXIT_CRASHED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
#: Under the run directory, and outside what ``integrity/checksums.sha256``
#: covers: the supervisor appends to its log after the stages it supervises
#: have checksummed the run, and ``verify`` would call that tampering.
SUPERVISOR_DIR = "supervisor"
LOCK_NAME = "lock"
LOG_NAME = "attempts.jsonl"


class RunLocked(RuntimeError):
    """Another live supervisor is driving this run."""


def _now() -> str:
    from .lifecycle import isonow

    return isonow()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    """One supervisor per run. A lock whose holder died is taken over."""

    def __init__(self, root: Path) -> None:
        self.path = root / SUPERVISOR_DIR / LOCK_NAME
        self.held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    holder = int(self.path.read_text(encoding="utf-8").split()[0])
                except (OSError, ValueError, IndexError):
                    holder = 0
                if holder and holder != os.getpid() and _alive(holder):
                    raise RunLocked(
                        f"run is already being driven by process {holder} "
                        f"({self.path}); stop it first, or wait for it"
                    ) from None
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(f"{os.getpid()} {_now()}\n")
            self.held = True
            return
        raise RunLocked(f"could not take {self.path}")

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


def _record(root: Path | None, row: dict[str, Any]) -> None:
    if root is None:
        return
    (root / SUPERVISOR_DIR).mkdir(parents=True, exist_ok=True)
    with (root / SUPERVISOR_DIR / LOG_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": _now(), **row}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _completed(root: Path | None) -> set[str]:
    if root is None:
        return set()
    try:
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        name
        for name, entry in (state.get("stages") or {}).items()
        if (entry or {}).get("status") == "completed"
    }


def _runs(output_root: Path) -> set[str]:
    if not output_root.is_dir():
        return set()
    return {p.name for p in output_root.glob("run-*") if (p / "run-manifest.json").is_file()}


def crashed(code: int) -> bool:
    return code not in (EXIT_OK, EXIT_FINDINGS, EXIT_REFUSED, EXIT_INTERRUPTED)


def supervise(
    common: Sequence[str],
    *,
    output_root: Path,
    run_id: str | None = None,
    topic: str | None = None,
    arms: str | None = None,
    max_cost_usd: float | None = None,
    attempts: int = 3,
    backoff_seconds: float = 2.0,
    command: Sequence[str] | None = None,
    cwd: Path | None = None,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, str | None]:
    """Drive a run to the end of the pipeline. Returns ``(exit code, run id)``."""

    prefix = list(command or [sys.executable, "-m", "pheasant_lab.cli"])
    root = output_root / run_id if run_id else None
    lock = RunLock(root) if root is not None else None
    if lock is not None:
        lock.acquire()
    worst = EXIT_OK
    try:
        done = _completed(root)
        if run_id and done:
            out(f"resuming {run_id}: already completed {', '.join(s for s in STAGES if s in done)}")
        for stage in STAGES:
            if stage in CHECKPOINTED and stage in done:
                continue
            attempt = 0
            while True:
                attempt += 1
                argv = [stage, *common]
                if stage == "collect":
                    if topic:
                        argv += ["--topic", topic]
                    if max_cost_usd is not None:
                        argv += ["--max-cost-usd", str(max_cost_usd)]
                    if run_id:
                        argv += ["--resume", run_id]
                else:
                    if run_id is None:
                        out("no run directory exists; nothing to continue")
                        return EXIT_CRASHED, None
                    argv += ["--run", run_id]
                    if stage == "evaluate" and arms:
                        argv += ["--arms", arms]
                out(
                    f"$ pheasant-lab {' '.join(argv)}"
                    + (f"   (attempt {attempt})" if attempt > 1 else "")
                )
                before = _runs(output_root) if run_id is None else set()
                started = time.monotonic()
                code = subprocess.call([*prefix, *argv], cwd=cwd)
                if run_id is None:
                    fresh = sorted(_runs(output_root) - before)
                    if fresh:
                        run_id = fresh[-1]
                        root = output_root / run_id
                        lock = RunLock(root)
                        lock.acquire()
                        out(f"run: {run_id}")
                _record(
                    root,
                    {
                        "stage": stage,
                        "attempt": attempt,
                        "exit_code": code,
                        "seconds": round(time.monotonic() - started, 3),
                        "outcome": "crashed" if crashed(code) else "exited",
                    },
                )
                if not crashed(code):
                    break
                if attempt >= attempts:
                    out(
                        f"{stage} crashed {attempt} time(s) (last exit {code}); stopping. "
                        f"The run is resumable: pheasant-lab run --resume {run_id or '<run>'}"
                    )
                    return EXIT_CRASHED, run_id
                wait = backoff_seconds * 2 ** (attempt - 1)
                out(f"{stage} crashed (exit {code}); resuming it in {wait:.0f}s")
                sleep(wait)
            if code in (EXIT_REFUSED, EXIT_INTERRUPTED):
                return code, run_id
            worst = worst or code
        return worst, run_id
    finally:
        if lock is not None:
            lock.release()
