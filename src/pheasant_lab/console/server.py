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

from ..settings import ConfigError, load_config
from .launcher import Launcher
from .projection import RunWatcher, fold_until, list_runs
from .region import probe_region
from .topics import add_topic, topic_rows
from .traces import RunTraces

#: How often a stream re-reads a run's files. Half a second keeps the feed
#: live without re-reading anything: a watcher only reads appended bytes.
STREAM_INTERVAL = 0.5
#: Events sent in one ``events`` message; a backfill arrives in pages.
STREAM_PAGE = 400


class Console:
    """Shared state for one server: where runs live, and what was launched."""

    def __init__(
        self,
        *,
        project_root: Path,
        output_root: Path,
        default_config: str,
        ui_dist: Path | None,
    ) -> None:
        self.project_root = project_root
        self.output_root = output_root
        self.default_config = default_config
        self.ui_dist = ui_dist
        self.launcher = Launcher(project_root, output_root)
        self._watchers: dict[str, RunWatcher] = {}
        self._lock = threading.Lock()

    def watcher(self, run_id: str) -> RunWatcher:
        root = (self.output_root / run_id).resolve()
        if root.parent != self.output_root.resolve() or not (root / "run-manifest.json").is_file():
            raise KeyError(run_id)
        with self._lock:
            watcher = self._watchers.get(run_id)
            if watcher is None:
                watcher = self._watchers[run_id] = RunWatcher(root)
            watcher.refresh()
            return watcher

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
                )
            },
            "pheasant": {
                "transport": resolved.pheasant.transport,
                "url": resolved.pheasant.url,
                "knowledge_base": resolved.pheasant.knowledge_base,
                "source_name": resolved.pheasant.source_name,
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
        def do_GET(self) -> None:
            url = urlparse(self.path)
            multi = parse_qs(url.query)
            query = {k: v[-1] for k, v in multi.items()}
            parts = [p for p in url.path.split("/") if p]
            try:
                if parts[:1] != ["api"]:
                    return self._static(url.path)
                route = parts[1:]
                if route == ["runs"]:
                    live = console.launcher.live_runs()
                    rows = list_runs(console.output_root)
                    for row in rows:
                        row["live"] = row["run_id"] in live
                        row["resumable"] = not row["live"] and not row["complete"]
                    return self._json(rows)
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
                if route == ["region"]:
                    config = query.get("config") or console.default_config
                    return self._json(probe_region(console.resolve(config, [])["pheasant"]))
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
            try:
                body = self._body()
                config = str(body.get("config") or console.default_config)
                overrides = [str(item) for item in body.get("set") or []]
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
                    argv = ["draft-topic", "--config", config, "--json"]
                    for item in overrides:
                        argv += ["--set", item]
                    argv += ["--intent", str(body.get("intent") or "")]
                    seeds = [str(t) for t in body.get("seed_terms") or [] if str(t).strip()]
                    if seeds:
                        argv += ["--seed-terms", ",".join(seeds)]
                    code, output = console.launcher.run_command(argv)
                    if code != 0:
                        message = output.strip().splitlines()[-1] if output.strip() else "failed"
                        return self._error(
                            HTTPStatus.BAD_REQUEST, message.removeprefix("refused: ")
                        )
                    return self._json(json.loads(output[output.index("{") :]))
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
                    )
                    return self._json(launch.as_dict(), status=HTTPStatus.ACCEPTED)
                if len(parts) == 3 and parts[0] == "launches" and parts[2] == "stop":
                    return self._json(console.launcher.stop(parts[1]).as_dict())
                return self._error(HTTPStatus.NOT_FOUND, f"no route {url.path}")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, f"unknown: {exc}")
            except ValidationError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, _validation_text(exc))
            except (ValueError, ConfigError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        # -- routes --------------------------------------------------------
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

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, default=str).encode()
            return self._raw(status, body, "application/json")

        def _error(self, status: HTTPStatus, message: str) -> None:
            return self._json({"detail": message}, status=status)

        def _raw(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
) -> ThreadingHTTPServer:
    console = Console(
        project_root=project_root,
        output_root=output_root,
        default_config=default_config,
        ui_dist=ui_dist if ui_dist is not None else default_ui_dist(project_root),
    )
    server = ThreadingHTTPServer((host, port), make_handler(console))
    server.daemon_threads = True
    server.console = console  # type: ignore[attr-defined]
    return server
