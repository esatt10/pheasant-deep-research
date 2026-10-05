"""Starting and stopping lab commands from the console.

The console is a launcher, not a second runtime. Every run it starts is the
ordinary CLI in a child process - the same argv a person would type, the same
config resolution, the same budget guard - so a run started from a browser and
one started from a terminal are indistinguishable in their trace, and the
console can be closed without stopping the run. What it adds is bookkeeping: a
launch id, the argv, the exit status, a tail of the output, and which run
directory the launch produced.

A *pipeline* is the CLI's own sequence (collect, freeze, evaluate, replay,
report, verify) run one command at a time against the run ``collect``
created, stopping at the first refusal - the same order ``demo`` uses.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .projection import list_runs

#: The CLI commands a launch may run. Anything else is refused: the console
#: binds loopback by default, and still has no business running arbitrary argv.
SINGLE_COMMANDS = {"collect", "audit", "freeze-benchmark", "evaluate", "replay", "verify", "report"}
PIPELINE = ("freeze-benchmark", "evaluate", "replay", "report", "verify")
REFUSED_EXIT = 2


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
    output: deque = field(default_factory=lambda: deque(maxlen=400))
    process: subprocess.Popen | None = None
    stop_requested: bool = False

    def as_dict(self) -> dict[str, Any]:
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
            "output_tail": list(self.output)[-60:],
        }


class Launcher:
    def __init__(self, project_root: Path, output_root: Path) -> None:
        self.project_root = project_root
        self.output_root = output_root
        self._launches: dict[str, Launch] = {}
        self._lock = threading.Lock()

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
    ) -> Launch:
        config_path = self._config_path(config)
        # Relative to the project root, which is the child's cwd: the argv a
        # run records is then the one a person would type, on any machine.
        common = ["--config", str(config_path.relative_to(self.project_root.resolve()))]
        for item in overrides or []:
            if "=" not in item:
                raise ValueError(f"override {item!r} is not a.b=value")
            common += ["--set", item]
        if kind == "demo":
            argv = ["demo", *common]
            if topic:
                argv += ["--topic", topic]
            if arms:
                argv += ["--arms", arms]
        elif kind == "pipeline":
            argv = ["collect", *common]
            if topic:
                argv += ["--topic", topic]
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

        launch = Launch(
            launch_id=f"launch-{uuid.uuid4().hex[:10]}",
            kind=kind,
            argv=argv,
            started_at=time.time(),
            config=str(config_path.relative_to(self.project_root)),
            run_id=run_id,
        )
        with self._lock:
            self._launches[launch.launch_id] = launch
        threading.Thread(
            target=self._drive,
            args=(launch, common, arms),
            name=f"console-{launch.launch_id}",
            daemon=True,
        ).start()
        return launch

    def stop(self, launch_id: str) -> Launch:
        launch = self.get(launch_id)
        launch.stop_requested = True
        process = launch.process
        if process is not None and process.poll() is None:
            # SIGINT first: the CLI turns it into a clean "interrupted" with
            # its trace flushed, which a SIGKILL would not give it.
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        return launch

    def get(self, launch_id: str) -> Launch:
        with self._lock:
            launch = self._launches.get(launch_id)
        if launch is None:
            raise KeyError(launch_id)
        return launch

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            launches = list(self._launches.values())
        return [launch.as_dict() for launch in sorted(launches, key=lambda row: -row.started_at)]

    def run_command(self, argv: list[str], *, timeout: float = 120.0) -> tuple[int, str]:
        """Run a short, read-only command (plan, doctor) and capture its output."""

        completed = subprocess.run(
            self._python(argv),
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self._env(),
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")

    def config_path(self, config: str) -> Path:
        return self._config_path(config)

    # -- internals ---------------------------------------------------------
    def _config_path(self, config: str) -> Path:
        path = (self.project_root / config).resolve()
        if not path.is_file() or self.project_root.resolve() not in path.parents:
            raise ValueError(f"{config!r} is not a config file inside {self.project_root}")
        return path

    def _python(self, argv: list[str]) -> list[str]:
        return [sys.executable, "-m", "pheasant_lab.cli", *argv]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _drive(self, launch: Launch, common: list[str], arms: str | None) -> None:
        before = {row["run_id"] for row in list_runs(self.output_root)}
        code = self._step(launch, launch.argv)
        if launch.run_id is None:
            launch.run_id = self._new_run(before)
        if launch.kind == "pipeline" and code in (0, 1) and launch.run_id:
            for command in PIPELINE:
                if launch.stop_requested:
                    break
                argv = [command, *common, "--run", launch.run_id]
                if command == "evaluate" and arms:
                    argv += ["--arms", arms]
                step = self._step(launch, argv)
                code = code or step
                if step == REFUSED_EXIT:
                    break
        launch.exit_code = code
        launch.finished_at = time.time()
        # The CLI's exit 1 is "the run completed and a decision failed" (a
        # gate, the non-inferiority test) - not a crash, and the console
        # must not call it one. Only 2 is a refusal before work.
        launch.status = (
            "stopped"
            if launch.stop_requested
            else "succeeded"
            if code == 0
            else "refused"
            if code == REFUSED_EXIT
            else "completed_with_findings"
            if code == 1
            else "failed"
        )

    def _step(self, launch: Launch, argv: list[str]) -> int:
        launch.step = argv[0]
        launch.output.append(f"$ pheasant-lab {' '.join(argv)}")
        before = {row["run_id"] for row in list_runs(self.output_root)}
        process = subprocess.Popen(
            self._python(argv),
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=self._env(),
        )
        launch.process = process
        if launch.run_id is None:
            # Surface the run as soon as its directory exists, so the live
            # view can attach while collection is still going. Watched apart
            # from the output: a command can be silent for the whole of a
            # barrier wait, which is exactly when someone wants to watch it.
            threading.Thread(
                target=self._find_run, args=(launch, process, before), daemon=True
            ).start()
        assert process.stdout is not None
        for line in process.stdout:
            launch.output.append(line.rstrip("\n"))
        return process.wait()

    def _find_run(self, launch: Launch, process: subprocess.Popen, before: set[str]) -> None:
        while launch.run_id is None:
            launch.run_id = self._new_run(before)
            if launch.run_id or process.poll() is not None:
                break
            time.sleep(0.25)

    def _new_run(self, before: set[str]) -> str | None:
        fresh = [row for row in list_runs(self.output_root) if row["run_id"] not in before]
        return fresh[0]["run_id"] if fresh else None
