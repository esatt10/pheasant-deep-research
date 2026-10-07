import type {
  AuditRow,
  ConfigRow,
  Launch,
  LogInventory,
  LogPage,
  RetentionAction,
  RetentionPolicy,
  McpCall,
  RegionProbe,
  Resolved,
  RunModel,
  RunRow,
  TopicDoc,
  TopicDraft,
  TopicList,
  Trace,
  TraceActor,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/* ---- access key ---------------------------------------------------------
 * pheasant-kb's shape: a shared bearer key the console reads from
 * PHEASANT_LAB_CONSOLE_TOKEN. It belongs to the browser *tab*, not durable
 * storage, and never to the bundle - a key baked into JavaScript is a key
 * every visitor has.
 */

const KEY_STORAGE = "pheasant-lab.console.key";
const AUTH_REQUIRED = "pheasant-lab:auth-required";
const AUTH_CHANGED = "pheasant-lab:auth-changed";

export function getAccessKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setAccessKey(value: string): void {
  const key = value.trim();
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* session storage may be unavailable */
  }
  window.dispatchEvent(new Event(AUTH_CHANGED));
}

function listen(name: string, listener: () => void): () => void {
  const handler = () => listener();
  window.addEventListener(name, handler);
  return () => window.removeEventListener(name, handler);
}

export const onAuthRequired = (listener: () => void) => listen(AUTH_REQUIRED, listener);
export const signalAuthRequired = () => window.dispatchEvent(new Event(AUTH_REQUIRED));
export const onAuthChanged = (listener: () => void) => listen(AUTH_CHANGED, listener);

export function authHeaders(): Record<string, string> {
  const key = getAccessKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

/* ---- requests in flight -------------------------------------------------
 * Counted so the top bar can show that something is loading. Background
 * polls pass `quiet` - a bar that pulses every two seconds says nothing.
 */

let inflight = 0;
const inflightListeners = new Set<(n: number) => void>();

export function onInflight(listener: (n: number) => void): () => void {
  inflightListeners.add(listener);
  listener(inflight);
  return () => inflightListeners.delete(listener);
}

function track(delta: number) {
  inflight = Math.max(0, inflight + delta);
  inflightListeners.forEach((listener) => listener(inflight));
}

async function request<T>(path: string, init?: RequestInit & { quiet?: boolean }): Promise<T> {
  const { quiet, ...rest } = init ?? {};
  if (!quiet) track(1);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...rest,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(rest.headers ?? {}) },
    });
  } finally {
    if (!quiet) track(-1);
  }
  if (response.status === 401) signalAuthRequired();
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* no body */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

const quiet = <T,>(path: string) => request<T>(path, { quiet: true });

export interface ConfigRequest {
  config: string;
  set: string[];
  topic?: string;
}

export const api = {
  auth: () => quiet<{ required: boolean; authenticated: boolean }>("/auth"),
  runs: () => request<RunRow[]>("/runs"),
  runsQuiet: () => quiet<RunRow[]>("/runs"),
  run: (runId: string, until?: number) =>
    request<RunModel>(`/runs/${runId}${until != null ? `?until=${until}` : ""}`),
  reports: (runId: string) => request<{ reports: string[] }>(`/runs/${runId}/reports`),
  report: (runId: string, name: string) =>
    request<{ name: string; markdown: string }>(`/runs/${runId}/reports/${name}`),
  configs: () => request<ConfigRow[]>("/configs"),
  resolve: (body: ConfigRequest) => post<Resolved>("/config/resolve", body),
  plan: (body: ConfigRequest) =>
    post<{ exit_code: number; output: string; projection: Record<string, number | string | boolean> | null }>(
      "/plan",
      body,
    ),
  doctor: (body: ConfigRequest & { mock?: boolean }) =>
    post<{ exit_code: number; output: string }>("/doctor", body),
  launches: () => quiet<Launch[]>("/launches"),
  launch: (body: ConfigRequest & { kind: string; arms?: string; run_id?: string }) =>
    post<Launch>("/launches", body),
  stop: (launchId: string) => post<Launch>(`/launches/${launchId}/stop`, {}),
  traces: (runId: string) => request<{ actors: TraceActor[] }>(`/runs/${runId}/traces`),
  trace: (runId: string, actor: string) =>
    request<Trace>(`/runs/${runId}/traces/${encodeURIComponent(actor)}`),
  mcpCall: (runId: string, index: number) => request<McpCall>(`/runs/${runId}/mcp/${index}`),
  topics: (config: string, set: string[]) =>
    request<TopicList>(
      `/topics?config=${encodeURIComponent(config)}${set.map((s) => `&set=${encodeURIComponent(s)}`).join("")}`,
    ),
  draftTopic: (body: Omit<ConfigRequest, "topic"> & { intent: string; seed_terms?: string[] }) =>
    post<TopicDraft>("/topics/draft", body),
  addTopic: (body: Omit<ConfigRequest, "topic"> & { topic: TopicDoc; replace?: boolean }) =>
    post<{ topic: TopicDoc; replaced: boolean; topics_file: string; override: string; count: number }>("/topics", body),
  logs: () => request<LogInventory>("/logs"),
  logsQuiet: () => quiet<LogInventory>("/logs"),
  readLaunchLog: (launchId: string, options: LogQuery) =>
    request<LogPage>(`/logs/launches/${launchId}?${logQuery(options)}`, { quiet: options.quiet }),
  readRunFile: (runId: string, path: string, options: LogQuery) =>
    request<LogPage>(`/logs/runs/${runId}?path=${encodeURIComponent(path)}&${logQuery(options)}`, {
      quiet: options.quiet,
    }),
  deleteLaunchLog: (launchId: string) => request<AuditRow>(`/logs/launches/${launchId}`, { method: "DELETE" }),
  deleteRun: (runId: string) => request<AuditRow>(`/runs/${runId}`, { method: "DELETE" }),
  dropProjection: (runId: string) => request<AuditRow>(`/logs/runs/${runId}/projection`, { method: "DELETE" }),
  keepRun: (runId: string, keep: boolean) =>
    post<{ policy: RetentionPolicy }>(`/logs/runs/${runId}/keep`, { keep }),
  setRetention: (policy: Partial<RetentionPolicy>) =>
    request<{ policy: RetentionPolicy; plan: RetentionAction[] }>("/logs/retention", {
      method: "PUT",
      body: JSON.stringify({ policy }),
    }),
  previewRetention: (policy: Partial<RetentionPolicy>) =>
    post<{ plan: RetentionAction[] }>("/logs/retention/preview", { policy }),
  applyRetention: () => post<{ applied: RetentionAction[] }>("/logs/retention/apply", {}),
  region: (config?: string) =>
    quiet<RegionProbe>(`/region${config ? `?config=${encodeURIComponent(config)}` : ""}`),
};

export interface LogQuery {
  offset?: number;
  limit?: number;
  q?: string;
  level?: string;
  tail?: boolean;
  quiet?: boolean;
}

function logQuery(options: LogQuery): string {
  const params = new URLSearchParams();
  if (options.offset) params.set("offset", String(options.offset));
  if (options.limit) params.set("limit", String(options.limit));
  if (options.q) params.set("q", options.q);
  if (options.level) params.set("level", options.level);
  if (options.tail) params.set("tail", "1");
  return params.toString();
}
