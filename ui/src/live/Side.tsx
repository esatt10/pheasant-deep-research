import type { LabEvent, RunModel } from "../types";
import { ROLE_COLOR, STAGE_LABEL, armColor, stagePill, usd } from "../format";
import { summarize } from "./summary";
import { answersDue, type Focus } from "./focus";

/** The swarm as a tree (Option A), until an agent is picked. */
export function SwarmTree({
  model,
  focus,
  onFocus,
  onCollapse,
}: {
  model: RunModel;
  focus: Focus;
  onFocus: (focus: Focus) => void;
  onCollapse: () => void;
}) {
  const selected = focus?.kind === "agent" ? focus.id : null;
  const selectedArm = focus?.kind === "arm" ? focus.id : null;
  const onSelect = (agentId: string | null) =>
    onFocus(agentId && agentId !== selected ? { kind: "agent", id: agentId } : null);
  const researchers = model.agents.filter((a) => a.role === "researcher");
  const searching = researchers.filter((a) => a.status === "searching").length;
  const pre = model.custody.awaiting_claim ?? 0;
  return (
    <div className="card side">
      <div className="card__head">
        Swarm <span className="sub">{researchers.length} branches · {searching} searching</span>
        <div className="r">
          <button className="btn btn--ghost btn--small" onClick={onCollapse} aria-label="Collapse the swarm" title="Collapse">
            ⇤
          </button>
        </div>
      </div>
      <div className="side__scroll" style={{ padding: 6 }}>
        <button className="tnode" onClick={() => onSelect(null)}>
          <span className="sw" style={{ background: ROLE_COLOR.orchestrator }} />
          <span className="name">Orchestrator</span>
          <span className="meta">{model.rounds.length ? `round ${model.rounds.length}` : "planning"}</span>
        </button>
        <button className="tnode ind1" onClick={() => onSelect(null)}>
          <span className="sw" style={{ background: ROLE_COLOR.planner }} />
          <span className="name">Topic planner</span>
          <span className="meta">{model.subtopics.length ? `✓ ${model.subtopics.length} subtopics` : "—"}</span>
        </button>
        {researchers.map((agent) => (
          <button
            key={agent.agent_id}
            className={`tnode ind1${selected === agent.agent_id ? " tnode--sel" : ""}`}
            onClick={() => onSelect(agent.agent_id)}
            title={agent.subtopic_label ?? ""}
          >
            {agent.status === "searching" ? <span className="spinner" /> : <span className="sw" style={{ background: ROLE_COLOR.researcher }} />}
            <span className="name">
              {agent.short_id.split(" ").pop()} · {agent.subtopic_label ?? "—"}
            </span>
            <span className="meta">{agent.acquired} src</span>
          </button>
        ))}
        <button className="tnode ind1" onClick={() => onSelect(null)}>
          <span className="sw" style={{ background: ROLE_COLOR.auditor }} />
          <span className="name">Saturation auditor</span>
          <span className="meta">
            {model.audit.coverage_fraction != null ? `coverage ${Number(model.audit.coverage_fraction).toFixed(2)}` : "idle"}
          </span>
        </button>
        <button className="tnode" onClick={() => onSelect(null)}>
          <span className="sw" style={{ background: ROLE_COLOR.pheasant }} />
          <span className="name">Pheasant region</span>
          <span className="meta">{model.region.server_name ?? "—"}</span>
        </button>
        <button className="tnode ind1" onClick={() => onSelect(null)}>
          <span className="dot" style={{ color: pre ? "var(--warn)" : "var(--muted)" }} />
          <span className="name">Index queue</span>
          <span className="meta" style={pre ? { color: "var(--warn)" } : undefined}>
            {pre
              ? `${pre} awaiting claim`
              : model.custody.claimed
                ? "claimed"
                : model.region.sync.disposition === "completed"
                  ? "in-call"
                  : "—"}
          </span>
        </button>
      </div>
      <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)" }}>
        <div className="eyebrow" style={{ marginBottom: 6 }}>
          Arms {model.questions_total ? `· ${model.questions_total} questions` : "(after freeze)"}
        </div>
        <div className="armbars">
          {model.run.arms_configured.map((arm) => {
            const row = model.arms.find((a) => a.arm_id === arm);
            // `answered` counts every answer event, abstentions included.
            const done = row ? row.answered : 0;
            const total = answersDue(model);
            const on = selectedArm === arm;
            return (
              <button
                key={arm}
                className={`armbar${on ? " armbar--sel" : ""}`}
                title={`${row?.label ?? arm}${row ? ` · ${row.answered} answers, ${row.abstained} of them abstentions` : " · not started"} — click to filter to this arm`}
                onClick={() => onFocus(on ? null : { kind: "arm", id: arm })}
                aria-pressed={on}
              >
                <span className="armbar__id" style={{ color: armColor(arm) }}>{arm}</span>
                <span className="bar" style={{ flex: 1 }}>
                  <i style={{ width: total ? `${Math.min(100, (done / total) * 100)}%` : "0%", background: armColor(arm) }} />
                </span>
                <span className="muted mono armbar__n">{total ? `${done}/${total}` : row ? done : "—"}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** One agent, in full (Option B's inspector). */
export function Inspector({
  model,
  agentId,
  events,
  onClose,
}: {
  model: RunModel;
  agentId: string;
  events: LabEvent[];
  onClose: () => void;
}) {
  const agent = model.agents.find((a) => a.agent_id === agentId);
  if (!agent) return null;
  const subtopic = model.subtopics.find((s) => s.subtopic_id === agent.subtopic_id);
  const facet = model.facets.find((f) => subtopic?.facet_ids.includes(f.facet_id));
  const sources = model.sources.filter((s) => s.agent_id === agentId);
  const recent = events.filter((e) => e.agent_id === agentId).slice(-6).reverse();
  return (
    <div className="card side insp">
      <div className="card__head">
        <span className="dot" style={{ color: ROLE_COLOR.researcher }} />
        {agent.short_id}
        <div className="r">
          <span className={agent.status === "searching" ? "pill pill--info" : "pill"}>{agent.status}</span>
          <button className="btn btn--ghost btn--small" onClick={onClose} aria-label="Back to the swarm" title="Back to the swarm (Esc)">
            ×
          </button>
        </div>
      </div>
      <div className="side__scroll">
        <section>
          <div className="eyebrow">Subtopic question</div>
          <div style={{ fontSize: 12.5, marginTop: 3 }}>{subtopic?.question ?? agent.subtopic_label ?? "—"}</div>
          <dl className="kv" style={{ marginTop: 8 }}>
            <dt>Facet</dt>
            <dd>{facet ? `${facet.label} · weight ${facet.weight}` : "—"}</dd>
            <dt>Minimums</dt>
            <dd style={facet && !facet.meets_minimum ? { color: "var(--warn)" } : undefined}>
              {facet ? (facet.meets_minimum ? "all met" : facet.unmet.join(" · ")) : "not audited yet"}
            </dd>
            <dt>Work</dt>
            <dd>
              {agent.searches} searches · {agent.discovered} found · {agent.acquired} acquired · {agent.submitted} submits
            </dd>
            <dt>Spend</dt>
            <dd className="mono">{usd(agent.cost_usd, 4)}</dd>
            {agent.last_query ? (
              <>
                <dt>Last query</dt>
                <dd className="soft" title={agent.last_query}>“{agent.last_query.slice(0, 90)}{agent.last_query.length > 90 ? "…" : ""}”</dd>
              </>
            ) : null}
          </dl>
        </section>
        <section>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Sources · chain of custody</div>
          {sources.length === 0 ? <span className="muted small">none yet</span> : null}
          {sources.map((source) => (
            <div key={source.source_id} className="srow" title={source.identifier ?? ""}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{source.title ?? source.source_id}</span>
              <span className={stagePill(source.stage)}>{STAGE_LABEL[source.stage]}</span>
            </div>
          ))}
        </section>
        <section>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Latest events</div>
          {recent.map((event) => (
            <div key={event.sequence} className="small" style={{ padding: "3px 0" }}>
              <span className="mono muted">{event.sequence}</span> <span className="mono">{event.event_type}</span>
              <div className="soft">{summarize(event)}</div>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
