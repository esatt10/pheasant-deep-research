"""``pheasant-lab serve``: the console's HTTP surface, standard library only.

Laptop-local like the rest of the lab: no web framework, no broker, no
database. ``ThreadingHTTPServer`` serves the built UI, a JSON API over the run
directories, and one server-sent-event stream per watched run. Binds loopback
by default, because it can start paid runs.

The stream sends two kinds of message: ``events`` (what was appended to
``raw/events.jsonl`` since the last tick - the feed) and ``model`` (the folded
view from :mod:`.projection`). The model is computed here, once, in Python,
rather than re-derived in the browser: one fold, tested with the rest of the
suite, instead of a second implementation in TypeScript that would drift from
it.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from ..catalog import advise, catalog
from ..settings import ConfigError, load_config, load_dotenv
from .launcher import Launch, Launcher
from .logs import LogControl
from .mcp import handle_jsonrpc, mcp_info
from .projection import RunWatcher, fold_until, in_scope
from .state import Connections, Prices, RunLabels, Secrets, Workspace
from .topics import add_topic, topic_rows
from .traces import RunTraces

#: How often a stream re-reads a run's files. Half a second keeps the feed
#: live without re-reading anything: a watcher only reads appended bytes.
STREAM_INTERVAL = 0.5
#: Events sent in one ``events`` message; a backfill arrives in pages.
STREAM_PAGE = 400
#: Where the console's own access key is read from, by default.
TOKEN_ENV = "PHEASANT_LAB_CONSOLE_TOKEN"
#: Answered without a key: the shell that asks for one, and whether one is needed.
PUBLIC_API = frozenset({"/api/auth"})
#: Where the MCP endpoint answers. ``/mcp`` is what MCP clients are given.
MCP_PATHS = frozenset({"/mcp", "/api/mcp"})


class Unauthenticated(RuntimeError):
    """A console bound beyond loopback with no key: it can start paid runs."""


def is_loopback(host: str) -> bool:
    """Only an address no other machine can reach. ``""`` binds every interface."""

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Console:
    """Shared state for one server: where runs live, and what was launched."""

    def __init__(
        self,
        *,
        project_root: Path,
        output_root: Path,
        default_config: str,
        ui_dist: Path | None,
        run_scope: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.output_root = output_root
        self.default_config = default_config
        self.ui_dist = ui_dist
        #: Only runs whose manifest says they were made in this deployment
        #: (``docker`` in the lab image) are listed, read or deleted.
        self.run_scope = run_scope or None
        home = output_root / ".console"
        self.secrets = Secrets(home / "secrets.json")
        self.connections = Connections(project_root, self.secrets)
        self.prices = Prices(project_root)
        self.labels = RunLabels(home)
        self.workspace = Workspace(home, default_config)
        #: The cap on one topic-draft call; ``draft-topic`` reserves it first.
        self.draft_budget_usd = 0.25
        self.launcher = Launcher(
            project_root, output_root, extra_env=self.secrets.environment, on_run=self._labelled
        )
        self.logs = LogControl(output_root, self.launcher, scope=self.run_scope)
        self._watchers: dict[str, RunWatcher] = {}
        self._lock = threading.Lock()
        #: The console's own access key, or ``None`` for an open (loopback) console.
        self.token: bytes | None = None
        self.env_file: str | None = ".env"
        from .operations import Operations

        self.ops = Operations(self)

    def _labelled(self, run_id: str, launch: Launch) -> None:
        if launch.label:
            self.labels.set(run_id, label=launch.label)

    def environment(self) -> dict[str, str]:
        """The process environment over the project's ``.env``.

        The same precedence ``load_config`` uses, so a secret the README says
        to put in ``.env`` reaches the console's own probes too - not only the
        runs it launches, which read ``.env`` themselves.
        """

        values = load_dotenv(self.project_root / self.env_file) if self.env_file else {}
        # A token stored for a connection is what a launched run is handed,
        # so the console's own probes read it too.
        return {**values, **os.environ, **self.secrets.environment()}

    def authorized(self, header: str | None) -> bool:
        if self.token is None:
            return True
        scheme, _, supplied = (header or "").partition(" ")
        # Bytes, as pheasant-kb compares them: a header byte above 127 makes
        # ``compare_digest`` raise on str operands - a 500 from the guard.
        # ``http.server`` decodes headers as latin-1, so encoding back the same
        # way recovers exactly the bytes the client sent.
        raw = supplied.strip().encode("latin-1", "replace")
        return scheme.lower() == "bearer" and hmac.compare_digest(raw, self.token)

    def watcher(self, run_id: str) -> RunWatcher:
        root = (self.output_root / run_id).resolve()
        if root.parent != self.output_root.resolve() or not (root / "run-manifest.json").is_file():
            raise KeyError(run_id)
        if not in_scope(root, self.run_scope):
            raise KeyError(run_id)
        with self._lock:
            watcher = self._watchers.get(run_id)
            if watcher is None:
                watcher = self._watchers[run_id] = RunWatcher(root)
            watcher.refresh()
            return watcher

    def forget_run(self, run_id: str) -> None:
        with self._lock:
            self._watchers.pop(run_id, None)

    def configs(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted((self.project_root / "configs").glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            if "\nexperiment:" not in "\n" + text:
                continue
            rows.append(
                {
                    "path": str(path.relative_to(self.project_root)),
                    "name": path.stem,
                    "default": str(path.relative_to(self.project_root)) == self.default_config,
                }
            )
        return rows

    def traces(self, run_id: str) -> RunTraces:
        return RunTraces(self.watcher(run_id).root)

    def topics(self, config: str, overrides: list[str]) -> dict[str, Any]:
        return topic_rows(self.project_root, self.launcher.config_path(config), _pairs(overrides))

    def resolve(self, config: str, overrides: list[str]) -> dict[str, Any]:
        path = self.launcher.config_path(config)
        pairs = _pairs(overrides)
        resolved = load_config(
            path, overrides=pairs, project_root=self.project_root, env_file=".env"
        )
        from ..redaction import Redactor

        redactor = Redactor(enabled=True)
        redactor.register_environment()
        payload = resolved.redacted(redactor)
        return {
            "config": str(path.relative_to(self.project_root)),
            "digest": resolved.digest(redactor),
            "resolved": {
                key: payload.get(key)
                for key in (
                    "experiment",
                    "collection",
                    "stopping",
                    "benchmark",
                    "arms",
                    "replay",
                    "budget",
                    "logging",
                )
            },
            "pheasant": {
                "transport": resolved.pheasant.transport,
                "url": resolved.pheasant.url,
                "knowledge_base": resolved.pheasant.knowledge_base,
                "source_name": resolved.pheasant.source_name,
                "token_env": resolved.pheasant.token_env,
                "mock_claim_seconds": resolved.pheasant.mock_claim_seconds,
                "capabilities": {
                    name: {"tool": spec.tool, "required": spec.required}
                    for name, spec in resolved.pheasant.capabilities.items()
                },
            },
            "topics": [
                {"id": topic.id, "title": topic.title, "facets": len(topic.facets)}
                for topic in resolved.topics
            ],
            "models": {role: spec.model for role, spec in resolved.models.items()},
            "unresolved_env": resolved.unresolved_env,
            "source_files": resolved.source_files,
            # Every catalog field's resolved value, by its --set key, so a form
            # reads "what is in force" without walking the tree itself.
            "values": {row["key"]: row["current"] for row in catalog(resolved)["fields"]},
            "advice": advise(resolved),
        }


def _asdict(policy: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(policy)


def _read_options(query: dict[str, str]) -> dict[str, Any]:
    return {
        "offset": int(query.get("offset", 0) or 0),
        "limit": int(query.get("limit", 500) or 500),
        "query": query.get("q") or None,
        "level": query.get("level") or None,
        "tail": query.get("tail") in {"1", "true"},
    }


def _pairs(overrides: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in overrides if "=" in item)


def _validation_text(error: ValidationError) -> str:
    """A pydantic refusal as one line a form can show beside its fields."""

    parts = []
    for item in error.errors():
        where = ".".join(str(p) for p in item.get("loc", ()))
        message = str(item.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)


def make_handler(console: Console) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pheasant-lab-console"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            if os.environ.get("PHEASANT_LAB_CONSOLE_LOG"):
                super().log_message(format, *args)

        # -- dispatch ------------------------------------------------------
        def _guard(self, path: str) -> bool:
            """Refuse an unauthenticated ``/api`` call when the console has a key.

            The built shell and its assets stay public - the bundle cannot
            carry the key, so the page that asks for it has to load without
            it - and ``/api/auth`` says whether a key is needed. Everything
            else under ``/api`` (runs, traces, launches, logs) needs it.
            """

            if not path.startswith("/api/") or path in PUBLIC_API:
                return True
            if console.authorized(self.headers.get("Authorization")):
                return True
            self._json(
                {"detail": "this console requires an access key"},
                status=HTTPStatus.UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Bearer realm="pheasant-lab console"'},
            )
            return False

        def do_GET(self) -> None:
            url = urlparse(self.path)
            multi = parse_qs(url.query)
            query = {k: v[-1] for k, v in multi.items()}
            parts = [p for p in url.path.split("/") if p]
            if url.path.rstrip("/") in MCP_PATHS:
                # No server-initiated stream is offered (streamable HTTP lets
                # a server decline one with 405); every answer is a POST reply.
                return self._json(
                    {"detail": "POST JSON-RPC to this endpoint; no GET stream is offered"},
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                    headers={"Allow": "POST"},
                )
            if not self._guard(url.path):
                return None
            try:
                if parts[:1] != ["api"]:
                    return self._static(url.path)
                route = parts[1:]
                if route == ["auth"]:
                    return self._json(
                        {
                            "required": console.token is not None,
                            "authenticated": console.authorized(self.headers.get("Authorization")),
                        }
                    )
                if route == ["runs"]:
                    return self._json(console.ops.list_runs())
                if len(route) == 3 and route[0] == "runs" and route[2] == "detail":
                    return self._json(console.ops.get_run(route[1]))
                if route == ["settings"]:
                    return self._json(console.ops.get_settings())
                if route == ["settings", "catalog"]:
                    return self._json(
                        console.ops.describe_settings(
                            section=query.get("section") or None,
                            key=query.get("key") or None,
                            include_advanced=query.get("advanced", "1") not in {"0", "false"},
                        )
                    )
                if route == ["settings", "command"]:
                    return self._json(console.ops.command_line())
                if route == ["budget"]:
                    return self._json(console.ops.get_budget())
                if route == ["prices"]:
                    return self._json(console.ops.prices())
                if route == ["connections"]:
                    return self._json(console.ops.list_connections())
                if len(route) == 3 and route[0] == "connections" and route[2] == "probe":
                    return self._json(console.ops.probe_connection(unquote(route[1])))
                if route == ["plan"]:
                    return self._json(console.ops.plan())
                if route == ["mcp", "info"]:
                    return self._json(mcp_info(console))
                if len(route) == 2 and route[0] == "runs":
                    watcher = console.watcher(route[1])
                    if query.get("until"):
                        # A replay position: the view then, not now.
                        model = fold_until(watcher.root, int(query["until"]))
                        view = model.snapshot()
                        view["run"]["replay_of"] = watcher.model.sequence
                        return self._json(view)
                    return self._json(watcher.model.snapshot(now=time.time()))
                if len(route) == 3 and route[0] == "runs" and route[2] == "events":
                    return self._events(route[1], query)
                if len(route) == 3 and route[0] == "runs" and route[2] == "stream":
                    return self._stream(route[1], int(query.get("after", 0) or 0))
                if len(route) == 3 and route[0] == "runs" and route[2] == "reports":
                    return self._reports(route[1])
                if len(route) == 4 and route[0] == "runs" and route[2] == "reports":
                    return self._report(route[1], route[3])
                if len(route) == 3 and route[0] == "runs" and route[2] == "traces":
                    return self._json({"actors": console.traces(route[1]).actors()})
                if len(route) == 4 and route[0] == "runs" and route[2] == "traces":
                    return self._json(console.traces(route[1]).trace(unquote(route[3])))
                if len(route) == 4 and route[0] == "runs" and route[2] == "mcp":
                    return self._json(console.traces(route[1]).mcp_call(int(route[3])))
                if route == ["topics"]:
                    config = query.get("config") or console.default_config
                    return self._json(console.topics(config, multi.get("set", [])))
                if route == ["configs"]:
                    return self._json(console.configs())
                if route == ["launches"]:
                    return self._json(console.launcher.list())
                if len(route) == 2 and route[0] == "launches":
                    return self._json(console.launcher.get(route[1]).as_dict())
                if route == ["logs"]:
                    return self._json(console.logs.inventory())
                if route == ["logs", "retention"]:
                    logs = console.logs
                    return self._json({"policy": _asdict(logs.policy()), "plan": logs.plan()})
                if len(route) == 3 and route[:2] == ["logs", "launches"]:
                    return self._json(console.logs.read_launch(route[2], **_read_options(query)))
                if len(route) == 3 and route[:2] == ["logs", "runs"]:
                    return self._json(
                        console.logs.read_run_file(
                            route[2], query.get("path", ""), **_read_options(query)
                        )
                    )
                if route == ["region"]:
                    return self._json(console.ops.probe_connection(config=query.get("config")))
                return self._error(HTTPStatus.NOT_FOUND, f"no route {url.path}")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, f"unknown: {exc}")
            except ValidationError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, _validation_text(exc))
            except (ValueError, ConfigError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p][1:]
            if url.path.rstrip("/") in MCP_PATHS:
                return self._mcp_post()
            if not self._guard(url.path):
                return None
            try:
                body = self._body()
                config = str(body.get("config") or console.default_config)
                overrides = [str(item) for item in body.get("set") or []]
                who = "ui" if self.headers.get("X-Pheasant-Lab-Client") == "ui" else "api"
                if parts == ["settings", "reset"]:
                    return self._json(console.ops.reset_settings(by=who))
                if parts == ["models", "recommended"]:
                    return self._json(
                        console.ops.apply_recommended_models(by=who, roles=body.get("roles"))
                    )
                if parts == ["connections"]:
                    connection = body.get("connection")
                    if not isinstance(connection, dict):
                        raise ValueError("body.connection must be an object")
                    token = body.get("token")
                    saved = console.ops.save_connection(
                        connection, token=None if token is None else str(token)
                    )
                    return self._json(saved, status=HTTPStatus.CREATED)
                if parts == ["connections", "select"]:
                    return self._json(console.ops.select_connection(body.get("name"), by=who))
                if parts == ["topics", "select"]:
                    return self._json(console.ops.select_topic(body.get("topic"), by=who))
                if parts == ["runs", "launch"]:
                    launched = console.ops.launch(
                        kind=str(body.get("kind") or "pipeline"),
                        label=body.get("label"),
                        run_id=body.get("run_id"),
                        max_cost_usd=body.get("max_cost_usd"),
                    )
                    return self._json(launched, status=HTTPStatus.ACCEPTED)
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "reports":
                    return self._json(
                        console.ops.generate_reports(parts[1]), status=HTTPStatus.ACCEPTED
                    )
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "resume":
                    launched = console.ops.launch(kind="resume", run_id=parts[1])
                    return self._json(launched, status=HTTPStatus.ACCEPTED)
                if parts == ["config", "resolve"]:
                    return self._json(console.resolve(config, overrides))
                if parts in (["plan"], ["doctor"]):
                    argv = [parts[0], "--config", config]
                    for item in overrides:
                        argv += ["--set", item]
                    if parts == ["plan"]:
                        argv.append("--json")
                        if body.get("topic"):
                            argv += ["--topic", str(body["topic"])]
                    elif body.get("mock"):
                        argv.append("--mock")
                    code, output = console.launcher.run_command(argv)
                    result: dict[str, Any] = {"exit_code": code, "output": output}
                    if parts == ["plan"]:
                        try:
                            result["projection"] = json.loads(output[output.index("{") :])
                        except ValueError:
                            result["projection"] = None
                    return self._json(result)
                if parts == ["topics", "draft"]:
                    # The console is a launcher: drafting is the CLI's
                    # `draft-topic`, so a draft from the browser and one from a
                    # terminal are the same call under the same budget guard.
                    context = body.get("context")
                    return self._json(
                        console.ops.draft_topic(
                            intent=str(body.get("intent") or ""),
                            seed_terms=[str(t) for t in body.get("seed_terms") or []],
                            model=body.get("model") or None,
                            provider=body.get("provider") or None,
                            reasoning_effort=body.get("reasoning_effort") or None,
                            context=context if isinstance(context, dict) else None,
                            config=config,
                            overrides=overrides,
                        )
                    )
                if parts == ["topics"]:
                    topic = body.get("topic")
                    if not isinstance(topic, dict):
                        raise ValueError("body.topic must be an object")
                    result = add_topic(
                        console.project_root,
                        console.launcher.config_path(config),
                        _pairs(overrides),
                        topic,
                        replace=bool(body.get("replace")),
                    )
                    return self._json(result, status=HTTPStatus.CREATED)
                if parts == ["launches"]:
                    launch = console.launcher.launch(
                        kind=str(body.get("kind") or "demo"),
                        config=config,
                        overrides=overrides,
                        topic=body.get("topic"),
                        arms=body.get("arms"),
                        run_id=body.get("run_id"),
                        max_cost_usd=body.get("max_cost_usd"),
                        label=body.get("label"),
                    )
                    return self._json(launch.as_dict(), status=HTTPStatus.ACCEPTED)
                if len(parts) == 3 and parts[0] == "launches" and parts[2] == "stop":
                    return self._json(console.launcher.stop(parts[1]).as_dict())
                if parts == ["logs", "retention", "preview"]:
                    from .logs import RetentionPolicy

                    merged = {**_asdict(console.logs.policy()), **(body.get("policy") or {})}
                    candidate = RetentionPolicy.from_payload(merged)
                    return self._json({"plan": console.logs.plan(candidate)})
                if parts == ["logs", "retention", "apply"]:
                    done = console.logs.apply(reason="retention policy, applied by hand")
                    for row in done:
                        if row.get("kind") == "run" and not row.get("skipped"):
                            console.forget_run(str(row.get("target")))
                    return self._json({"applied": done})
                if len(parts) == 4 and parts[:2] == ["logs", "runs"] and parts[3] == "keep":
                    policy = console.logs.keep(parts[2], bool(body.get("keep", True)))
                    return self._json({"policy": _asdict(policy)})
                return self._error(HTTPStatus.NOT_FOUND, f"no route {url.path}")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, f"unknown: {exc}")
            except ValidationError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, _validation_text(exc))
            except (ValueError, ConfigError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_PUT(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p][1:]
            if not self._guard(url.path):
                return None
            try:
                body = self._body()
                who = "ui" if self.headers.get("X-Pheasant-Lab-Client") == "ui" else "api"
                if parts == ["logs", "retention"]:
                    policy = console.logs.set_policy(dict(body.get("policy") or {}))
                    return self._json({"policy": _asdict(policy), "plan": console.logs.plan()})
                if parts == ["settings"]:
                    keep = ...
                    return self._json(
                        console.ops.update_settings(
                            by=who,
                            set_values=body.get("set")
                            if isinstance(body.get("set"), dict)
                            else None,
                            unset=list(body.get("unset") or []),
                            replace_overrides=body.get("overrides")
                            if isinstance(body.get("overrides"), dict)
                            else None,
                            config=body.get("config"),
                            topic=body.get("topic", keep),
                            connection=body.get("connection", keep),
                            launch=body.get("launch")
                            if isinstance(body.get("launch"), dict)
                            else None,
                            strict=bool(body.get("strict")),
                            expected_revision=body.get("revision"),
                        )
                    )
                if parts == ["budget"]:
                    return self._json(
                        console.ops.set_budget(
                            by=who,
                            cost_budget_usd=body.get("cost_budget_usd"),
                            runtime_budget_minutes=body.get("runtime_budget_minutes"),
                            allocation=body.get("allocation"),
                            launch_max_cost_usd=body.get("launch_max_cost_usd", ...),
                            evaluation_budget_reserve_fraction=body.get(
                                "evaluation_budget_reserve_fraction"
                            ),
                        )
                    )
                if len(parts) == 2 and parts[0] == "models":
                    return self._json(
                        console.ops.set_role_model(
                            by=who,
                            role=parts[1],
                            provider=body.get("provider"),
                            model=body.get("model"),
                            reasoning_effort=body.get("reasoning_effort", ...),
                            max_output_tokens=body.get("max_output_tokens"),
                        )
                    )
                if len(parts) == 2 and parts[0] == "prices":
                    return self._json(
                        console.ops.set_price(
                            by=who,
                            model=unquote(parts[1]),
                            input_usd=float(body["input"]),
                            output_usd=float(body["output"]),
                        )
                    )
                if len(parts) == 2 and parts[0] == "runs":
                    return self._json(
                        console.ops.update_run(
                            parts[1],
                            label=body.get("label", ...),
                            notes=body.get("notes", ...),
                            keep=body.get("keep"),
                        )
                    )
                return self._error(HTTPStatus.NOT_FOUND, f"no route {url.path}")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, f"unknown: {exc}")
            except ValidationError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, _validation_text(exc))
            except (TypeError, ValueError, ConfigError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_DELETE(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p][1:]
            if not self._guard(url.path):
                return None
            try:
                if len(parts) == 3 and parts[:2] == ["logs", "launches"]:
                    return self._json(console.logs.delete_launch(parts[2]))
                if len(parts) == 4 and parts[:2] == ["logs", "runs"] and parts[3] == "projection":
                    return self._json(console.logs.drop_projection(parts[2]))
                if len(parts) == 2 and parts[0] == "runs":
                    result = console.logs.delete_run(parts[1])
                    console.forget_run(parts[1])
                    console.labels.forget(parts[1])
                    return self._json(result)
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "reports":
                    return self._json(console.ops.delete_reports(parts[1]))
                who = "ui" if self.headers.get("X-Pheasant-Lab-Client") == "ui" else "api"
                if len(parts) == 2 and parts[0] == "connections":
                    return self._json(console.ops.delete_connection(unquote(parts[1]), by=who))
                if len(parts) == 2 and parts[0] == "topics":
                    return self._json(console.ops.delete_topic(unquote(parts[1]), by=who))
                if len(parts) == 2 and parts[0] == "prices":
                    return self._json(
                        console.ops.set_price(
                            by=who, model=unquote(parts[1]), input_usd=None, output_usd=None
                        )
                    )
                return self._error(HTTPStatus.NOT_FOUND, f"no route {url.path}")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, f"unknown: {exc}")
            except (ValueError, ConfigError) as exc:
                return self._error(HTTPStatus.CONFLICT, str(exc))

        # -- routes --------------------------------------------------------
        def _mcp_post(self) -> None:
            """Streamable HTTP MCP, answered as plain JSON (no server-initiated stream).

            Guarded by the console's key exactly like ``/api``: the tools can
            start paid runs and delete them.
            """

            if console.token is not None and not console.authorized(
                self.headers.get("Authorization")
            ):
                return self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32001, "message": "this console requires an access key"},
                    },
                    status=HTTPStatus.UNAUTHORIZED,
                    headers={"WWW-Authenticate": 'Bearer realm="pheasant-lab console"'},
                )
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )
            reply = handle_jsonrpc(console, body, client="mcp")
            if reply is None:
                # Notifications and responses: accepted, nothing to say.
                return self._raw(HTTPStatus.ACCEPTED, b"", "application/json")
            return self._json(reply)

        def _events(self, run_id: str, query: dict[str, str]) -> None:
            watcher = console.watcher(run_id)
            after = int(query.get("after", 0) or 0)
            limit = min(int(query.get("limit", 500) or 500), 5000)
            rows = [e for e in watcher.event_log if int(e.get("sequence") or 0) > after]
            return self._json({"events": rows[:limit], "sequence": watcher.model.sequence})

        def _stream(self, run_id: str, after: int) -> None:
            watcher = console.watcher(run_id)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            sent = after
            last_model = ""
            idle = 0.0
            try:
                while True:
                    with console._lock:
                        watcher.refresh()
                        backlog = [
                            e for e in watcher.event_log if int(e.get("sequence") or 0) > sent
                        ]
                        model = watcher.model.snapshot(now=time.time())
                    for start in range(0, len(backlog), STREAM_PAGE):
                        page = backlog[start : start + STREAM_PAGE]
                        self._send("events", page)
                        sent = int(page[-1].get("sequence") or sent)
                    text = json.dumps(model, sort_keys=True, default=str)
                    # The elapsed clock moves every tick; the rest only when
                    # something happened. Send on change, and at least every
                    # few seconds so a waiting barrier's clock keeps moving.
                    signature = text.replace(str(model["run"]["elapsed_seconds"]), "")
                    if signature != last_model or idle >= 2.0:
                        self._write(f"event: model\ndata: {text}\n\n")
                        last_model = signature
                        idle = 0.0
                    else:
                        idle += STREAM_INTERVAL
                    time.sleep(STREAM_INTERVAL)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return

        def _reports(self, run_id: str) -> None:
            root = console.watcher(run_id).root / "reports"
            names = sorted(p.name for p in root.glob("*.md")) if root.is_dir() else []
            return self._json({"reports": names})

        def _report(self, run_id: str, name: str) -> None:
            root = console.watcher(run_id).root / "reports"
            path = (root / name).resolve()
            if path.parent != root.resolve() or path.suffix != ".md" or not path.is_file():
                raise KeyError(name)
            return self._json({"name": name, "markdown": path.read_text(encoding="utf-8")})

        def _static(self, path: str) -> None:
            dist = console.ui_dist
            if dist is None or not (dist / "index.html").is_file():
                body = (
                    b"<!doctype html><meta charset=utf-8><title>pheasant-lab console</title>"
                    b"<body style='font-family:system-ui;padding:40px'><h1>UI not built</h1>"
                    b"<p>Run <code>make ui</code> (or <code>cd ui &amp;&amp; npm ci &amp;&amp; "
                    b"npm run build</code>) and reload. The API is live at <code>/api</code>.</p>"
                )
                return self._raw(HTTPStatus.OK, body, "text/html; charset=utf-8")
            target = (dist / path.lstrip("/")).resolve()
            if dist.resolve() not in target.parents or not target.is_file():
                # A single-page app: every unknown path is the app, which then
                # routes it. (`/api` never reaches here.)
                target = dist / "index.html"
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self._raw(HTTPStatus.OK, target.read_bytes(), ctype)

        # -- plumbing ------------------------------------------------------
        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("request body must be a JSON object")
            return data

        def _json(
            self,
            payload: Any,
            status: HTTPStatus = HTTPStatus.OK,
            headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, default=str).encode()
            return self._raw(status, body, "application/json", headers=headers)

        def _error(self, status: HTTPStatus, message: str) -> None:
            return self._json({"detail": message}, status=status)

        def _raw(
            self,
            status: HTTPStatus,
            body: bytes,
            ctype: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The browser navigated away mid-response. Nothing to report.
                return

        def _send(self, name: str, payload: Any) -> None:
            self._write(f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n")

        def _write(self, text: str) -> None:
            self.wfile.write(text.encode())
            self.wfile.flush()

    return Handler


def default_ui_dist(project_root: Path) -> Path | None:
    configured = os.environ.get("PHEASANT_LAB_UI_DIST")
    if configured:
        return Path(configured)
    for candidate in (project_root / "ui" / "dist", Path(__file__).parent / "static"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def serve(
    *,
    host: str,
    port: int,
    project_root: Path,
    output_root: Path,
    default_config: str,
    ui_dist: Path | None = None,
    token: str | None = None,
    allow_unauthenticated: bool = False,
    env_file: str | None = ".env",
    run_scope: str | None = None,
) -> ThreadingHTTPServer:
    """Build the console server.

    A console reachable from other machines with no key is refused, the way
    pheasant-kb refuses a serving role bound beyond loopback without
    ``security.api_auth``: it can start paid runs and read every trace, and a
    bind address is not a control once it is ``0.0.0.0``.
    ``allow_unauthenticated`` is the explicit "an ingress authenticates this".
    """

    token = (token or "").strip() or None
    if token is None and not is_loopback(host) and not allow_unauthenticated:
        raise Unauthenticated(
            f"refusing to serve the console on {host!r} without an access key: set "
            f"{TOKEN_ENV} (or pass --allow-unauthenticated when an authenticating "
            "proxy stands in front of it)"
        )
    console = Console(
        project_root=project_root,
        output_root=output_root,
        default_config=default_config,
        ui_dist=ui_dist if ui_dist is not None else default_ui_dist(project_root),
        run_scope=run_scope,
    )
    console.token = token.encode("utf-8") if token else None
    console.env_file = env_file
    server = ThreadingHTTPServer((host, port), make_handler(console))
    server.daemon_threads = True
    server.console = console  # type: ignore[attr-defined]
    threading.Thread(
        target=_retention_beat, args=(console,), name="console-retention", daemon=True
    ).start()
    return server


#: How often a policy with ``auto_apply`` is applied while the console runs.
RETENTION_BEAT_SECONDS = 600.0


def _retention_beat(console: Console, *, interval: float = RETENTION_BEAT_SECONDS) -> None:
    """Apply the retention policy on a beat, when the policy asks for that.

    Reads the policy each time, so turning ``auto_apply`` off in the browser
    takes effect at the next beat without a restart. A failure is a skipped
    beat: housekeeping must never take the console down with it.
    """

    while True:
        time.sleep(interval)
        try:
            if not console.logs.policy().auto_apply:
                continue
            for row in console.logs.apply(reason="retention policy, automatic"):
                if row.get("kind") == "run" and not row.get("skipped"):
                    console.forget_run(str(row.get("target")))
        except Exception:  # see the docstring
            continue
