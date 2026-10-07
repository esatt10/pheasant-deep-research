# Configuring and running the lab over MCP and HTTP

The console has two surfaces over **one implementation**
(`src/pheasant_lab/console/operations.py`): the HTTP API its UI reads, and MCP
tools for agents. A setting changed over MCP is the setting the browser shows,
because both edit the same settings draft (`<runs>/.console/workspace.json`).
A launch turns the draft into an ordinary `pheasant-lab run --set …` argv.

## Connecting

| Transport | How |
|---|---|
| Streamable HTTP | `POST http://127.0.0.1:8770/mcp` (JSON replies; no GET stream) |
| stdio | `pheasant-lab mcp --config configs/demo.yaml` |

With `PHEASANT_LAB_CONSOLE_TOKEN` set, `/mcp` needs it as a bearer token, like
`/api`. A Claude Code entry:

```json
{
  "mcpServers": {
    "pheasant-lab": {
      "type": "http",
      "url": "http://127.0.0.1:8770/mcp",
      "headers": { "Authorization": "Bearer ${PHEASANT_LAB_CONSOLE_TOKEN}" }
    }
  }
}
```

## Tools

| Area | Tools |
|---|---|
| Settings | `lab_describe_settings`, `lab_get_settings`, `lab_update_settings`, `lab_reset_settings`, `lab_command_line`, `lab_plan`, `lab_doctor` |
| Budget and models | `lab_get_budget`, `lab_set_budget`, `lab_set_role_model`, `lab_apply_recommended_models`, `lab_list_prices`, `lab_set_price`, `lab_delete_price` |
| Pheasant connection | `lab_list_connections`, `lab_save_connection`, `lab_delete_connection`, `lab_select_connection`, `lab_probe_connection` |
| Topics | `lab_list_topics`, `lab_draft_topic`, `lab_save_topic`, `lab_select_topic`, `lab_delete_topic` |
| Runs | `lab_launch_run`, `lab_list_launches`, `lab_get_launch`, `lab_stop_launch`, `lab_list_runs`, `lab_get_run`, `lab_update_run`, `lab_resume_run`, `lab_delete_run` |
| Reports | `lab_list_reports`, `lab_read_report`, `lab_generate_reports`, `lab_delete_reports` |
| Logs | `lab_list_logs`, `lab_read_log`, `lab_delete_log`, `lab_get_retention`, `lab_set_retention`, `lab_apply_retention` |

Every tool declares `readOnlyHint` / `destructiveHint`. Deleting a run needs
`confirm` equal to the run id. A refusal comes back as `isError: true` with the
reason in words.

### A session

```text
lab_describe_settings  {"section": "models"}           # what each role does, recommended model + reasoning
lab_apply_recommended_models {}                        # GPT-6.1 Sol / GPT-6 Luna per role
lab_set_price          {"model": "gpt-6.1-sol", "input_usd": …, "output_usd": …}
lab_set_budget         {"cost_budget_usd": 25, "launch_max_cost_usd": 10}
lab_select_connection  {"name": "docker"}
lab_draft_topic        {"intent": "…", "model": "gpt-6-luna", "context": {"details": "…"}}
lab_save_topic         {"topic": {…the draft, edited…}}
lab_doctor             {}
lab_launch_run         {"label": "first live run"}
lab_get_run            {"run_id": "run-…"}
lab_read_report        {"run_id": "run-…", "name": "summary.md"}
```

## Every setting is explained

`lab_describe_settings` (HTTP `GET /api/settings/catalog`, CLI
`pheasant-lab settings`) returns, for every `--set` key: what it means, the
values it takes, its range and unit, its default, its current value, whether it
moves the config digest, and — for agent roles — the recommended model and
reasoning level:

| Role | Recommended | Why |
|---|---|---|
| orchestrator, planner | `gpt-6.1-sol`, high | few calls, each decides where the budget goes / which vocabulary is searched |
| researcher, auditor | `gpt-6-luna`, medium | the volume roles: many concurrent calls over passages already in hand |
| benchmark_builder | `gpt-6.1-sol`, high | called once; every metric is measured against what it writes |
| specialist, control, test_agent | `gpt-6.1-sol`, high — **the same for all three** | `P1 − S0` must compare Pheasant with the sources, not one model with another |

The catalog is derived from the configuration models: a field with no
explanation, or an explanation for a field that is gone, fails
`tests/unit/test_catalog.py`.

## HTTP routes added for this

| Route | Does |
|---|---|
| `GET/PUT /api/settings`, `POST /api/settings/reset` | the shared draft (`{"set": {key: value or null}, "unset": [...], "config", "topic", "connection", "launch": {"max_cost_usd", "label"}, "strict"}`) |
| `GET /api/settings/catalog?section=&key=&advanced=` | the catalog |
| `GET /api/settings/command`, `GET /api/plan` | the draft's argv and cost projection |
| `GET/PUT /api/budget` | the budget |
| `PUT /api/models/{role}`, `POST /api/models/recommended` | role models |
| `GET /api/prices`, `PUT/DELETE /api/prices/{model}` | prices |
| `GET/POST /api/connections`, `DELETE /api/connections/{name}`, `POST /api/connections/select`, `GET /api/connections/{name}/probe` | connections |
| `POST /api/topics/select`, `DELETE /api/topics/{id}` | topics |
| `POST /api/runs/launch`, `POST /api/runs/{id}/resume`, `PUT /api/runs/{id}`, `GET /api/runs/{id}/detail` | runs |
| `POST/DELETE /api/runs/{id}/reports` | render / delete reports |
