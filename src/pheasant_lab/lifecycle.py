"""Run directories, the run manifest, and the resumable checkpoint.

``state.json`` is a checkpoint, not history. The event stream reconstructs
state; the checkpoint exists so a resumed run does not redo work it already
paid for. Where the two disagree, the events win - which is why ``replay``
rebuilds every projection and every metric from the events alone.

Durability, stated once. Every whole-file write here (the manifest, the
checkpoint, checksums) goes through :func:`durable_write_text`: a temp file
unique to the writer, flushed and ``fsync``-ed, renamed over the target, and
the directory ``fsync``-ed so the rename itself survives a power loss. A crash
leaves the old file or the new one, never half of either. And a checkpoint is
a *barrier*: :class:`RunState` syncs the append-only raw files before it
writes, so a checkpoint can never claim progress the raw trace might lose.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import ids
from .hashing import digest, digest_file
from .redaction import Redactor
from .settings import LabConfig

MANIFEST_NAME = "run-manifest.json"
STATE_NAME = "state.json"
RESOLVED_CONFIG_NAME = "resolved-config.redacted.yaml"

RAW_FILES = (
    "events.jsonl",
    "spans.jsonl",
    "errors.jsonl",
    "mcp-calls.redacted.jsonl",
    "sources.jsonl",
    "claims.jsonl",
    "ingest-receipts.jsonl",
    "questions.jsonl",
    "answers.jsonl",
    "proof-events.jsonl",
)

BENCHMARK_FILES = (
    "questions.jsonl",
    "expected-facts.jsonl",
    "expected-evidence.jsonl",
    "exclusions.jsonl",
    "abstention-cases.jsonl",
    "cohort-membership.jsonl",
    "benchmark-manifest.json",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def isonow() -> str:
    return utcnow().isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class RunPaths:
    """Every path a run writes to. Created once, on entry."""

    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def state(self) -> Path:
        return self.root / STATE_NAME

    @property
    def resolved_config(self) -> Path:
        return self.root / RESOLVED_CONFIG_NAME

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def source_metadata(self) -> Path:
        return self.artifacts / "source-metadata"

    @property
    def permitted_content(self) -> Path:
        return self.artifacts / "permitted-content"

    @property
    def benchmark(self) -> Path:
        return self.root / "benchmark"

    @property
    def projections(self) -> Path:
        return self.root / "projections"

    @property
    def metrics(self) -> Path:
        return self.root / "metrics"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def integrity(self) -> Path:
        return self.root / "integrity"

    def raw_file(self, name: str) -> Path:
        return self.raw / name

    def ensure(self) -> RunPaths:
        for directory in (
            self.root,
            self.raw,
            self.artifacts,
            self.source_metadata,
            self.permitted_content,
            self.benchmark,
            self.projections,
            self.metrics,
            self.reports,
            self.integrity,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def run_paths(output_root: str | Path, run_id: str) -> RunPaths:
    return RunPaths(Path(output_root) / run_id)


def environment_fingerprint() -> dict[str, Any]:
    """What about this laptop could change a result.

    Deliberately not a full package list: a fingerprint nobody can reproduce
    is a fingerprint nobody compares.
    """

    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(terse=True),
        "machine": platform.machine(),
    }


def build_manifest(
    *,
    config: LabConfig,
    run_id: str,
    nonce: str,
    config_digest: str,
    redactor: Redactor,
    command: str,
    package_version: str,
) -> dict[str, Any]:
    """The document every later comparison is judged against."""

    return {
        "schema_version": 1,
        "run_id": run_id,
        "experiment_name": config.experiment.name,
        "created_at": isonow(),
        "nonce": nonce,
        "config_digest": config_digest,
        "package_version": package_version,
        "command": command,
        "seed": config.experiment.seed,
        "arms": list(config.arms),
        "topics": [topic.id for topic in config.topics],
        "cost_budget_usd": config.experiment.cost_budget_usd,
        "runtime_budget_minutes": config.experiment.runtime_budget_minutes,
        "models": {
            role: {
                "provider": spec.provider,
                "model": spec.model,
                "reasoning_effort": spec.reasoning_effort,
                "temperature": spec.temperature,
                "max_output_tokens": spec.max_output_tokens,
                "tool_call_limit": spec.tool_call_limit,
            }
            for role, spec in sorted(config.models.items())
        },
        "pheasant": {
            "transport": config.pheasant.transport,
            "knowledge_base": config.pheasant.knowledge_base,
            "source_name": config.pheasant.source_name,
            "protocol_version": config.pheasant.protocol_version,
            "capabilities": {
                name: spec.tool for name, spec in sorted(config.pheasant.capabilities.items())
            },
        },
        "replay": config.replay.model_dump(mode="json"),
        "privacy": config.privacy.model_dump(mode="json"),
        "source_files": config.source_files,
        "overrides": config.overrides,
        "environment": environment_fingerprint(),
        "resolved_config": config.redacted(redactor),
        "status": "started",
    }


def _fsync_directory(directory: Path) -> None:
    """Make a rename durable. A no-op where directories cannot be opened (Windows)."""

    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def durable_write_text(path: Path, text: str) -> None:
    """Replace ``path`` atomically and durably.

    The temp name is unique to this writer - a fixed ``.partial`` is a
    collision waiting for a second process - and a failed write unlinks its own
    temp, or unique names would turn one orphan into one per attempt.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{ids.new_nonce(4)}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def repair_torn_tail(path: Path, quarantine: Path) -> dict[str, Any] | None:
    """Cut a torn final line off an append-only file, keeping the fragment.

    Every record is written as one line ending in a newline, so a file whose
    last byte is not a newline was interrupted mid-append - a killed process,
    a full disk, a power cut. Left alone, the next reader refuses the whole
    file and the run cannot resume. Truncated silently, a record disappears.
    So the fragment is moved to ``quarantine`` and the cut is reported, and
    the caller records it in the trace. Only the *tail* is ever repaired: a
    malformed line in the middle is not a crash, it is damage, and stays an
    error for ``verify`` to report.
    """

    if not path.is_file():
        return None
    size = path.stat().st_size
    if size == 0:
        return None
    with path.open("rb+") as handle:
        handle.seek(size - 1)
        if handle.read(1) == b"\n":
            return None
        # Walk back to the last complete line.
        position = size
        chunk = 65536
        keep = 0
        while position > 0:
            start = max(0, position - chunk)
            handle.seek(start)
            block = handle.read(position - start)
            index = block.rfind(b"\n")
            if index >= 0:
                keep = start + index + 1
                break
            position = start
        handle.seek(keep)
        fragment = handle.read()
        quarantine.mkdir(parents=True, exist_ok=True)
        stamp = isonow().replace(":", "").replace("-", "")
        saved = quarantine / f"{path.name}.{stamp}.fragment"
        saved.write_bytes(fragment)
        handle.truncate(keep)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "file": path.name,
        "kept_bytes": keep,
        "fragment_bytes": len(fragment),
        "fragment": saved.name,
    }


class RunState:
    """The resumable checkpoint.

    Every mutation writes the whole file through :func:`durable_write_text`.
    ``barrier`` - the tracer's ``sync`` once a session holds one - runs first,
    so the raw trace is on disk before any checkpoint that depends on it.
    """

    def __init__(
        self,
        path: Path,
        data: dict[str, Any] | None = None,
        *,
        barrier: Any = None,
    ) -> None:
        self.path = path
        self.data: dict[str, Any] = data if data is not None else {}
        self.barrier = barrier

    @classmethod
    def load(cls, path: Path) -> RunState:
        if path.is_file():
            return cls(path, json.loads(path.read_text(encoding="utf-8")))
        return cls(path, {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def update(self, **values: Any) -> None:
        self.data.update(values)
        self.save()

    def mark_stage(self, stage: str, status: str, **detail: Any) -> None:
        stages = self.data.setdefault("stages", {})
        stages[stage] = {"status": status, "at": isonow(), **detail}
        self.save()

    def stage_status(self, stage: str) -> str | None:
        entry = self.data.get("stages", {}).get(stage)
        return entry.get("status") if entry else None

    def completed(self, stage: str) -> bool:
        return self.stage_status(stage) == "completed"

    def save(self) -> None:
        if self.barrier is not None:
            self.barrier()
        durable_write_text(
            self.path, json.dumps(self.data, indent=2, sort_keys=True, default=str) + "\n"
        )


def write_json(path: Path, payload: Any) -> None:
    durable_write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_checksums(paths: RunPaths) -> dict[str, str]:
    """Digest every file a run produced, into ``integrity/checksums.sha256``.

    Excludes the checksum file itself, the DuckDB projection and the
    supervisor's own bookkeeping: the projection is derived and rebuilt, and
    the supervisor appends after the stages it drives have checksummed, so
    digesting either would make an honest run look tampered with.
    """

    checksums: dict[str, str] = {}
    for file in sorted(paths.root.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(paths.root).as_posix()
        if relative.startswith(("integrity/", "projections/", "supervisor/")):
            continue
        checksums[relative] = digest_file(file)
    paths.integrity.mkdir(parents=True, exist_ok=True)
    lines = [f"{value.removeprefix('sha256:')}  {name}" for name, value in checksums.items()]
    durable_write_text(paths.integrity / "checksums.sha256", "\n".join(lines) + "\n")
    return checksums


def read_checksums(paths: RunPaths) -> dict[str, str]:
    file = paths.integrity / "checksums.sha256"
    if not file.is_file():
        return {}
    out: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value, _, name = line.partition("  ")
        out[name] = "sha256:" + value
    return out


def manifest_digest(paths: RunPaths) -> str:
    """The digest a completed run is archived under."""

    return digest(read_json(paths.manifest))
