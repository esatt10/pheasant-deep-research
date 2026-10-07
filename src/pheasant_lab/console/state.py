"""What the console keeps between requests, and where.

Four small stores, each the one place its kind of thing is written:

``Workspace``
    The settings draft every surface edits: the config file, its ``--set``
    overrides, the selected topic and connection, and how the next launch is
    capped. One draft per console, so a setting changed over MCP is the
    setting the browser shows, and the other way round. It is console
    bookkeeping (``<output root>/.console/workspace.json``) until a launch
    turns it into an argv - the argv, not this file, is what a run records.

``Connections``
    Named Pheasant connection profiles: transport, URL, knowledge base,
    source and which variable holds the token. Selecting one writes ordinary
    overrides into the workspace (``url=...``, ``knowledge_base=...``), so a
    run's argv still says exactly which region it talked to. The shipped
    profiles live in ``configs/connections.example.yaml``; ones added from a
    surface go to ``connections.local.yaml`` in the local directory.

``Secrets``
    A token entered for a connection. Never returned by any surface (only
    whether one is set), written ``0600``, and handed to a launched run as the
    environment variable the connection names - which the redactor registers
    like any other ``*_TOKEN``, so it reaches no trace.

``Prices``
    ``pricing.local.yaml``: the shipped price list plus prices set from a
    surface. A model with no price is refused before any spend; this is where
    a person gives it one without editing YAML.

The local directory is ``configs/`` by default and ``$PHEASANT_LAB_LOCAL_DIR``
when set - the Docker image points it at a volume, because the image's own
``configs/`` is read-only content that upgrades replace.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..lifecycle import durable_write_text

LOCAL_DIR_ENV = "PHEASANT_LAB_LOCAL_DIR"
CONNECTIONS_FILE = "configs/connections.example.yaml"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def local_dir(project_root: Path) -> Path:
    configured = os.environ.get(LOCAL_DIR_ENV)
    return Path(configured) if configured else project_root / "configs"


def local_file(project_root: Path, name: str) -> tuple[Path, str]:
    """``(absolute path, the value an override names it by)`` for a local file.

    Relative to the project root when it sits inside it - the argv a run
    records then reads the same on any machine - and absolute otherwise.
    """

    path = (local_dir(project_root) / name).resolve()
    try:
        return path, str(path.relative_to(project_root.resolve()))
    except ValueError:
        return path, str(path)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def override_text(value: Any) -> str:
    """A JSON value as the YAML scalar a ``--set`` takes.

    Strings pass through as typed - the CLI parses them as YAML, so ``true``
    and ``0.2`` mean what they mean on a command line. Anything else is
    written as JSON, which is YAML.
    """

    if isinstance(value, str):
        return value
    return json.dumps(value)


# ---------------------------------------------------------------------------
# the settings draft
# ---------------------------------------------------------------------------


class Workspace:
    def __init__(self, home: Path, default_config: str) -> None:
        self.path = home / "workspace.json"
        self.default_config = default_config
        self._lock = threading.Lock()

    def _blank(self) -> dict[str, Any]:
        return {
            "revision": 0,
            "updated_at": None,
            "updated_by": None,
            "config": self.default_config,
            "overrides": {},
            "topic": None,
            "connection": None,
            "launch": {"max_cost_usd": None, "arms": None, "label": None},
        }

    def get(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._blank()
        blank = self._blank()
        return {**blank, **data, "launch": {**blank["launch"], **(data.get("launch") or {})}}

    def update(
        self,
        *,
        by: str,
        config: str | None = None,
        set_values: dict[str, Any] | None = None,
        unset: list[str] | None = None,
        replace_overrides: dict[str, Any] | None = None,
        topic: Any = ...,
        connection: Any = ...,
        launch: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get()
            if expected_revision is not None and expected_revision != current["revision"]:
                raise ValueError(
                    f"the draft moved on (revision {current['revision']}, you had "
                    f"{expected_revision}); reload it and apply your change again"
                )
            if config is not None and config != current["config"]:
                current["config"] = config
                # Overrides written against one file do not apply to another.
                current["overrides"] = {}
                current["topic"] = None
            overrides = dict(current["overrides"])
            if replace_overrides is not None:
                overrides = {k: override_text(v) for k, v in replace_overrides.items()}
            for key, value in (set_values or {}).items():
                if value is None:
                    overrides.pop(key, None)
                else:
                    overrides[key] = override_text(value)
            for key in unset or []:
                overrides.pop(key, None)
            current["overrides"] = dict(sorted(overrides.items()))
            if topic is not ...:
                current["topic"] = topic or None
            if connection is not ...:
                current["connection"] = connection or None
            if launch:
                merged = {**current["launch"], **launch}
                if merged.get("max_cost_usd") in ("", None):
                    merged["max_cost_usd"] = None
                else:
                    merged["max_cost_usd"] = float(merged["max_cost_usd"])
                    if merged["max_cost_usd"] <= 0:
                        raise ValueError("launch.max_cost_usd must be positive")
                current["launch"] = merged
            current["revision"] = int(current["revision"]) + 1
            current["updated_at"] = time.time()
            current["updated_by"] = by
            durable_write_text(self.path, json.dumps(current, indent=2, sort_keys=True) + "\n")
            return current

    def reset(self, *, by: str) -> dict[str, Any]:
        with self._lock:
            revision = int(self.get()["revision"]) + 1
            blank = self._blank() | {
                "revision": revision,
                "updated_at": time.time(),
                "updated_by": by,
            }
            durable_write_text(self.path, json.dumps(blank, indent=2, sort_keys=True) + "\n")
            return blank

    def argv_overrides(self, workspace: dict[str, Any] | None = None) -> list[str]:
        draft = workspace or self.get()
        return [f"{key}={value}" for key, value in draft["overrides"].items()]


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------


class Secrets:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def has(self, env_name: str) -> bool:
        return bool(self._read().get(env_name))

    def set(self, env_name: str, value: str | None) -> None:
        if not ENV_NAME.match(env_name):
            raise ValueError(f"{env_name!r} is not an environment variable name")
        with self._lock:
            data = self._read()
            if value:
                data[env_name] = value
            else:
                data.pop(env_name, None)
            _write_private(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def environment(self) -> dict[str, str]:
        """Every stored token, by the variable a run reads it from."""

        return self._read()


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------

CONNECTION_FIELDS = (
    "transport",
    "url",
    "command",
    "knowledge_base",
    "source_name",
    "token_env",
    "mock_claim_seconds",
)


class Connections:
    def __init__(self, project_root: Path, secrets: Secrets) -> None:
        self.project_root = project_root
        self.secrets = secrets
        self.shipped = project_root / CONNECTIONS_FILE
        self.local, _ = local_file(project_root, "connections.local.yaml")
        self._lock = threading.Lock()

    def _load(self, path: Path) -> dict[str, dict[str, Any]]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        rows = data.get("connections") if isinstance(data, dict) else None
        return {str(k): dict(v or {}) for k, v in (rows or {}).items()}

    def list(self) -> list[dict[str, Any]]:
        shipped = self._load(self.shipped)
        local = self._load(self.local)
        rows = []
        for name in [*shipped, *(n for n in local if n not in shipped)]:
            row = {**shipped.get(name, {}), **local.get(name, {})}
            rows.append(self._public(name, row, shipped=name in shipped, edited=name in local))
        return rows

    def get(self, name: str) -> dict[str, Any]:
        for row in self.list():
            if row["name"] == name:
                return row
        raise KeyError(name)

    def _public(
        self, name: str, row: dict[str, Any], *, shipped: bool, edited: bool
    ) -> dict[str, Any]:
        token_env = str(row.get("token_env") or "")
        return {
            "name": name,
            "description": str(row.get("description") or ""),
            **{key: row.get(key) for key in CONNECTION_FIELDS},
            "shipped": shipped,
            "edited": edited,
            "token_stored": bool(token_env) and self.secrets.has(token_env),
            "token_in_environment": bool(token_env) and bool(os.environ.get(token_env)),
        }

    def save(self, payload: dict[str, Any], *, token: str | None = None) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not NAME.match(name):
            raise ValueError(
                f"connection name {name!r} must be lowercase letters, digits and hyphens (1-63)"
            )
        transport = str(payload.get("transport") or "streamable_http")
        if transport not in ("streamable_http", "stdio", "mock"):
            raise ValueError(f"transport {transport!r} must be streamable_http, stdio or mock")
        url = str(payload.get("url") or "").strip()
        if transport == "streamable_http" and not re.match(r"^https?://", url):
            raise ValueError(
                "a streamable_http connection needs an http(s) url, e.g. http://pheasant:8765/mcp"
            )
        if transport == "stdio" and not str(payload.get("command") or "").strip():
            raise ValueError("a stdio connection needs the command that starts the server")
        token_env = str(payload.get("token_env") or "").strip()
        if token and not token_env:
            # A stored token gets a variable of its own, so two connections
            # with different tokens can never read each other's.
            token_env = "PHEASANT_TOKEN_" + name.upper().replace("-", "_")
        if token_env and not ENV_NAME.match(token_env):
            raise ValueError(f"token_env {token_env!r} is not an environment variable name")
        row: dict[str, Any] = {
            "description": str(payload.get("description") or "").strip(),
            "transport": transport,
            "url": url,
            "command": str(payload.get("command") or "").strip(),
            "knowledge_base": str(payload.get("knowledge_base") or "").strip(),
            "source_name": str(payload.get("source_name") or "").strip(),
            "token_env": token_env or "PHEASANT_API_TOKEN",
        }
        if payload.get("mock_claim_seconds") not in (None, ""):
            row["mock_claim_seconds"] = float(payload["mock_claim_seconds"])
        if transport == "streamable_http" and not row["knowledge_base"]:
            raise ValueError(
                "name the knowledge base the lab writes to (pheasant's `pheasant.name`)"
            )
        with self._lock:
            local = self._load(self.local)
            local[name] = {k: v for k, v in row.items() if v not in ("", None)}
            self._write(local)
        if token is not None:
            self.secrets.set(row["token_env"], token or None)
        return self.get(name)

    def delete(self, name: str) -> dict[str, Any]:
        with self._lock:
            local = self._load(self.local)
            if name not in local:
                if name in self._load(self.shipped):
                    raise ValueError(f"{name} ships with the lab; edit it instead of deleting it")
                raise KeyError(name)
            row = local.pop(name)
            self._write(local)
        if row.get("token_env") and str(row["token_env"]).startswith("PHEASANT_TOKEN_"):
            self.secrets.set(str(row["token_env"]), None)
        return {"deleted": name}

    def overrides(self, name: str) -> dict[str, Any]:
        """The ``--set`` overrides that point a run at this connection."""

        row = self.get(name)
        values: dict[str, Any] = {"transport": row["transport"]}
        for key in ("url", "command", "knowledge_base", "source_name", "token_env"):
            if row.get(key):
                values[key] = row[key]
        values["mock_claim_seconds"] = (
            row.get("mock_claim_seconds") if row["transport"] == "mock" else None
        )
        return values

    def _write(self, rows: dict[str, dict[str, Any]]) -> None:
        header = (
            "# Pheasant connections added from the pheasant-lab console or its MCP tools.\n"
            "# Tokens are never written here; see .console/secrets.json (0600).\n"
        )
        text = header + yaml.safe_dump({"connections": rows}, sort_keys=False)
        self.local.parent.mkdir(parents=True, exist_ok=True)
        durable_write_text(self.local, text)


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------


class Prices:
    """The price list a run is held to, editable without editing YAML."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.path, self.override_value = local_file(project_root, "pricing.local.yaml")
        self._lock = threading.Lock()

    def rows(self, current_source: Path) -> dict[str, Any]:
        source = self.path if self.path.is_file() else current_source
        data = _read_yaml(source)
        return {
            "source": str(source),
            "local_file": self.override_value,
            "override": f"pricing.source={self.override_value}",
            "unit": data.get("unit", "per_million_tokens"),
            "currency": data.get("currency", "USD"),
            "version": str(data.get("version", "")),
            "models": {
                str(k): {"input": float(v.get("input", 0)), "output": float(v.get("output", 0))}
                for k, v in (data.get("models") or {}).items()
                if isinstance(v, dict)
            },
        }

    def set(
        self, current_source: Path, model: str, *, input_usd: float | None, output_usd: float | None
    ) -> dict[str, Any]:
        model = model.strip()
        if not model:
            raise ValueError("name the model to price")
        with self._lock:
            base = _read_yaml(self.path if self.path.is_file() else current_source)
            models = dict(base.get("models") or {})
            if input_usd is None and output_usd is None:
                if model not in models:
                    raise KeyError(model)
                models.pop(model)
            else:
                for label, value in (("input", input_usd), ("output", output_usd)):
                    if value is None or float(value) < 0:
                        raise ValueError(f"{label} price for {model} must be zero or more")
                models[model] = {"input": float(input_usd), "output": float(output_usd)}  # type: ignore[arg-type]
            document = {
                "version": time.strftime("%Y-%m-%d"),
                "currency": base.get("currency", "USD"),
                "unit": base.get("unit", "per_million_tokens"),
                "models": models,
            }
            header = (
                "# Model prices set from the pheasant-lab console or its MCP tools, on top of\n"
                "# the price list that was in force. A run uses it through\n"
                f"#   --set pricing.source={self.override_value}\n"
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            durable_write_text(self.path, header + yaml.safe_dump(document, sort_keys=False))
        return self.rows(current_source)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# run labels
# ---------------------------------------------------------------------------


class RunLabels:
    """A person's name and notes for a run. Never written into the run itself.

    A run directory is append-only evidence (rule 5); what somebody calls it
    afterwards is bookkeeping about it, kept beside the retention policy.
    """

    def __init__(self, home: Path) -> None:
        self.path = home / "run-labels.json"
        self._lock = threading.Lock()

    def all(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, run_id: str) -> dict[str, Any]:
        return self.all().get(run_id) or {}

    def set(self, run_id: str, *, label: Any = ..., notes: Any = ...) -> dict[str, Any]:
        with self._lock:
            rows = self.all()
            row = dict(rows.get(run_id) or {})
            if label is not ...:
                text = str(label or "").strip()
                if len(text) > 120:
                    raise ValueError("a run label is at most 120 characters")
                row["label"] = text or None
            if notes is not ...:
                row["notes"] = str(notes or "").strip() or None
            row["updated_at"] = time.time()
            if not row.get("label") and not row.get("notes"):
                rows.pop(run_id, None)
            else:
                rows[run_id] = row
            durable_write_text(self.path, json.dumps(rows, indent=2, sort_keys=True) + "\n")
            return rows.get(run_id) or {}

    def forget(self, run_id: str) -> None:
        with self._lock:
            rows = self.all()
            if rows.pop(run_id, None) is not None:
                durable_write_text(self.path, json.dumps(rows, indent=2, sort_keys=True) + "\n")
