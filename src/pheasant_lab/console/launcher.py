"""Starting and stopping lab commands from the console.

The console is a launcher, not a second runtime. Every run it starts is the
ordinary CLI in a child process - the same argv a person would type, the same
config resolution, the same budget guard - so a run started from a browser and
one started from a terminal are indistinguishable in their trace.

**A launch outlives the console.** The child is started in its own session,
writes its output to a log file rather than a pipe, and its launch record is a
file - so closing, crashing or restarting the console neither stops the run
(a pipe would: the child's next ``print`` after the reader died is a
``BrokenPipeError``) nor forgets it. A restarted console reads the records
back, re-attaches to children still running and marks the rest
``interrupted``. Launch bookkeeping lives under ``<output root>/.console/``.

A *pipeline* is ``pheasant-lab run``: one supervisor child that drives
collect, freeze, evaluate, replay, report and verify, resuming any stage that
crashes (:mod:`pheasant_lab.supervisor`). ``resume`` is the same supervisor
pointed at an existing run; it skips every stage the run's checkpoint records
as completed.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..lifecycle import durable_write_text
from .projection import list_runs

#: The CLI commands a launch may run. Anything else is refused: the console
#: binds loopback by default, and still has no business running arbitrary argv.
SINGLE_COMMANDS = {"collect", "audit", "freeze-benchmark", "evaluate", "replay", "verify", "report"}
REFUSED_EXIT = 2
CRASHED_EXIT = 3
TAIL_LINES = 60


@dataclass
class Launch:
    launch_id: str
    kind: str
    argv: list[str]
    started_at: float
    config: str
    run_id: str | None = None
    status: str = "running"
    exit_code: int | None = None
    step: str | None = None
    finished_at: float | None = None
    pid: int | None = None
    log: str | None = None
    output: deque = field(default_factory=lambda: deque(maxlen=400))
    process: subprocess.Popen | None = None
    stop_requested: bool = False
    #: A person's name for the run this launch makes; applied once the run exists.
    label: str | None = None

    def record(self) -> dict[str, Any]:
        """What is persisted: everything but the live handles."""

        return {
            "launch_id": self.launch_id,
            "kind": self.kind,
            "argv": self.argv,
            "config": self.config,
            "run_id": self.run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "step": self.step,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "log": self.log,
            "stop_requested": self.stop_requested,
            "label": self.label,
        }

    def as_dict(self) -> dict[str, Any]:
        row = self.record()
        row["output_tail"] = list(self.output)[-TAIL_LINES:]
        return row


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def status_for(code: int, *, stopped: bool) -> str:
    # The CLI's exit 1 is "the run completed and a decision failed" (a gate,
    # the non-inferiority test) - not a crash, and the console must not call
    # it one. Only 2 is a refusal before work, and 3 a crash the supervisor
    # gave up resuming - which leaves the run resumable, not lost.
    if stopped:
        return "stopped"
    return {
        0: "succeeded",
        1: "completed_with_findings",
        REFUSED_EXIT: "refused",
        CRASHED_EXIT: "crashed_resumable",
        130: "stopped",
    }.get(code, "failed")


class Launcher:
    def __init__(
        self,
        project_root: Path,
        output_root: Path,
        *,
        extra_env: Callable[[], dict[str, str]] | None = None,
        on_run: Callable[[str, Launch], None] | None = None,
    ) -> None:
        self.project_root = project_root
        self.output_root = output_root
        #: Tokens stored for connections, handed to every child by name.
        self.extra_env = extra_env
        #: Called once a launch's run directory exists (to apply its label).
        self.on_run = on_run
        self.home = output_root / ".console" / "launches"
        self._launches: dict[str, Launch] = {}
        self._lock = threading.Lock()
        self._restore()

    # -- public ------------------------------------------------------------
    def launch(
        self,
        *,
        kind: str,
        config: str,
        overrides: list[str] | None = None,
        topic: str | None = None,
        arms: str | None = None,
        run_id: str | None = None,
        max_cost_usd: float | None = None,
        label: str | None = None,
    ) -> Launch:
        config_path = self._config_path(config)
        # Relative to the project root, which is the child's cwd: the argv a
        # run records is then the one a person would type, on any machine.
        common = ["--config", str(config_path.relative_to(self.project_root.resolve()))]
        for item in overrides or []:
            if "=" not in item:
                raise ValueError(f"override {item!r} is not a.b=value")
            common += ["--set", item]
        common += self._output_root_override(config_path, overrides or [])
        if kind == "demo":
            argv = ["demo", *common]
            if topic:
                argv += ["--topic", topic]
            if arms:
                argv += ["--arms", arms]
        elif kind in ("pipeline", "resume"):
            argv = ["run", *common]
            if kind == "resume":
                if not run_id:
                    raise ValueError("resume needs a run")
                argv += ["--resume", run_id]
            elif topic:
                argv += ["--topic", topic]
            if arms:
                argv += ["--arms", arms]
            if max_cost_usd is not None:
                argv += ["--max-cost-usd", str(max_cost_usd)]
        elif kind in SINGLE_COMMANDS:
            argv = [kind, *common]
            if kind != "collect":
                if not run_id:
                    raise ValueError(f"{kind} needs a run")
                argv += ["--run", run_id]
            elif topic:
                argv += ["--topic", topic]
            if kind == "evaluate" and arms:
                argv += ["--arms", arms]
        else:
            raise ValueError(f"unknown launch kind {kind!r}")
        if run_id and any(
            launch.run_id == run_id and launch.status == "running" for launch in self._all()
        ):
            raise ValueError(f"{run_id} already has a running launch; stop it first")

        launch_id = f"launch-{uuid.uuid4().hex[:10]}"
        launch = Launch(
            launch_id=launch_id,
            kind=kind,
            argv=argv,
            started_at=time.time(),
            config=str(config_path.relative_to(self.project_root)),
            run_id=run_id,
            step=argv[0],
            log=str(self.home / f"{launch_id}.log"),
            label=(label or "").strip() or None,
        )
        with self._lock:
            self._launches[launch.launch_id] = launch
        self._start(launch)
        return launch

    def stop(self, launch_id: str) -> Launch:
        launch = self.get(launch_id)
        launch.stop_requested = True
        self._save(launch)
        if launch.status == "running" and _alive(launch.pid):
            # SIGINT first, to the whole group: the supervisor and the stage it
            # is running both turn it into a clean "interrupted" with their
            # traces flushed, which a SIGKILL would not give them.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(launch.pid, signal.SIGINT)  # type: ignore[arg-type]
            if launch.process is not None:
                try:
                    launch.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    launch.process.terminate()
        return launch

    def get(self, launch_id: str) -> Launch:
        with self._lock:
            launch = self._launches.get(launch_id)
        if launch is None:
            raise KeyError(launch_id)
        self._tail(launch)
        return launch

    def list(self) -> list[dict[str, Any]]:
        launches = self._all()
        for launch in launches:
            self._tail(launch)
        return [launch.as_dict() for launch in sorted(launches, key=lambda row: -row.started_at)]

    def forget(self, launch_id: str) -> None:
        """Drop a finished launch from memory once its files are deleted."""

        with self._lock:
            launch = self._launches.get(launch_id)
            if launch is not None and launch.status == "running":
                raise ValueError(f"{launch_id} is still running")
            self._launches.pop(launch_id, None)

    def live_runs(self) -> set[str]:
        return {
            launch.run_id
            for launch in self._all()
            if launch.run_id and launch.status == "running" and _alive(launch.pid)
        }

    def run_command(self, argv: list[str], *, timeout: float = 120.0) -> tuple[int, str]:
        """Run a short, read-only command (plan, doctor) and capture its output."""

        code, stdout, stderr = self.run_command_streams(argv, timeout=timeout)
        return code, stdout + stderr

    def run_command_streams(
        self, argv: list[str], *, timeout: float = 120.0
    ) -> tuple[int, str, str]:
        """Run a short command while keeping machine output separate from logs."""

        completed = subprocess.run(
            self._python(argv),
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self._env(),
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""

    def config_path(self, config: str) -> Path:
        return self._config_path(config)

    def _output_root_override(self, config_path: Path, overrides: list[str]) -> list[str]:
        """Point a child at this console's output root when its config names another.

        A console started with ``--runs`` elsewhere (the Docker image's runs
        volume, say) would otherwise launch runs into the config's own
        ``experiment.output_root`` and never see them. The output root is left
        out of the config digest, so saying it costs a run nothing.
        """

        if any(item.startswith("experiment.output_root=") for item in overrides):
            return []
        from ..settings import load_config

        try:
            config = load_config(
                config_path,
                overrides=dict(item.split("=", 1) for item in overrides),
                project_root=self.project_root,
                env_file=".env",
            )
        except Exception:  # the child refuses it in its own words
            return []
        configured = Path(config.experiment.output_root)
        if not configured.is_absolute():
            configured = self.project_root / configured
        if configured.resolve() == self.output_root.resolve():
            return []
        return ["--set", f"experiment.output_root={self.output_root.resolve()}"]

    # -- internals ---------------------------------------------------------
    def _all(self) -> list[Launch]:
        with self._lock:
            return list(self._launches.values())

    def _config_path(self, config: str) -> Path:
        path = (self.project_root / config).resolve()
        if not path.is_file() or self.project_root.resolve() not in path.parents:
            raise ValueError(f"{config!r} is not a config file inside {self.project_root}")
        return path

    def _python(self, argv: list[str]) -> list[str]:
        return [sys.executable, "-m", "pheasant_lab.cli", *argv]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.extra_env is not None:
            env.update(self.extra_env())
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _save(self, launch: Launch) -> None:
        durable_write_text(
            self.home / f"{launch.launch_id}.json",
            json.dumps(launch.record(), indent=2, sort_keys=True) + "\n",
        )

    def _start(self, launch: Launch) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        before = {row["run_id"] for row in list_runs(self.output_root)}
        log = Path(str(launch.log))
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"$ pheasant-lab {' '.join(launch.argv)}\n")
            handle.flush()
            # Its own session, and a file rather than a pipe: neither the
            # console's Ctrl-C nor the console's death reaches the run.
            process = subprocess.Popen(
                self._python(launch.argv),
                cwd=self.project_root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                env=self._env(),
                start_new_session=True,
            )
        launch.process = process
        launch.pid = process.pid
        self._save(launch)
        threading.Thread(
            target=self._watch,
            args=(launch, before),
            name=f"console-{launch.launch_id}",
            daemon=True,
        ).start()

    def _watch(self, launch: Launch, before: set[str]) -> None:
        """Surface the run as soon as its directory exists, then wait for the end.

        Watched apart from the output: a command can be silent for the whole
        of a barrier wait, which is exactly when someone wants to watch it.
        """

        process = launch.process
        while process is not None and process.poll() is None:
            if launch.run_id is None:
                launch.run_id = self._new_run(before)
                if launch.run_id:
                    self._save(launch)
                    self._announce(launch)
            time.sleep(0.25)
        if launch.run_id is None:
            launch.run_id = self._new_run(before)
            if launch.run_id:
                self._announce(launch)
        code = process.wait() if process is not None else -1
        launch.exit_code = code
        launch.finished_at = time.time()
        launch.status = status_for(code, stopped=launch.stop_requested)
        self._tail(launch)
        self._save(launch)

    def _announce(self, launch: Launch) -> None:
        if self.on_run is not None and launch.run_id and launch.kind not in SINGLE_COMMANDS:
            with contextlib.suppress(Exception):
                self.on_run(launch.run_id, launch)

    def _follow(self, launch: Launch) -> None:
        """A child a previous console started: poll it, since it is not ours to wait on."""

        while _alive(launch.pid):
            time.sleep(1.0)
        launch.finished_at = launch.finished_at or time.time()
        if launch.status == "running":
            # Not our child, so no exit status reaches us - but a supervised
            # run wrote every stage's exit to its attempts log.
            launch.status = "stopped" if launch.stop_requested else self._outcome(launch)
        self._tail(launch)
        self._save(launch)

    def _outcome(self, launch: Launch) -> str:
        if not launch.run_id:
            return "ended"
        log = self.output_root / launch.run_id / "supervisor" / "attempts.jsonl"
        try:
            last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        except (OSError, ValueError, IndexError):
            return "ended"
        code = int(last.get("exit_code") or 0)
        launch.exit_code = code
        if last.get("outcome") == "crashed":
            return "crashed_resumable"
        if last.get("stage") == "verify" or code in (REFUSED_EXIT, 130):
            return status_for(code, stopped=False)
        return "ended"

    def _restore(self) -> None:
        if not self.home.is_dir():
            return
        for path in sorted(self.home.glob("launch-*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            launch = Launch(
                launch_id=str(row["launch_id"]),
                kind=str(row.get("kind") or ""),
                argv=list(row.get("argv") or []),
                started_at=float(row.get("started_at") or 0.0),
                config=str(row.get("config") or ""),
                run_id=row.get("run_id"),
                status=str(row.get("status") or "ended"),
                exit_code=row.get("exit_code"),
                step=row.get("step"),
                finished_at=row.get("finished_at"),
                pid=row.get("pid"),
                log=row.get("log"),
                stop_requested=bool(row.get("stop_requested")),
                label=row.get("label"),
            )
            if launch.status == "running":
                if _alive(launch.pid):
                    threading.Thread(target=self._follow, args=(launch,), daemon=True).start()
                else:
                    # The console went down and so did its child, or the
                    # machine restarted under both. The run is on disk and
                    # `resume` picks it up where its checkpoint says.
                    launch.status = "interrupted"
                    launch.finished_at = launch.finished_at or time.time()
                    self._save(launch)
            self._tail(launch)
            self._launches[launch.launch_id] = launch

    def _tail(self, launch: Launch) -> None:
        if not launch.log:
            return
        path = Path(launch.log)
        if not path.is_file():
            return
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 64 * 1024))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return
        lines = text.splitlines()
        if size > 64 * 1024:
            lines = lines[1:]
        launch.output = deque(lines[-400:], maxlen=400)
        if launch.status == "running" and launch.kind in ("pipeline", "resume"):
            for line in reversed(lines):
                if line.startswith("$ pheasant-lab "):
                    launch.step = line.split()[2]
                    break

    def _new_run(self, before: set[str]) -> str | None:
        fresh = [row for row in list_runs(self.output_root) if row["run_id"] not in before]
        return fresh[0]["run_id"] if fresh else None
