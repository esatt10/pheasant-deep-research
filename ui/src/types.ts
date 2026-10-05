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
  status: "running" | "succeeded" | "completed_with_findings" | "failed" | "refused" | "stopped";
  exit_code: number | null;
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
}
