export function clock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, seconds);
  if (s < 10) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rest = Math.floor(s % 60);
  return m ? `${m}:${String(rest).padStart(2, "0")}` : `${rest}s`;
}

export function usd(value: number | null | undefined, digits = 2): string {
  if (value == null) return "—";
  return `$${value.toFixed(digits)}`;
}

export function ago(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "—";
  const delta = Date.now() / 1000 - epochSeconds;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export const ROLE_COLOR: Record<string, string> = {
  orchestrator: "var(--r-orch)",
  planner: "var(--r-plan)",
  researcher: "var(--r-res)",
  auditor: "var(--r-aud)",
  pheasant: "var(--r-pheasant)",
  indexer: "var(--r-pheasant)",
};

export const armColor = (arm: string) => `var(--arm-${arm}, var(--muted))`;

export const STAGE_LABEL: Record<string, string> = {
  discovered: "discovered",
  acquired: "acquired",
  submitted: "submitted",
  accepted: "accepted",
  awaiting_claim: "awaiting claim",
  claimed: "indexer claimed",
  indexed: "indexed",
  rejected: "rejected",
};

export function stagePill(stage: string): string {
  if (stage === "indexed") return "pill pill--accent";
  if (stage === "awaiting_claim" || stage === "claimed") return "pill pill--warn";
  if (stage === "rejected") return "pill pill--danger";
  return "pill";
}
