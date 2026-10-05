import type {
  ConfigRow,
  Launch,
  McpCall,
  RegionProbe,
  Resolved,
  RunModel,
  RunRow,
  TopicDoc,
  TopicList,
  Trace,
  TraceActor,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
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

export interface ConfigRequest {
  config: string;
  set: string[];
  topic?: string;
}

export const api = {
  runs: () => request<RunRow[]>("/runs"),
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
  launches: () => request<Launch[]>("/launches"),
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
  addTopic: (body: Omit<ConfigRequest, "topic"> & { topic: TopicDoc; replace?: boolean }) =>
    post<{ topic: TopicDoc; replaced: boolean; topics_file: string; override: string; count: number }>("/topics", body),
  region: (config?: string) =>
    request<RegionProbe>(`/region${config ? `?config=${encodeURIComponent(config)}` : ""}`),
};
