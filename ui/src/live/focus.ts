import type { LabEvent, RunModel, Source } from "../types";

/**
 * What the person has selected on the live page: one research branch or one
 * arm. Every panel reads the same value, so selecting in one place filters
 * the others - the feed to that actor's events, the visuals dimmed around
 * it, custody and facets narrowed to its sources.
 */
export type Focus = { kind: "agent"; id: string } | { kind: "arm"; id: string } | null;

export const sameFocus = (a: Focus, b: Focus) => !!a && !!b && a.kind === b.kind && a.id === b.id;

export function eventMatches(event: LabEvent, focus: Focus): boolean {
  if (!focus) return true;
  return focus.kind === "agent" ? event.agent_id === focus.id : event.arm_id === focus.id;
}

export function laneOf(focus: Focus): string | null {
  if (!focus) return null;
  return focus.kind === "agent" ? `agent:${focus.id}` : `arm:${focus.id}`;
}

export function focusFromLane(laneId: string): Focus {
  if (laneId.startsWith("agent:")) return { kind: "agent", id: laneId.slice(6) };
  if (laneId.startsWith("arm:")) return { kind: "arm", id: laneId.slice(4) };
  return null;
}

export function focusLabel(focus: Focus, model: RunModel): string {
  if (!focus) return "";
  if (focus.kind === "arm") return `arm ${focus.id}`;
  const agent = model.agents.find((a) => a.agent_id === focus.id);
  return agent ? `${agent.short_id.split(" ").pop()} · ${agent.subtopic_label ?? ""}` : focus.id;
}

/** The custody funnel over one branch's sources, in the model's own shape. */
export function custodyFor(sources: Source[]): Record<string, number> {
  const counts: Record<string, number> = {};
  const order = ["discovered", "acquired", "submitted", "accepted", "awaiting_claim", "claimed", "indexed"];
  for (const source of sources) {
    if (source.stage === "rejected") {
      counts.rejected = (counts.rejected ?? 0) + 1;
      continue;
    }
    // A source at a later stage has passed every earlier one.
    const reached = order.indexOf(source.stage);
    for (let index = 0; index <= reached; index += 1) counts[order[index]] = (counts[order[index]] ?? 0) + 1;
  }
  return counts;
}

/**
 * How far the run has got, from what it has recorded - a progress bar, never
 * a number a report states. Phases count whole; the active one counts the
 * fraction its own evidence supports (evaluation: answers recorded of answers
 * due; collection: rounds closed of the configured maximum).
 */
export function runProgress(model: RunModel): { fraction: number; label: string } {
  const phases = model.run.phases;
  if (!phases.length) return { fraction: 0, label: "" };
  const done = phases.filter((p) => p.state === "done").length;
  const active = phases.find((p) => p.state === "active");
  let partial = 0;
  let label = active ? active.label : done === phases.length ? "finished" : "";
  if (active?.key === "evaluate" && model.questions_total) {
    const due = answersDue(model) * Math.max(1, model.run.arms_configured.length);
    // `answered` counts every answer event, abstentions included.
    const answered = model.arms.reduce((sum, arm) => sum + arm.answered, 0);
    partial = Math.min(1, answered / due);
    label = `${active.label} · ${answered}/${due} answers`;
  } else if (active?.key === "collect") {
    const indexed = model.custody.indexed ?? 0;
    const discovered = model.custody.discovered ?? 0;
    partial = discovered ? Math.min(0.95, indexed / discovered) : 0.05;
    label = `${active.label} · round ${model.rounds.length + 1} · ${indexed}/${discovered} indexed`;
  }
  return { fraction: Math.min(1, (done + partial) / phases.length), label };
}

/** Answers one arm owes: every frozen question, once per repetition. */
export function answersDue(model: RunModel): number {
  return (model.questions_total ?? 0) * Math.max(1, model.run.repetitions ?? 1);
}
