"""Logging control for the console: what is kept, for how long, and deleting it.

Three kinds of thing accumulate under the console's output root, and they are
not equally disposable:

* **launch logs** (``.console/launches/launch-*.log`` and their records) - a
  child process's stdout. Pure diagnostics: delete freely once it has ended.
* **projections** (``<run>/projections/``) - the DuckDB file and its summary,
  rebuilt from ``raw/`` by ``pheasant-lab replay``. Usually most of a run's
  bytes, and safe to drop for exactly that reason.
* **runs** (``<run>/``) - the authoritative record. Rule 5 holds here: no row
  in ``raw/*.jsonl`` is ever rewritten or trimmed, so a run is kept whole or
  deleted whole. Partial pruning of a raw trace would leave a run whose
  checksums, sequence continuity and replay all fail, which is worse than no
  run at all.

Nothing is deleted while a launch is still writing to it. Every deletion -
by hand or by the retention policy - is appended to
``.console/retention-log.jsonl``, so "where did that run go" has an answer.

The policy lives in ``.console/retention.json`` beside the launch records it
governs: console bookkeeping, not experiment configuration, so it never moves
a run's config digest.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..lifecycle import durable_write_text
from .projection import in_scope

#: Directory names inside a run, mapped to how a person thinks about them.
CATEGORIES: dict[str, str] = {
    "raw": "trace",
    "projections": "projection",
    "reports": "reports",
    "metrics": "metrics",
    "benchmark": "benchmark",
    "supervisor": "supervisor",
    "integrity": "integrity",
    "logs": "logs",
}
#: Files a person may read through the log viewer: text, never a database.
READABLE_SUFFIXES = frozenset({".log", ".jsonl", ".json", ".md", ".txt", ".csv", ".yaml"})
#: The most a single read returns; a viewer pages through the rest.
MAX_READ_LINES = 2000
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class RetentionPolicy:
    """How long the console keeps what accumulates under its output root.

    ``None`` means "keep forever" and is every default: a lab that deletes
    runs nobody asked it to delete has lost data, not saved space.
    """

    #: Delete finished launch logs older than this many days.
    launch_log_days: float | None = None
    #: Keep at most this many finished launch logs (newest first).
    max_launch_logs: int | None = None
    #: Drop rebuildable projections of runs not touched for this many days.
    projection_days: float | None = None
    #: Delete whole runs not touched for this many days.
    run_days: float | None = None
    #: Keep at most this many runs (newest first); older ones are deleted.
    max_runs: int | None = None
    #: Never delete a run that has rendered reports, whatever its age.
    protect_reported: bool = True
    #: Runs a person marked "keep": exempt from every rule above.
    kept_runs: list[str] = field(default_factory=list)
    #: Apply on a beat while the console runs, not only when asked.
    auto_apply: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RetentionPolicy:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(f"unknown retention setting(s): {', '.join(unknown)}")
        policy = cls(**{k: v for k, v in payload.items() if k in known})
        policy.validate()
        return policy

    def validate(self) -> None:
        for name in ("launch_log_days", "projection_days", "run_days"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int | float) or value < 0):
                raise ValueError(f"{name} must be a non-negative number of days or null")
        for name in ("max_launch_logs", "max_runs"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a whole number of at least 1, or null")
        if not isinstance(self.kept_runs, list):
            raise ValueError("kept_runs must be a list of run ids")


class LogControl:
    """Inventory, reading, deletion and retention over one output root."""

    def __init__(self, output_root: Path, launcher: Any, *, scope: str | None = None) -> None:
        self.output_root = output_root
        self.launcher = launcher
        #: Only runs made in this deployment are listed, read or deleted.
        self.scope = scope
        self.home = output_root / ".console"
        self.policy_path = self.home / "retention.json"
        self.audit_path = self.home / "retention-log.jsonl"

    # -- policy ------------------------------------------------------------
    def policy(self) -> RetentionPolicy:
        try:
            payload = json.loads(self.policy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RetentionPolicy()
        try:
            return RetentionPolicy.from_payload(payload)
        except (TypeError, ValueError):
            return RetentionPolicy()

    def set_policy(self, payload: dict[str, Any]) -> RetentionPolicy:
        merged = {**asdict(self.policy()), **payload}
        policy = RetentionPolicy.from_payload(merged)
        durable_write_text(
            self.policy_path, json.dumps(asdict(policy), indent=2, sort_keys=True) + "\n"
        )
        return policy

    def keep(self, run_id: str, keep: bool) -> RetentionPolicy:
        self._run_dir(run_id)
        kept = [r for r in self.policy().kept_runs if r != run_id]
        if keep:
            kept.append(run_id)
        return self.set_policy({"kept_runs": sorted(kept)})

    # -- inventory ---------------------------------------------------------
    def inventory(self) -> dict[str, Any]:
        live_runs = self.launcher.live_runs()
        policy = self.policy()
        launches = []
        for row in self.launcher.list():
            log = Path(row["log"]) if row.get("log") else None
            size = log.stat().st_size if log and log.is_file() else 0
            launches.append(
                {
                    "launch_id": row["launch_id"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "run_id": row.get("run_id"),
                    "started_at": row.get("started_at"),
                    "finished_at": row.get("finished_at"),
                    "size_bytes": size,
                    "lines_hint": row.get("output_tail", [])[-1:] or [],
                    "deletable": row["status"] != "running",
                }
            )
        runs = []
        for run in sorted(self._runs(), key=lambda p: -_mtime(p)):
            categories = _category_sizes(run)
            runs.append(
                {
                    "run_id": run.name,
                    "updated_at": _mtime(run),
                    "size_bytes": sum(categories.values()),
                    "categories": categories,
                    "files": _readable_files(run),
                    "live": run.name in live_runs,
                    "reported": (run / "reports" / "summary.md").is_file(),
                    "kept": run.name in policy.kept_runs,
                }
            )
        return {
            "output_root": str(self.output_root),
            "totals": {
                "launch_logs": sum(row["size_bytes"] for row in launches),
                "runs": sum(row["size_bytes"] for row in runs),
                "projections": sum(row["categories"].get("projection", 0) for row in runs),
            },
            "launches": launches,
            "runs": runs,
            "policy": asdict(policy),
            "audit": self.audit(limit=20),
        }

    # -- reading -----------------------------------------------------------
    def read_launch(self, launch_id: str, **options: Any) -> dict[str, Any]:
        launch = self.launcher.get(launch_id)
        if not launch.log:
            raise KeyError(launch_id)
        return read_lines(Path(launch.log), **options) | {"launch_id": launch_id}

    def read_run_file(self, run_id: str, relative: str, **options: Any) -> dict[str, Any]:
        run = self._run_dir(run_id)
        target = (run / relative).resolve()
        if run.resolve() not in target.parents or not target.is_file():
            raise KeyError(relative)
        if target.suffix not in READABLE_SUFFIXES:
            raise ValueError(f"{relative} is not a text log")
        return read_lines(target, **options) | {"run_id": run_id, "path": relative}

    # -- deleting ----------------------------------------------------------
    def delete_launch(self, launch_id: str, *, reason: str = "by hand") -> dict[str, Any]:
        launch = self.launcher.get(launch_id)
        if launch.status == "running":
            raise ValueError(f"{launch_id} is still running; stop it before deleting its log")
        freed = 0
        for path in (Path(launch.log) if launch.log else None, self._record(launch_id)):
            if path is not None and path.is_file():
                freed += path.stat().st_size
                path.unlink()
        self.launcher.forget(launch_id)
        return self._audit("launch_log", launch_id, freed, reason)

    def delete_run(self, run_id: str, *, reason: str = "by hand") -> dict[str, Any]:
        run = self._run_dir(run_id)
        self._refuse_live(run_id)
        freed = _tree_size(run)
        shutil.rmtree(run)
        return self._audit("run", run_id, freed, reason)

    def drop_projection(self, run_id: str, *, reason: str = "by hand") -> dict[str, Any]:
        run = self._run_dir(run_id)
        self._refuse_live(run_id)
        projections = run / "projections"
        freed = _tree_size(projections) if projections.is_dir() else 0
        if projections.is_dir():
            shutil.rmtree(projections)
        return self._audit("projection", run_id, freed, reason)

    def drop_reports(self, run_id: str, *, reason: str = "by hand") -> dict[str, Any]:
        """Delete a run's rendered reports; ``pheasant-lab report`` renders them again.

        Reports are derived from the raw trace like the projection, so they
        are deletable where the trace is not (rule 5).
        """

        run = self._run_dir(run_id)
        self._refuse_live(run_id)
        reports = run / "reports"
        freed = _tree_size(reports) if reports.is_dir() else 0
        if reports.is_dir():
            shutil.rmtree(reports)
        return self._audit("reports", run_id, freed, reason)

    # -- retention ---------------------------------------------------------
    def plan(
        self, policy: RetentionPolicy | None = None, *, now: float | None = None
    ) -> list[dict]:
        """What ``policy`` would delete now, without deleting anything."""

        policy = policy or self.policy()
        now = time.time() if now is None else now
        actions: list[dict[str, Any]] = []
        live = self.launcher.live_runs()

        finished = [
            row
            for row in sorted(self.launcher.list(), key=lambda r: -(r.get("started_at") or 0))
            if row["status"] != "running"
        ]
        for index, row in enumerate(finished):
            ended = row.get("finished_at") or row.get("started_at") or now
            reasons = []
            if policy.launch_log_days is not None and now - ended > policy.launch_log_days * 86400:
                reasons.append(f"older than {policy.launch_log_days:g} day(s)")
            if policy.max_launch_logs is not None and index >= policy.max_launch_logs:
                reasons.append(f"beyond the newest {policy.max_launch_logs}")
            if reasons:
                actions.append(
                    {"kind": "launch_log", "target": row["launch_id"], "reason": "; ".join(reasons)}
                )

        runs = sorted(self._runs(), key=lambda p: -_mtime(p))
        deleted_runs: set[str] = set()
        for index, run in enumerate(runs):
            if run.name in live or run.name in policy.kept_runs:
                continue
            if policy.protect_reported and (run / "reports" / "summary.md").is_file():
                continue
            age = now - _mtime(run)
            reasons = []
            if policy.run_days is not None and age > policy.run_days * 86400:
                reasons.append(f"untouched for more than {policy.run_days:g} day(s)")
            if policy.max_runs is not None and index >= policy.max_runs:
                reasons.append(f"beyond the newest {policy.max_runs}")
            if reasons:
                deleted_runs.add(run.name)
                actions.append({"kind": "run", "target": run.name, "reason": "; ".join(reasons)})
        if policy.projection_days is not None:
            for run in runs:
                if run.name in live or run.name in deleted_runs:
                    continue
                if not (run / "projections").is_dir():
                    continue
                if now - _mtime(run) > policy.projection_days * 86400:
                    actions.append(
                        {
                            "kind": "projection",
                            "target": run.name,
                            "reason": f"untouched for more than {policy.projection_days:g} "
                            "day(s); `pheasant-lab replay` rebuilds it",
                        }
                    )
        return actions

    def apply(self, *, reason: str = "retention policy") -> list[dict[str, Any]]:
        done = []
        for action in self.plan():
            handler = {
                "launch_log": self.delete_launch,
                "run": self.delete_run,
                "projection": self.drop_projection,
            }[action["kind"]]
            try:
                done.append(handler(action["target"], reason=f"{reason}: {action['reason']}"))
            except (KeyError, ValueError, OSError) as exc:
                # A launch that started in between, a run deleted by hand a
                # moment ago: skipped and said, never a reason to stop.
                done.append({**action, "skipped": str(exc)})
        return done

    def audit(self, *, limit: int = 50) -> list[dict[str, Any]]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows = []
        for line in lines[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(rows))

    # -- internals ---------------------------------------------------------
    def _runs(self) -> list[Path]:
        if not self.output_root.is_dir():
            return []
        return [
            path
            for path in self.output_root.iterdir()
            if path.is_dir() and path.name.startswith("run-") and in_scope(path, self.scope)
        ]

    def _run_dir(self, run_id: str) -> Path:
        run = (self.output_root / run_id).resolve()
        if run.parent != self.output_root.resolve() or not run.name.startswith("run-"):
            raise KeyError(run_id)
        if not run.is_dir() or not in_scope(run, self.scope):
            raise KeyError(run_id)
        return run

    def _refuse_live(self, run_id: str) -> None:
        if run_id in self.launcher.live_runs():
            raise ValueError(f"{run_id} has a running launch; stop it before deleting")

    def _record(self, launch_id: str) -> Path:
        return self.launcher.home / f"{launch_id}.json"

    def _audit(self, kind: str, target: str, freed: int, reason: str) -> dict[str, Any]:
        row = {
            "at": time.time(),
            "kind": kind,
            "target": target,
            "freed_bytes": freed,
            "reason": reason,
        }
        self.home.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return row


def read_lines(
    path: Path,
    *,
    offset: int = 0,
    limit: int = 500,
    query: str | None = None,
    level: str | None = None,
    tail: bool = False,
) -> dict[str, Any]:
    """A page of a text log, optionally filtered, with line numbers kept.

    ``level`` keeps lines at or above it (``WARNING`` keeps warnings, errors
    and tracebacks' ``Error:`` lines). ``tail`` returns the last page, which
    is what a person opening a running launch's log wants.
    """

    limit = max(1, min(int(limit), MAX_READ_LINES))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise KeyError(path.name) from exc
    lines = text.splitlines()
    wanted = _level_filter(level)
    needle = (query or "").lower()
    matched = [
        (number, line)
        for number, line in enumerate(lines, start=1)
        if (not needle or needle in line.lower()) and (wanted is None or wanted(line))
    ]
    total = len(matched)
    if tail:
        offset = max(0, total - limit)
    page = matched[offset : offset + limit]
    return {
        "lines": [{"n": number, "text": line} for number, line in page],
        "offset": offset,
        "limit": limit,
        "matched": total,
        "total_lines": len(lines),
        "size_bytes": path.stat().st_size,
        "has_more": offset + limit < total,
    }


def _level_filter(level: str | None):
    if not level:
        return None
    level = level.upper()
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    floor = LEVELS.index(level)
    keep = list(LEVELS[floor:])

    def test(line: str) -> bool:
        upper = line.upper()
        if any(f"{name} " in upper[:40] or f'"LEVEL": "{name}"' in upper for name in keep):
            return True
        # A traceback's last line names the exception, not a level.
        return floor >= LEVELS.index("WARNING") and (
            "TRACEBACK" in upper[:20] or "ERROR:" in upper[:80] or "REFUSED:" in upper[:20]
        )

    return test


def _mtime(path: Path) -> float:
    try:
        newest = path.stat().st_mtime
        state = path / "state.json"
        if state.is_file():
            newest = max(newest, state.stat().st_mtime)
        events = path / "raw" / "events.jsonl"
        if events.is_file():
            newest = max(newest, events.stat().st_mtime)
        return newest
    except OSError:
        return 0.0


def _tree_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _category_sizes(run: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for child in run.iterdir():
        category = CATEGORIES.get(child.name, "other")
        size = _tree_size(child) if child.is_dir() else child.stat().st_size
        sizes[category] = sizes.get(category, 0) + size
    return sizes


def _readable_files(run: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(run.rglob("*")):
        if path.is_file() and path.suffix in READABLE_SUFFIXES:
            rows.append({"path": str(path.relative_to(run)), "size_bytes": path.stat().st_size})
    return rows
