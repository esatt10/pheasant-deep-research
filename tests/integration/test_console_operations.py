"""One implementation, two surfaces: the console's HTTP API and its MCP tools.

What is held here: a setting changed over MCP is the setting the browser
reads (one draft), every setting is explained, the budget and the Pheasant
connection are adjustable from both surfaces, a stored token never comes back
out, runs/reports/logs are manageable end to end, and a console scoped to a
deployment shows only the runs made there.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from pheasant_lab.console.server import serve

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = "operations-console-key"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("PHEASANT_LAB_LOCAL_DIR", raising=False)
    monkeypatch.delenv("PHEASANT_LAB_RUN_SCOPE", raising=False)
    shutil.copytree(
        REPO_ROOT / "configs",
        tmp_path / "project" / "configs",
        ignore=shutil.ignore_patterns("*.local.yaml"),
    )
    return tmp_path / "project"


def _make_run(root: Path, run_id: str, *, deployment: str | None) -> Path:
    run = root / run_id
    (run / "raw").mkdir(parents=True)
    (run / "reports").mkdir()
    environment = {"python": "3"} | ({"deployment": deployment} if deployment else {})
    (run / "run-manifest.json").write_text(
        json.dumps({"run_id": run_id, "experiment_name": "t", "environment": environment})
    )
    (run / "raw" / "events.jsonl").write_text('{"sequence": 1, "event_type": "run.started"}\n')
    (run / "reports" / "summary.md").write_text("# summary\n")
    return run


def _server(project: Path, runs: Path, **kwargs):
    server = serve(
        host="127.0.0.1",
        port=0,
        project_root=project,
        output_root=runs,
        default_config="configs/demo.yaml",
        ui_dist=project / "no-ui",
        env_file=None,
        **kwargs,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def console(project: Path) -> Iterator[tuple[str, Path]]:
    runs = project / "runs"
    _make_run(runs, "run-aaaaaaaaaaaaaaaa", deployment="docker")
    server = _server(project, runs, token=KEY)
    yield f"http://127.0.0.1:{server.server_address[1]}", runs
    server.shutdown()
    server.server_close()


def _call(url: str, *, method: str = "GET", body=None, key: str | None = KEY):
    request = urllib.request.Request(url, method=method)
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    if body is not None:
        request.data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text, status = response.read().decode(), response.status
    except urllib.error.HTTPError as error:
        text, status = error.read().decode(), error.code
    try:
        return status, json.loads(text) if text else None
    except ValueError:
        return status, text


def _mcp(
    base: str, method: str, params: dict | None = None, *, key: str | None = KEY, ident: int = 1
):
    return _call(
        f"{base}/mcp",
        method="POST",
        body={"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}},
        key=key,
    )


def _tool(base: str, tool_name: str, **arguments):
    status, reply = _mcp(base, "tools/call", {"name": tool_name, "arguments": arguments})
    assert status == 200, reply
    result = reply["result"]
    return result, result.get("structuredContent")


# -- settings ------------------------------------------------------------------


def test_a_setting_changed_over_mcp_is_the_one_the_browser_reads(console) -> None:
    base, _ = console
    result, body = _tool(base, "lab_update_settings", set={"collection.max_research_agents": 4})
    assert not result["isError"] and body["valid"] is True
    status, settings = _call(f"{base}/api/settings")
    assert status == 200
    assert settings["draft"]["overrides"]["collection.max_research_agents"] == "4"
    assert settings["resolved"]["values"]["collection.max_research_agents"] == 4
    assert settings["draft"]["updated_by"] == "mcp"

    # ...and the other way round.
    status, updated = _call(
        f"{base}/api/settings", method="PUT", body={"set": {"collection.max_research_agents": None}}
    )
    assert status == 200 and "collection.max_research_agents" not in updated["draft"]["overrides"]
    _, body = _tool(base, "lab_get_settings")
    assert "collection.max_research_agents" not in body["draft"]["overrides"]


def test_an_invalid_draft_is_kept_and_reported_unless_strict(console) -> None:
    base, _ = console
    status, body = _call(
        f"{base}/api/settings", method="PUT", body={"set": {"budget.allocation.planning": 0.5}}
    )
    assert status == 200 and body["valid"] is False and "sum to 1.0" in body["error"]
    status, body = _call(
        f"{base}/api/settings",
        method="PUT",
        body={"set": {"budget.allocation.planning": 0.6}, "strict": True},
    )
    assert status == 400 and "sum to 1.0" in body["detail"]
    status, body = _call(f"{base}/api/settings", method="PUT", body={"set": {"nonsense.key": 1}})
    assert status == 400 and "names no configuration section" in body["detail"]


def test_every_field_is_explained_on_both_surfaces(console) -> None:
    base, _ = console
    status, described = _call(f"{base}/api/settings/catalog?section=search")
    assert status == 200 and described["fields"]
    assert all(row["help"] for row in described["fields"])
    keys = {row["key"] for row in described["fields"]}
    assert {"replay.search_mode", "replay.max_results_per_search"} <= keys
    _, one = _tool(base, "lab_describe_settings", key="models.researcher.reasoning_effort")
    assert one["recommended"] == "medium" and "minimal" in one["choices"]


def test_the_budget_is_adjustable_from_both_surfaces(console) -> None:
    base, _ = console
    _, budget = _tool(base, "lab_set_budget", cost_budget_usd=25, launch_max_cost_usd=5)
    assert budget["cost_budget_usd"] == 25 and budget["launch_max_cost_usd"] == 5
    status, budget = _call(
        f"{base}/api/budget",
        method="PUT",
        body={"allocation": {"planning": 0.05, "collection": 0.45}},
    )
    assert status == 200 and budget["allocation"]["collection"] == 0.45
    status, refused = _call(f"{base}/api/budget", method="PUT", body={"cost_budget_usd": 0})
    assert status == 400 and "positive" in refused["detail"]
    _, command = _tool(base, "lab_command_line")
    assert (
        "--max-cost-usd" in command["argv"] and "experiment.cost_budget_usd=25" in command["text"]
    )


def test_role_models_take_the_recommendation(console) -> None:
    base, _ = console
    _, body = _tool(base, "lab_apply_recommended_models", roles=["researcher", "planner"])
    values = body["draft"]["overrides"]
    assert values["models.researcher.model"] == "gpt-6-luna"
    assert values["models.planner.model"] == "gpt-6.1-sol"
    assert values["models.planner.reasoning_effort"] == "high"
    # Unpriced models are a refusal the draft reports, not a silent free model.
    assert body["valid"] is False and "price" in body["error"]
    _, prices = _tool(base, "lab_set_price", model="gpt-6-luna", input_usd=0.5, output_usd=2)
    assert prices["models"]["gpt-6-luna"] == {"input": 0.5, "output": 2.0}
    _, prices = _tool(base, "lab_set_price", model="gpt-6.1-sol", input_usd=2, output_usd=8)
    _, settings = _tool(base, "lab_get_settings")
    assert settings["valid"] is True, settings["error"]


# -- connections -----------------------------------------------------------------


def test_a_connection_is_chosen_and_its_token_never_comes_back(console, project: Path) -> None:
    base, runs = console
    status, saved = _call(
        f"{base}/api/connections",
        method="POST",
        body={
            "connection": {
                "name": "team-region",
                "transport": "streamable_http",
                "url": "http://region.example:8765/mcp",
                "knowledge_base": "team-kb",
                "source_name": "lab-src",
            },
            "token": "s3cret-token-value",
        },
    )
    assert status == 201 and saved["token_stored"] is True
    assert "s3cret-token-value" not in json.dumps(saved)
    assert saved["token_env"] == "PHEASANT_TOKEN_TEAM_REGION"
    secrets = runs / ".console" / "secrets.json"
    assert stat.S_IMODE(os.stat(secrets).st_mode) == 0o600

    _, chosen = _tool(base, "lab_select_connection", name="team-region")
    assert chosen["draft"]["connection"] == "team-region"
    pheasant = chosen["resolved"]["pheasant"]
    assert pheasant["url"] == "http://region.example:8765/mcp"
    assert pheasant["knowledge_base"] == "team-kb"
    assert pheasant["token_env"] == "PHEASANT_TOKEN_TEAM_REGION"
    _, listed = _tool(base, "lab_list_connections")
    assert "s3cret-token-value" not in json.dumps(listed)
    assert {"mock", "docker", "lab-fleet"} <= {row["name"] for row in listed["connections"]}

    _, back = _tool(base, "lab_select_connection", name="mock")
    assert back["resolved"]["pheasant"]["transport"] == "mock"
    status, _ = _call(f"{base}/api/connections/team-region", method="DELETE")
    assert status == 200
    assert "PHEASANT_TOKEN_TEAM_REGION" not in json.loads(secrets.read_text())
    status, refused = _call(f"{base}/api/connections/docker", method="DELETE")
    assert status == 409 and "ships with the lab" in refused["detail"]


# -- runs, reports, logs ----------------------------------------------------------


def test_a_run_is_renamed_kept_reported_and_deleted_whole(console) -> None:
    base, runs = console
    run_id = "run-aaaaaaaaaaaaaaaa"
    _, updated = _tool(base, "lab_update_run", run_id=run_id, label="first try", keep=True)
    assert updated["label"] == "first try" and updated["kept"] is True
    status, rows = _call(f"{base}/api/runs")
    assert rows[0]["label"] == "first try"
    assert (runs / run_id / "run-manifest.json").read_text().find("first try") == -1

    _, reports = _tool(base, "lab_list_reports", run_id=run_id)
    assert [r["name"] for r in reports["reports"]] == ["summary.md"]
    _, read = _tool(base, "lab_read_report", run_id=run_id, name="summary.md")
    assert read["markdown"].startswith("# summary")
    status, _ = _call(f"{base}/api/runs/{run_id}/reports", method="DELETE")
    assert status == 200 and not (runs / run_id / "reports").exists()

    result, _ = _tool(base, "lab_delete_run", run_id=run_id, confirm="wrong")
    assert result["isError"] and "cannot be undone" in result["content"][0]["text"]
    result, _ = _tool(base, "lab_delete_run", run_id=run_id, confirm=run_id)
    assert not result["isError"] and not (runs / run_id).exists()
    _, logs = _tool(base, "lab_list_logs")
    assert any(row["kind"] == "run" and row["target"] == run_id for row in logs["audit"])


def test_a_console_scoped_to_docker_shows_only_docker_runs(project: Path) -> None:
    runs = project / "runs"
    _make_run(runs, "run-dddddddddddddddd", deployment="docker")
    _make_run(runs, "run-llllllllllllllll", deployment=None)
    server = _server(project, runs, run_scope="docker")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, rows = _call(f"{base}/api/runs", key=None)
        assert status == 200 and [r["run_id"] for r in rows] == ["run-dddddddddddddddd"]
        assert _call(f"{base}/api/runs/run-llllllllllllllll", key=None)[0] == 404
        status, inventory = _call(f"{base}/api/logs", key=None)
        assert [r["run_id"] for r in inventory["runs"]] == ["run-dddddddddddddddd"]
        assert _call(f"{base}/api/runs/run-llllllllllllllll", method="DELETE", key=None)[0] == 404
        assert (runs / "run-llllllllllllllll").is_dir()
    finally:
        server.shutdown()
        server.server_close()


# -- the protocol ------------------------------------------------------------------


def test_the_mcp_endpoint_speaks_the_protocol_and_needs_the_key(console) -> None:
    base, _ = console
    status, _ = _mcp(base, "initialize", {"protocolVersion": "2026-07-28"}, key=None)
    assert status == 401
    status, init = _mcp(base, "initialize", {"protocolVersion": "2026-07-28", "capabilities": {}})
    assert status == 200 and init["result"]["protocolVersion"] == "2026-07-28"
    assert init["result"]["serverInfo"]["name"] == "pheasant-lab"
    status, _ = _call(
        f"{base}/mcp",
        method="POST",
        body={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert status == 202
    _, listed = _mcp(base, "tools/list")
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {
        "lab_describe_settings",
        "lab_update_settings",
        "lab_set_budget",
        "lab_select_connection",
        "lab_draft_topic",
        "lab_launch_run",
        "lab_delete_run",
        "lab_generate_reports",
        "lab_read_log",
    } <= names
    destructive = {
        t["name"] for t in listed["result"]["tools"] if t["annotations"]["destructiveHint"]
    }
    assert "lab_delete_run" in destructive and "lab_get_settings" not in destructive
    _, unknown = _mcp(base, "tools/call", {"name": "lab_nope", "arguments": {}})
    assert unknown["error"]["code"] == -32602
    result, _ = _tool(base, "lab_update_settings", bogus=1)
    assert result["isError"] and "does not take bogus" in result["content"][0]["text"]
    assert _call(f"{base}/mcp", key=KEY)[0] == 405


def test_a_topic_is_drafted_with_context_saved_and_selected_over_mcp(console) -> None:
    base, _ = console
    _, draft = _tool(
        base,
        "lab_draft_topic",
        intent="How do tardigrades survive extreme ionising radiation?",
        context={
            "details": "Compare Dsup with DNA repair pathways",
            "title": "Tardigrade radiation",
        },
    )
    assert draft["drafted_by"]["deterministic"] is True and draft["facets"]
    assert draft["details"] == "Compare Dsup with DNA repair pathways"
    topic = {
        k: draft[k]
        for k in ("id", "title", "intent", "details", "seed_terms", "date_range", "facets")
    }
    topic["facets"] = [{k: f[k] for k in ("id", "label", "weight")} for f in topic["facets"]]
    _, saved = _tool(base, "lab_save_topic", topic=topic)
    assert saved["topic"]["details"] == "Compare Dsup with DNA repair pathways"
    _, listed = _tool(base, "lab_list_topics")
    assert listed["selected"] == draft["id"]
    _, removed = _tool(base, "lab_delete_topic", topic_id=draft["id"])
    assert removed["deleted"] == draft["id"]
    _, listed = _tool(base, "lab_list_topics")
    assert listed["selected"] is None


def test_a_hosted_draft_without_a_price_is_refused_in_words(console) -> None:
    base, _ = console
    result, _ = _tool(
        base,
        "lab_draft_topic",
        intent="Why do some tardigrades survive radiation?",
        model="gpt-6.1-sol",
    )
    assert result["isError"]
    assert "gpt-6.1-sol" in result["content"][0]["text"]
    assert "Model prices" in result["content"][0]["text"]
