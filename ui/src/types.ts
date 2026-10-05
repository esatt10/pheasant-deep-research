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
