"""The console's MCP surface: every operation as a tool.

The lab is an MCP *client* of Pheasant; this makes it an MCP *server* too, so
an agent can configure a search, set its budget, choose the Pheasant
connection, launch a run and manage its runs, reports and logs with the same
operations the browser uses (:mod:`.operations`). Standard library only, like
the rest of the console: a JSON-RPC 2.0 dispatcher served over streamable
HTTP at ``/mcp`` (plain JSON replies, no server-initiated stream) and over
stdio by ``pheasant-lab mcp``.

Tool errors are results with ``isError: true`` and the refusal's own words,
which is what an agent can act on; protocol errors are JSON-RPC errors.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TextIO

from pydantic import ValidationError

from .. import __version__
from ..settings import ConfigError

if TYPE_CHECKING:
    from .server import Console

SUPPORTED_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "pheasant-lab"

INSTRUCTIONS = """\
pheasant-lab: configure, run and manage swarm-search experiments against a
Pheasant knowledge region.

Settings live in one shared draft (the same one the browser console edits).
Start with lab_describe_settings to see every field, what it means, its valid
values and the recommended model/reasoning level for each agent role. Change
fields with lab_update_settings (keys are `--set` paths, e.g.
`collection.max_research_agents`), the budget with lab_set_budget, the region
with lab_select_connection, and the topic with lab_select_topic or
lab_draft_topic + lab_save_topic. lab_plan projects worst-case cost and
lab_doctor checks everything before spend. lab_launch_run starts the run;
lab_list_runs / lab_get_run / lab_update_run / lab_delete_run manage runs,
lab_*_report(s) the reports and lab_*_log(s) the logs. Deleting a run needs
confirm=<run id>. A run's raw trace is never edited, only kept or deleted whole.
"""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict[str, Any]
    call: Callable[[Console, dict[str, Any], str], Any]
    required: tuple[str, ...] = ()
    read_only: bool = False
    destructive: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.read_only,
            },
        }


def _s(description: str, kind: str | list[str] = "string", **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **extra}


_RUN_ID = _s("A run id, e.g. run-0123456789abcdef (from lab_list_runs).")
_KEEP = ...


def _opt(args: dict[str, Any], key: str) -> Any:
    return args.get(key, _KEEP)


TOOLS: tuple[Tool, ...] = (
    # -- settings ----------------------------------------------------------
    Tool(
        "lab_describe_settings",
        "Explain every setting: what it means, valid values or range, its default and current "
        "value, and for agent roles the recommended model and reasoning level. Filter by section "
        "(experiment, budget, search, connection, collection, stopping, benchmark, arms, models, "
        "privacy, refinement, logging, metrics, proof, files) or ask for one key.",
        {
            "section": _s("Only this section."),
            "key": _s("Only this --set key, e.g. replay.max_results_per_search."),
            "include_advanced": _s("Include advanced fields (default true).", "boolean"),
        },
        lambda c, a, by: c.ops.describe_settings(
            section=a.get("section"),
            key=a.get("key"),
            include_advanced=a.get("include_advanced", True),
        ),
        read_only=True,
    ),
    Tool(
        "lab_get_settings",
        "The shared settings draft (config file, overrides, topic, connection, launch caps) and "
        "whether it resolves; `resolved.values` holds every field's value in force.",
        {},
        lambda c, a, by: c.ops.get_settings(),
        read_only=True,
    ),
    Tool(
        "lab_update_settings",
        "Change settings in the shared draft. `set` maps --set keys to values (null removes an "
        "override, returning the field to the file's value); `unset` lists keys to remove. "
        "Invalid combinations are kept and reported (valid: false) unless strict is true, because "
        "linked fields such as the five budget shares can only be edited one at a time.",
        {
            "set": _s('Map of --set key to value, e.g. {"collection.max_depth": 3}.', "object"),
            "unset": _s("Keys to return to the file's value.", "array", items={"type": "string"}),
            "config": _s("Switch the base config file (clears overrides), e.g. configs/demo.yaml."),
            "launch": _s("Launch options: {max_cost_usd, label}.", "object"),
            "strict": _s("Refuse a change that does not resolve.", "boolean"),
        },
        lambda c, a, by: c.ops.update_settings(
            by=by,
            set_values=a.get("set"),
            unset=a.get("unset"),
            config=a.get("config"),
            launch=a.get("launch"),
            strict=bool(a.get("strict")),
        ),
    ),
    Tool(
        "lab_reset_settings",
        "Discard every override in the shared draft and return to the default config file.",
        {},
        lambda c, a, by: c.ops.reset_settings(by=by),
        destructive=True,
    ),
    Tool(
        "lab_plan",
        "Project the draft's worst-case cost and volume without any model or ingest call.",
        {},
        lambda c, a, by: c.ops.plan(),
        read_only=True,
    ),
    Tool(
        "lab_doctor",
        "Preflight: models, prices, providers, the region's tools and every mapped argument. "
        "Refuses before any spend; run it before lab_launch_run.",
        {"mock": _s("Check against the in-process mock region instead.", "boolean")},
        lambda c, a, by: c.ops.doctor(mock=bool(a.get("mock"))),
        read_only=True,
    ),
    Tool(
        "lab_command_line",
        "The exact `pheasant-lab run` argv the draft would launch.",
        {},
        lambda c, a, by: c.ops.command_line(),
        read_only=True,
    ),
    # -- budget and models ---------------------------------------------------
    Tool(
        "lab_get_budget",
        "The run budget (USD and minutes), its allocation across phases, the evaluation reserve, "
        "the next launch's own cap and the topic-draft cap, each explained.",
        {},
        lambda c, a, by: c.ops.get_budget(),
        read_only=True,
    ),
    Tool(
        "lab_set_budget",
        "Set the budget. cost_budget_usd is the run's hard ceiling (every call reserves its worst "
        "case first); allocation shares (planning, collection, benchmark, evaluation, reserve) "
        "must sum to 1; launch_max_cost_usd caps only the next launch (null clears it).",
        {
            "cost_budget_usd": _s("Positive USD.", "number"),
            "runtime_budget_minutes": _s("Whole minutes.", "integer"),
            "allocation": _s("Shares by phase, summing to 1.", "object"),
            "launch_max_cost_usd": _s("Cap for the next launch, or null.", ["number", "null"]),
            "evaluation_budget_reserve_fraction": _s("Share collection may never touch.", "number"),
        },
        lambda c, a, by: c.ops.set_budget(
            by=by,
            cost_budget_usd=a.get("cost_budget_usd"),
            runtime_budget_minutes=a.get("runtime_budget_minutes"),
            allocation=a.get("allocation"),
            launch_max_cost_usd=_opt(a, "launch_max_cost_usd"),
            evaluation_budget_reserve_fraction=a.get("evaluation_budget_reserve_fraction"),
        ),
    ),
    Tool(
        "lab_set_role_model",
        "Set an agent role's model. Roles: orchestrator, planner, researcher, auditor, "
        "benchmark_builder, specialist, control, test_agent. reasoning_effort: minimal, low, "
        "medium, high, or null for the provider default. lab_describe_settings section=models "
        "gives each role's recommendation.",
        {
            "role": _s("The role."),
            "provider": _s("openai, anthropic or replay.", enum=["openai", "anthropic", "replay"]),
            "model": _s("Model id, e.g. gpt-6.1-sol or gpt-6-luna."),
            "reasoning_effort": _s(
                "minimal, low, medium, high, or null.",
                ["string", "null"],
                enum=["minimal", "low", "medium", "high", None],
            ),
            "max_output_tokens": _s("Cap per call; also what the budget reserves.", "integer"),
        },
        lambda c, a, by: c.ops.set_role_model(
            by=by,
            role=a["role"],
            provider=a.get("provider"),
            model=a.get("model"),
            reasoning_effort=_opt(a, "reasoning_effort"),
            max_output_tokens=a.get("max_output_tokens"),
        ),
        required=("role",),
    ),
    Tool(
        "lab_apply_recommended_models",
        "Set roles to the recommended model and reasoning level (GPT-6.1 Sol high for planning, "
        "benchmark and the answering arms; GPT-6 Luna medium for researchers and the auditor).",
        {"roles": _s("Only these roles (default all).", "array", items={"type": "string"})},
        lambda c, a, by: c.ops.apply_recommended_models(by=by, roles=a.get("roles")),
    ),
    Tool(
        "lab_list_prices",
        "Model prices in force (USD per million tokens) and any model in use that has none.",
        {},
        lambda c, a, by: c.ops.prices(),
        read_only=True,
    ),
    Tool(
        "lab_set_price",
        "Price a model in USD per million input and output tokens. A model with no price is "
        "refused before any spend.",
        {
            "model": _s("Model id."),
            "input_usd": _s("USD per million input tokens.", "number"),
            "output_usd": _s("USD per million output tokens.", "number"),
        },
        lambda c, a, by: c.ops.set_price(
            by=by,
            model=a["model"],
            input_usd=float(a["input_usd"]),
            output_usd=float(a["output_usd"]),
        ),
        required=("model", "input_usd", "output_usd"),
    ),
    Tool(
        "lab_delete_price",
        "Remove a model's price from the console's price list.",
        {"model": _s("Model id.")},
        lambda c, a, by: c.ops.set_price(by=by, model=a["model"], input_usd=None, output_usd=None),
        required=("model",),
        destructive=True,
    ),
    # -- connections ---------------------------------------------------------
    Tool(
        "lab_list_connections",
        "Pheasant connection profiles (transport, URL, knowledge base, source, token variable, "
        "whether a token is stored) and which one the draft uses.",
        {},
        lambda c, a, by: c.ops.list_connections(),
        read_only=True,
    ),
    Tool(
        "lab_save_connection",
        "Add or edit a Pheasant connection. transport: streamable_http (url ending /mcp, e.g. "
        "http://pheasant:8765/mcp), stdio (command) or mock. token is stored 0600 and never "
        "returned; an empty string removes it.",
        {
            "name": _s("Lowercase slug."),
            "description": _s("What this region is."),
            "transport": _s(
                "streamable_http, stdio or mock.", enum=["streamable_http", "stdio", "mock"]
            ),
            "url": _s("The region's MCP URL."),
            "command": _s("stdio only: the command."),
            "knowledge_base": _s("pheasant's `pheasant.name`, e.g. pheasant-lab."),
            "source_name": _s("The source the lab registers, e.g. swarm-lab-literature."),
            "token_env": _s("Variable holding the region's API token."),
            "token": _s("The token itself, to store."),
            "mock_claim_seconds": _s("mock only: simulated indexer claim delay.", "number"),
        },
        lambda c, a, by: c.ops.save_connection(
            {k: v for k, v in a.items() if k != "token"}, token=a.get("token")
        ),
        required=("name",),
    ),
    Tool(
        "lab_delete_connection",
        "Delete a connection added from the console (shipped ones can be edited, not deleted).",
        {"name": _s("Connection name.")},
        lambda c, a, by: c.ops.delete_connection(a["name"], by=by),
        required=("name",),
        destructive=True,
    ),
    Tool(
        "lab_select_connection",
        "Point the draft at a connection (its fields become overrides), or null for the config "
        "file's own region.",
        {"name": _s("Connection name, or null.", ["string", "null"])},
        lambda c, a, by: c.ops.select_connection(a.get("name"), by=by),
    ),
    Tool(
        "lab_probe_connection",
        "Ask a region about itself over HTTP: ready, index queue, what it holds for the lab's "
        "source. Default: the draft's own region.",
        {"name": _s("Connection name (optional).")},
        lambda c, a, by: c.ops.probe_connection(a.get("name")),
        read_only=True,
    ),
    # -- topics --------------------------------------------------------------
    Tool(
        "lab_list_topics",
        "Research topics the draft can run, and which is selected.",
        {},
        lambda c, a, by: c.ops.list_topics(),
        read_only=True,
    ),
    Tool(
        "lab_draft_topic",
        "Draft a research topic (title, seed terms, facets, window) from an intent plus whatever "
        "else is known (context: title, details, facets, date_range, preferred_types). model: "
        "gpt-6.1-sol (broader) or gpt-6-luna (fast); default the planner's. Writes nothing; save "
        "with lab_save_topic.",
        {
            "intent": _s("What you want to find out, in your own words."),
            "seed_terms": _s("Terms you already have.", "array", items={"type": "string"}),
            "model": _s("e.g. gpt-6.1-sol or gpt-6-luna."),
            "reasoning_effort": _s("minimal, low, medium or high."),
            "context": _s("The form so far.", "object"),
            "max_cost_usd": _s("Cap for this one call (default 0.25).", "number"),
        },
        lambda c, a, by: c.ops.draft_topic(
            intent=a.get("intent") or "",
            seed_terms=a.get("seed_terms"),
            model=a.get("model"),
            reasoning_effort=a.get("reasoning_effort"),
            context=a.get("context"),
            max_cost_usd=a.get("max_cost_usd"),
        ),
    ),
    Tool(
        "lab_save_topic",
        "Save a topic ({id, title, intent?, details?, seed_terms, date_range {from,to}, facets "
        "[{id,label,weight}], source_authority}) beside the current ones and select it.",
        {
            "topic": _s("The topic object.", "object"),
            "replace": _s("Replace a topic with the same id.", "boolean"),
            "select": _s("Select it for the next run (default true).", "boolean"),
        },
        lambda c, a, by: c.ops.save_topic(
            a["topic"], by=by, replace=bool(a.get("replace")), select=a.get("select", True)
        ),
        required=("topic",),
    ),
    Tool(
        "lab_select_topic",
        "Choose which topic the next run collects for (null: the first in the file).",
        {"topic_id": _s("Topic id, or null.", ["string", "null"])},
        lambda c, a, by: c.ops.select_topic(a.get("topic_id"), by=by),
    ),
    Tool(
        "lab_delete_topic",
        "Remove a topic from the console's topics file (shipped files are never edited).",
        {"topic_id": _s("Topic id.")},
        lambda c, a, by: c.ops.delete_topic(a["topic_id"], by=by),
        required=("topic_id",),
        destructive=True,
    ),
    # -- runs ----------------------------------------------------------------
    Tool(
        "lab_launch_run",
        "Start a run from the draft. kind: pipeline (collect, freeze, evaluate, replay, report, "
        "verify - the default), demo (offline, free), or one stage (collect, audit, "
        "freeze-benchmark, evaluate, replay, verify, report) with run_id. Runs detached; follow "
        "it with lab_get_launch or lab_get_run.",
        {
            "kind": _s("pipeline, demo, resume or a stage name."),
            "label": _s("A name for the run."),
            "run_id": _s("For resume and single stages."),
            "max_cost_usd": _s("Cap for this launch only.", "number"),
        },
        lambda c, a, by: c.ops.launch(
            kind=a.get("kind") or "pipeline",
            label=a.get("label"),
            run_id=a.get("run_id"),
            max_cost_usd=a.get("max_cost_usd"),
        ),
    ),
    Tool(
        "lab_list_launches",
        "Every launch (running and finished) with its status and output tail.",
        {},
        lambda c, a, by: c.ops.list_launches(),
        read_only=True,
    ),
    Tool(
        "lab_get_launch",
        "One launch's status, argv, run id and output tail.",
        {"launch_id": _s("A launch id.")},
        lambda c, a, by: c.ops.get_launch(a["launch_id"]),
        required=("launch_id",),
        read_only=True,
    ),
    Tool(
        "lab_stop_launch",
        "Stop a running launch cleanly (SIGINT); the run stays resumable.",
        {"launch_id": _s("A launch id.")},
        lambda c, a, by: c.ops.stop_launch(a["launch_id"]),
        required=("launch_id",),
    ),
    Tool(
        "lab_list_runs",
        "Every run this console can see: stages, label, whether live, complete, resumable, kept.",
        {},
        lambda c, a, by: c.ops.list_runs(),
        read_only=True,
    ),
    Tool(
        "lab_get_run",
        "One run: its row, a summary of the live view (phases, budget, custody, arms) and reports.",
        {"run_id": _RUN_ID},
        lambda c, a, by: c.ops.get_run(a["run_id"]),
        required=("run_id",),
        read_only=True,
    ),
    Tool(
        "lab_update_run",
        "Rename (label), annotate (notes) or protect from retention (keep) a run. The run's own "
        "files are never rewritten.",
        {
            "run_id": _RUN_ID,
            "label": _s("A name, or empty to clear.", ["string", "null"]),
            "notes": _s("Free notes, or empty to clear.", ["string", "null"]),
            "keep": _s("Exempt from every retention rule.", "boolean"),
        },
        lambda c, a, by: c.ops.update_run(
            a["run_id"], label=_opt(a, "label"), notes=_opt(a, "notes"), keep=a.get("keep")
        ),
        required=("run_id",),
    ),
    Tool(
        "lab_resume_run",
        "Resume an interrupted run where its checkpoint says, under its original configuration.",
        {"run_id": _RUN_ID},
        lambda c, a, by: c.ops.launch(kind="resume", run_id=a["run_id"]),
        required=("run_id",),
    ),
    Tool(
        "lab_delete_run",
        "Delete a whole run directory. Cannot be undone: confirm must equal the run id. Refused "
        "while a launch is writing it.",
        {"run_id": _RUN_ID, "confirm": _s("The run id again.")},
        lambda c, a, by: c.ops.delete_run(a["run_id"], confirm=a.get("confirm")),
        required=("run_id", "confirm"),
        destructive=True,
    ),
    # -- reports -------------------------------------------------------------
    Tool(
        "lab_list_reports",
        "A run's rendered reports (Markdown, CSV, JSON).",
        {"run_id": _RUN_ID},
        lambda c, a, by: c.ops.list_reports(a["run_id"]),
        required=("run_id",),
        read_only=True,
    ),
    Tool(
        "lab_read_report",
        "Read one report, e.g. summary.md.",
        {"run_id": _RUN_ID, "name": _s("Report file name.")},
        lambda c, a, by: c.ops.read_report(a["run_id"], a["name"]),
        required=("run_id", "name"),
        read_only=True,
    ),
    Tool(
        "lab_generate_reports",
        "Render (or re-render) a run's reports from its raw trace; starts a `report` launch.",
        {"run_id": _RUN_ID},
        lambda c, a, by: c.ops.generate_reports(a["run_id"]),
        required=("run_id",),
    ),
    Tool(
        "lab_delete_reports",
        "Delete a run's rendered reports (they are derived; lab_generate_reports rebuilds them).",
        {"run_id": _RUN_ID},
        lambda c, a, by: c.ops.delete_reports(a["run_id"]),
        required=("run_id",),
        destructive=True,
    ),
    # -- logs ----------------------------------------------------------------
    Tool(
        "lab_list_logs",
        "Every launch log and run on disk, sized by category, with the retention policy and the "
        "deletion audit.",
        {},
        lambda c, a, by: c.ops.logs_inventory(),
        read_only=True,
    ),
    Tool(
        "lab_read_log",
        "Read a launch log (launch_id) or a text file inside a run (run_id + path, e.g. "
        "raw/events.jsonl), paged and filtered by text or level; tail=true reads the end.",
        {
            "launch_id": _s("A launch id."),
            "run_id": _s("A run id."),
            "path": _s("A file inside the run."),
            "offset": _s("First line.", "integer"),
            "limit": _s("Lines (default 500).", "integer"),
            "query": _s("Text filter."),
            "level": _s("DEBUG, INFO, WARNING or ERROR."),
            "tail": _s("Read from the end.", "boolean"),
        },
        lambda c, a, by: c.ops.read_log(
            launch_id=a.get("launch_id"),
            run_id=a.get("run_id"),
            path=a.get("path"),
            offset=int(a.get("offset") or 0),
            limit=int(a.get("limit") or 500),
            query=a.get("query"),
            level=a.get("level"),
            tail=bool(a.get("tail")),
        ),
        read_only=True,
    ),
    Tool(
        "lab_delete_log",
        "Delete a launch log (what=launch_log, launch_id) or a run's derived projection or "
        "reports (what=projection|reports, run_id). Raw traces are kept or deleted only whole, "
        "with lab_delete_run.",
        {
            "what": _s(
                "launch_log, projection or reports.", enum=["launch_log", "projection", "reports"]
            ),
            "launch_id": _s("For a launch log."),
            "run_id": _s("For a projection or reports."),
        },
        lambda c, a, by: c.ops.delete_log(
            what=a.get("what") or "launch_log", launch_id=a.get("launch_id"), run_id=a.get("run_id")
        ),
        destructive=True,
    ),
    Tool(
        "lab_get_retention",
        "The retention policy and exactly what it would delete now.",
        {},
        lambda c, a, by: c.ops.retention(),
        read_only=True,
    ),
    Tool(
        "lab_set_retention",
        "Set retention: launch_log_days, max_launch_logs, run_days, max_runs, projection_days "
        "(null keeps forever), protect_reported, auto_apply, kept_runs.",
        {"policy": _s("Fields to change.", "object")},
        lambda c, a, by: c.ops.set_retention(dict(a.get("policy") or {})),
        required=("policy",),
    ),
    Tool(
        "lab_apply_retention",
        "Apply the retention policy now (deletes what lab_get_retention lists).",
        {},
        lambda c, a, by: c.ops.apply_retention(),
        destructive=True,
    ),
)

_BY_NAME = {tool.name: tool for tool in TOOLS}


def mcp_info(console: Console) -> dict[str, Any]:
    return {
        "endpoint": "/mcp",
        "transport": "streamable_http (JSON replies) or `pheasant-lab mcp` over stdio",
        "requires_key": console.token is not None,
        "tools": [tool.name for tool in TOOLS],
    }


def _result(payload: Any, *, error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str, indent=1)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if not error and isinstance(payload, dict):
        result["structuredContent"] = json.loads(json.dumps(payload, default=str))
    elif not error and isinstance(payload, list):
        result["structuredContent"] = {"items": json.loads(json.dumps(payload, default=str))}
    return result


def _call_tool(console: Console, params: dict[str, Any], client: str) -> dict[str, Any]:
    name = params.get("name")
    tool = _BY_NAME.get(str(name))
    if tool is None:
        raise LookupError(f"unknown tool {name!r}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _result("arguments must be an object", error=True)
    unknown = sorted(set(arguments) - set(tool.properties))
    if unknown:
        return _result(f"{tool.name} does not take {', '.join(unknown)}", error=True)
    missing = [key for key in tool.required if key not in arguments]
    if missing:
        return _result(f"{tool.name} needs {', '.join(missing)}", error=True)
    try:
        return _result(tool.call(console, arguments, client))
    except KeyError as exc:
        return _result(f"not found: {exc.args[0] if exc.args else exc}", error=True)
    except ValidationError as exc:
        from .operations import _refusal

        return _result(f"refused: {_refusal(exc)}", error=True)
    except (ValueError, ConfigError, TypeError) as exc:
        return _result(f"refused: {exc}", error=True)


def _one(console: Console, message: Any, client: str) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }
    method = message.get("method")
    ident = message.get("id")
    if method is None:
        return None  # a response to something we never ask; ignored
    if "id" not in message:
        return None  # a notification (initialized, cancelled): nothing to answer
    params = message.get("params") or {}
    try:
        if method == "initialize":
            asked = str(params.get("protocolVersion") or "")
            version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            result: Any = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [tool.schema() for tool in TOOLS]}
        elif method == "tools/call":
            result = _call_tool(console, params, client)
        elif method in ("resources/list", "prompts/list"):
            result = {method.split("/")[0]: []}
        else:
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
    except LookupError as exc:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def handle_jsonrpc(console: Console, body: Any, *, client: str = "mcp") -> Any:
    """One JSON-RPC message or a batch; ``None`` when nothing is owed a reply."""

    if isinstance(body, list):
        replies = [reply for reply in (_one(console, item, client) for item in body) if reply]
        return replies or None
    return _one(console, body, client)


def serve_stdio(console: Console, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """``pheasant-lab mcp``: newline-delimited JSON-RPC on stdin/stdout."""

    for line in stdin:
        if not line.strip():
            continue
        try:
            body = json.loads(line)
        except ValueError:
            reply: Any = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        else:
            reply = handle_jsonrpc(console, body, client="mcp-stdio")
        if reply is not None:
            stdout.write(json.dumps(reply, default=str) + "\n")
            stdout.flush()
