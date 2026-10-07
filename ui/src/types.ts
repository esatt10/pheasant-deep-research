/** The shapes `pheasant-lab serve` returns. The model is `console/projection.py`'s snapshot. */

export interface RunRow {
  run_id: string;
  experiment: string | null;
  topic_title: string | null;
  command: string | null;
  created_at: string | null;
  updated_at: number | null;
  stages: Record<string, string>;
  reported: boolean;
  arms: string[] | null;
  cost_budget_usd: number | null;
  mock: boolean;
  config: string | null;
  overrides: string[];
  complete: boolean;
  interrupted: boolean;
  live: boolean;
  resumable: boolean;
  deployment?: string;
  label?: string | null;
  notes?: string | null;
  kept?: boolean;
}

export interface Phase {
  key: string;
  label: string;
  state: "done" | "active" | "pending";
}

export interface Agent {
  agent_id: string;
  role: string;
  short_id: string;
  subtopic_id: string | null;
  subtopic_label: string | null;
  searches: number;
  discovered: number;
  acquired: number;
  submitted: number;
  tool_calls: number;
  cost_usd: number;
  last_event: string | null;
  last_at: number | null;
  last_query: string | null;
  status: "searching" | "idle";
}

export type Stage =
  | "discovered"
  | "acquired"
  | "submitted"
  | "accepted"
  | "awaiting_claim"
  | "claimed"
  | "indexed"
  | "rejected";

export interface Source {
  source_id: string;
  title: string | null;
  subtopic_id: string | null;
  agent_id: string | null;
  provider: string | null;
  identifier: string | null;
  stage: Stage;
  waiting_seconds: number | null;
  mode?: string;
}

export interface Bar {
  lane: string;
  start: number;
  end: number | null;
  label: string;
  status: string | null;
}

export interface Tick {
  lane: string;
  t: number;
  kind: string;
  label: unknown;
  arm?: string | null;
}

export interface Lane {
  id: string;
  label: string;
  kind: "orchestrator" | "planner" | "researcher" | "auditor" | "pheasant" | "indexer" | "arm";
  bars: Bar[];
  ticks: Tick[];
}

export interface Notice {
  tone: "info" | "warn" | "danger" | "ok";
  title: string;
  detail: string;
  at?: number | null;
  code: string;
  closed?: boolean;
}

export interface QueueTask {
  task_id: string;
  state: string;
  waiting_seconds: number | null;
  claimed_by: string | null;
  position: number | null;
  source?: string;
}

export interface Facet {
  facet_id: string;
  label: string;
  sources: number;
  families: number;
  authoritative: number;
  claims: number;
  meets_minimum: boolean;
  unmet: string[];
  weight: number;
}

export interface RunModel {
  run: {
    run_id: string;
    experiment: string | null;
    topic_id: string | null;
    topic_title: string | null;
    command: string | null;
    config_digest: string | null;
    created_at: string | null;
    sequence: number;
    elapsed_seconds: number;
    horizon_seconds: number;
    finished: boolean;
    phases: Phase[];
    arms_configured: string[];
    repetitions?: number;
    replay_of?: number;
  };
  agents: Agent[];
  subtopics: { subtopic_id: string; question: string | null; facet_ids: string[]; label: string }[];
  sources: Source[];
  custody: Record<string, number>;
  lanes: Lane[];
  rounds: { round: number; at: number | null; decision: string | null }[];
  budget: {
    total_usd: number;
    committed_usd: number;
    buckets: { bucket: string; budget_usd: number; committed_usd: number }[];
  };
  facets: Facet[];
  audit: Record<string, unknown>;
  arms: {
    arm_id: string;
    label: string;
    answered: number;
    abstained: number;
    mean_latency_ms: number | null;
    cost_usd: number;
  }[];
  questions_total: number | null;
  region: {
    server_name?: string;
    server_version?: string;
    protocol_version?: string;
    tool_calls: Record<string, number>;
    sync: { disposition?: string; task_id?: string | null; at?: number | null };
    barrier: {
      state?: string;
      poll?: number;
      waited_seconds?: number;
      still_accepted?: number;
      acknowledged?: number;
    };
    queue: QueueTask[] | null;
    inventory?: {
      disposition?: "consistent" | "mismatch";
      region_documents?: number;
      region_bytes?: number;
      receipts_indexed?: number;
      receipts?: number;
    } | null;
  };
  notices: Notice[];
  notice_history: Notice[];
}

export interface LabEvent {
  event_id: string;
  sequence: number;
  occurred_at: string;
  event_type: string;
  status: string;
  agent_id: string | null;
  agent_role: string | null;
  arm_id: string | null;
  question_id: string | null;
  payload: Record<string, unknown>;
}

export interface Launch {
  launch_id: string;
  kind: string;
  argv: string[];
  config: string;
  run_id: string | null;
  status:
    | "running"
    | "succeeded"
    | "completed_with_findings"
    | "failed"
    | "refused"
    | "stopped"
    | "crashed_resumable"
    | "interrupted"
    | "ended";
  exit_code: number | null;
  pid: number | null;
  step: string | null;
  started_at: number;
  finished_at: number | null;
  output_tail: string[];
}

export interface ConfigRow {
  path: string;
  name: string;
  default: boolean;
}

export interface Resolved {
  config: string;
  digest: string;
  resolved: Record<string, any>;
  pheasant: {
    transport: string;
    url: string;
    knowledge_base: string;
    source_name: string;
    mock_claim_seconds: number;
    capabilities: Record<string, { tool: string; required: boolean }>;
  };
  topics: { id: string; title: string; facets: number }[];
  models: Record<string, string>;
  unresolved_env: string[];
  source_files: Record<string, string>;
  /** Every catalog field's value in force, by its --set key. */
  values?: Record<string, unknown>;
  advice?: Advice[];
}

/* ---- settings catalog, draft, connections (console/operations.py) ------ */

export interface Advice {
  key: string;
  level: "warn" | "info";
  message: string;
  related: string[];
}

export interface CatalogField {
  key: string;
  section: string;
  label: string;
  kind: "int" | "float" | "bool" | "str" | "enum" | "list" | "map" | "any";
  nullable: boolean;
  default: unknown;
  choices: unknown[] | null;
  suggestions: string[] | null;
  minimum: number | null;
  maximum: number | null;
  unit: string | null;
  help: string;
  values: string;
  advanced: boolean;
  moves_digest: boolean;
  current?: unknown;
  recommended?: string;
  role?: string;
}

export interface RoleAdviceRow {
  role: string;
  does?: string;
  model?: string;
  reasoning_effort?: string;
  why?: string;
  calls?: string;
}

export interface Catalog {
  sections: { key: string; title: string; blurb: string }[];
  fields: CatalogField[];
  roles: RoleAdviceRow[];
  draft_models: { model: string; provider: string; reasoning_effort: string; label: string; note: string }[];
  reasoning_efforts: string[];
  providers: string[];
  advice: Advice[];
}

export interface Draft {
  revision: number;
  updated_at: number | null;
  updated_by: string | null;
  config: string;
  overrides: Record<string, string>;
  topic: string | null;
  connection: string | null;
  launch: { max_cost_usd: number | null; label: string | null; arms?: string | null };
}

export interface Settings {
  draft: Draft;
  valid: boolean;
  error: string | null;
  resolved: Resolved | null;
}

export interface Connection {
  name: string;
  description: string;
  transport: "streamable_http" | "stdio" | "mock";
  url: string | null;
  command: string | null;
  knowledge_base: string | null;
  source_name: string | null;
  token_env: string | null;
  mock_claim_seconds: number | null;
  shipped: boolean;
  edited: boolean;
  token_stored: boolean;
  token_in_environment: boolean;
}

export interface Prices {
  source: string;
  local_file: string;
  unit: string;
  currency: string;
  models: Record<string, { input: number; output: number }>;
  unpriced_in_use: string[];
  in_force: string;
}

export interface Budget {
  cost_budget_usd: number;
  runtime_budget_minutes: number;
  allocation: Record<string, number>;
  evaluation_budget_reserve_fraction: number;
  launch_max_cost_usd: number | null;
  draft_budget_usd: number;
  explain: Record<string, string>;
}

export interface RegionProbe {
  reachable: boolean | null;
  transport: string;
  mock?: boolean;
  base_url?: string;
  ready?: {
    status: string;
    role?: string;
    reason?: string;
    indexes_locally?: boolean;
    leader?: boolean;
    graph_generation?: { loaded?: string | null; published?: string | null };
  };
  queue?: { enabled: boolean; listing: string; tasks: (QueueTask & { source: string })[] } | null;
  queue_unsupported?: boolean;
  /** `/sources/{name}/overview` for the lab's source (pheasant >= 0.13.3). */
  inventory?: {
    source_name: string;
    documents?: number | null;
    size_bytes?: number | null;
    status?: string | null;
    last_indexed_at?: string | null;
  } | null;
  notices: Notice[];
}

/* ---- traces (console/traces.py) ---------------------------------------- */

export interface TraceActor {
  actor: string;
  kind: "orchestration" | "agent" | "arm";
  label: string;
  role: string | null;
  events: number;
  spans: number;
  mcp_calls: number;
  model_calls: number;
  cost_usd: number;
  tokens: number;
  failures: number;
}

export interface TraceEvent {
  event_id: string;
  sequence: number;
  t: number | null;
  event_type: string;
  status: string | null;
  question_id: string | null;
  payload: Record<string, unknown>;
  mcp_call?: number;
  placed?: "by_time" | "by_question";
}

export interface TraceSpan {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  status: string | null;
  error: string | null;
  start: number | null;
  end: number | null;
  duration_ms: number | null;
  attributes: Record<string, string>;
  children: TraceSpan[];
  events: TraceEvent[];
}

export interface TraceAnswer {
  question_id: string;
  question: string | null;
  question_type: string | null;
  cohorts: string[] | null;
  repetition: number | null;
  status: string | null;
  abstained: boolean | null;
  abstention_reason: string | null;
  answer_text: string | null;
  claims: { text?: string; claim_text?: string; citations?: string[] }[];
  queries_used: string[];
  read_passages: { artifact_id?: string; source_id?: string; text_source?: string }[];
  search_calls: Record<string, unknown>[] | null;
  latency_ms: number | null;
  cost_usd: number | null;
  model: string | null;
  snapshot_id: string | null;
  error: string | null;
}

export interface TraceClaim {
  claim_id: string;
  claim_text: string;
  claim_type?: string;
  support?: string;
  source_id?: string;
  subtopic_id?: string;
  round?: number;
  facet_ids?: string[];
  locator?: string;
  eligible?: boolean;
}

export interface Trace {
  actor: string;
  label: string;
  window: { start: number; end: number };
  spans: TraceSpan[];
  events: TraceEvent[];
  unjoined_mcp_calls: { mcp_call: number; tool: string; status: string; duration_ms: number; question_id: string | null; t: number | null }[];
  answers?: TraceAnswer[];
  claims?: TraceClaim[];
  errors: Record<string, unknown>[];
}

export interface McpCall {
  index: number;
  tool: string;
  status: string;
  attempt: number;
  duration_ms: number;
  arm_id: string | null;
  question_id: string | null;
  recorded_at: string;
  request: unknown;
  response: unknown;
}

/* ---- topics (console/topics.py) ---------------------------------------- */

export interface TopicFacet {
  id: string;
  label: string;
  weight: number;
}

export interface TopicDoc {
  id: string;
  title: string;
  intent?: string | null;
  details?: string | null;
  seed_terms: string[];
  date_range: { from: string | null; to: string | null };
  facets: TopicFacet[];
  source_authority: { family_key: string[]; preferred_types: string[]; minimum_peer_reviewed: number };
}

export interface TopicList {
  topics_file: string;
  local_file: string;
  override: string;
  topics: TopicDoc[];
  selected?: string | null;
}

export interface TopicDraft extends TopicDoc {
  notes: string;
  facets: (TopicFacet & { rationale?: string })[];
  drafted_by: {
    provider: string;
    model: string;
    deterministic: boolean;
    cost_usd: number;
    input_tokens: number;
    output_tokens: number;
  };
}

/* ---- logs (console/logs.py) -------------------------------------------- */

export interface RetentionPolicy {
  launch_log_days: number | null;
  max_launch_logs: number | null;
  projection_days: number | null;
  run_days: number | null;
  max_runs: number | null;
  protect_reported: boolean;
  kept_runs: string[];
  auto_apply: boolean;
}

export interface RetentionAction {
  kind: "launch_log" | "run" | "projection";
  target: string;
  reason: string;
  skipped?: string;
  freed_bytes?: number;
}

export interface AuditRow {
  at: number;
  kind: RetentionAction["kind"];
  target: string;
  freed_bytes: number;
  reason: string;
}

export interface LogInventory {
  output_root: string;
  totals: { launch_logs: number; runs: number; projections: number };
  launches: {
    launch_id: string;
    kind: string;
    status: Launch["status"];
    run_id: string | null;
    started_at: number;
    finished_at: number | null;
    size_bytes: number;
    deletable: boolean;
  }[];
  runs: {
    run_id: string;
    updated_at: number;
    size_bytes: number;
    categories: Record<string, number>;
    files: { path: string; size_bytes: number }[];
    live: boolean;
    reported: boolean;
    kept: boolean;
  }[];
  policy: RetentionPolicy;
  audit: AuditRow[];
}

export interface LogPage {
  lines: { n: number; text: string }[];
  offset: number;
  limit: number;
  matched: number;
  total_lines: number;
  size_bytes: number;
  has_more: boolean;
}
