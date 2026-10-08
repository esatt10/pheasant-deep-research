import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { McpCall, Trace, TraceActor, TraceAnswer, TraceEvent, TraceSpan } from "../types";
import { ROLE_COLOR, armColor, usd } from "../format";
import { ZoomControls, useDrag, useTimeZoom, useWheel } from "../components/zoom";

/**
 * Every agent's whole trace, from the run's own raw files (console/traces.py).
 *
 * Left: the actors — the orchestration, each research branch, each arm.
 * Right: that actor's span tree as a waterfall on the run's clock, every event
 * inside the span that recorded it, and for each MCP call the request and the
 * response exactly as the region answered. An arm's answer span also carries
 * the question, the answer, its claims and what it read. Nothing here is a
 * number a report states; it is the record the reports were computed from.
 */

const LABEL_W = 340;

function actorColor(actor: TraceActor | undefined): string {
  if (!actor) return "var(--muted)";
  if (actor.kind === "arm") return armColor(actor.actor.split(":")[1]);
  if (actor.kind === "agent") return ROLE_COLOR[actor.role ?? "researcher"] ?? "var(--r-res)";
  return "var(--r-orch)";
}

function seconds(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1) return `${(value * 1000).toFixed(value < 0.01 ? 1 : 0)} ms`;
  return `${value.toFixed(2)} s`;
}

function scalar(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value.length > 90 ? `${value.slice(0, 89)}…` : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

/** One line a person can read: the fields that say what happened, first. */
function summarize(event: TraceEvent): string {
  const p = event.payload ?? {};
  const pick = (...keys: string[]) => keys.map((k) => [k, scalar(p[k])] as const).filter(([, v]) => v != null);
  const preferred = pick(
    "tool",
    "query",
    "provider",
    "title",
    "model",
    "disposition",
    "state",
    "input_tokens",
    "output_tokens",
    "duration_ms",
    "count",
    "results",
  );
  const rows = preferred.length ? preferred : Object.keys(p).slice(0, 4).map((k) => [k, scalar(p[k])] as const).filter(([, v]) => v != null);
  return rows
    .slice(0, 5)
    .map(([k, v]) => (k === "duration_ms" ? `${Number(v).toFixed(1)} ms` : k === "tool" || k === "query" || k === "title" ? v : `${k} ${v}`))
    .join(" · ");
}

function eventTone(event: TraceEvent): string {
  if (event.status === "failed" || event.event_type.endsWith("failed")) return "danger";
  if (event.event_type.startsWith("mcp.")) return "pheasant";
  if (event.event_type.startsWith("cost.")) return "model";
  return "plain";
}

function flatten(spans: TraceSpan[], open: Set<string>, depth = 0, out: { span: TraceSpan; depth: number }[] = []) {
  for (const span of spans) {
    out.push({ span, depth });
    if (open.has(span.span_id)) flatten(span.children, open, depth + 1, out);
  }
  return out;
}

function allSpans(spans: TraceSpan[], out: TraceSpan[] = []): TraceSpan[] {
  for (const span of spans) {
    out.push(span);
    allSpans(span.children, out);
  }
  return out;
}

export function Traces({ runId, actorId }: { runId: string; actorId?: string }) {
  const navigate = useNavigate();
  const [actors, setActors] = useState<TraceActor[]>([]);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setActors([]);
    void api
      .traces(runId)
      .then((r) => setActors(r.actors))
      .catch((e: Error) => setError(e.message));
  }, [runId]);

  const actor = actorId ?? actors[0]?.actor;
  useEffect(() => {
    if (!actorId && actors[0]) navigate(`/reports/${runId}/traces/${encodeURIComponent(actors[0].actor)}`, { replace: true });
  }, [actorId, actors, runId, navigate]);

  useEffect(() => {
    if (!actor) return;
    setTrace(null);
    void api
      .trace(runId, actor)
      .then((t) => {
        setTrace(t);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [runId, actor]);

  const current = actors.find((a) => a.actor === actor);
  const groups: [string, TraceActor[]][] = [
    ["Orchestration", actors.filter((a) => a.kind === "orchestration")],
    ["Research branches", actors.filter((a) => a.kind === "agent")],
    ["Evaluation arms", actors.filter((a) => a.kind === "arm")],
  ];

  return (
    <div className="traces">
      <div className="card traces__actors">
        <div className="card__head">Agents <span className="sub">{actors.length} actors</span></div>
        <div className="side__scroll" style={{ padding: 6 }}>
          {groups.map(([title, rows]) =>
            rows.length ? (
              <div key={title} style={{ marginBottom: 6 }}>
                <div className="eyebrow" style={{ padding: "6px 8px 3px" }}>{title}</div>
                {rows.map((row) => (
                  <button
                    key={row.actor}
                    className={`tnode${row.actor === actor ? " tnode--sel" : ""}`}
                    onClick={() => navigate(`/reports/${runId}/traces/${encodeURIComponent(row.actor)}`)}
                    title={row.actor}
                  >
                    <span className="sw" style={{ background: actorColor(row) }} />
                    <span className="name">{row.kind === "orchestration" ? "Orchestration" : row.label}</span>
                    <span className="meta">
                      {row.failures ? <span style={{ color: "var(--danger)" }}>{row.failures}✕ </span> : null}
                      {row.events} ev · {row.mcp_calls} mcp
                    </span>
                  </button>
                ))}
              </div>
            ) : null,
          )}
        </div>
      </div>

      <div className="traces__main fit-scroll">
        {error ? <div className="pill pill--danger" style={{ whiteSpace: "normal" }}>{error}</div> : null}
        <div className="card">
          <div className="card__head">
            <span className="sw" style={{ width: 10, height: 10, borderRadius: 3, background: actorColor(current) }} />
            {trace?.label ?? current?.label ?? "…"}
          </div>
          {current ? (
            <div className="card__body traces__stats">
              <Stat label="spans" value={current.spans} />
              <Stat label="events" value={current.events} />
              <Stat label="MCP calls" value={current.mcp_calls} />
              <Stat label="model calls" value={current.model_calls} />
              <Stat label="tokens" value={current.tokens.toLocaleString()} />
              <Stat label="spend" value={usd(current.cost_usd, 4)} />
              <Stat label="failed events" value={current.failures} tone={current.failures ? "danger" : undefined} />
              <Stat label="window" value={trace ? seconds(trace.window.end - trace.window.start) : "—"} />
            </div>
          ) : null}
        </div>
        {trace ? (
          <>
            <Waterfall trace={trace} runId={runId} filter={filter} onFilter={setFilter} color={actorColor(current)} />
            {trace.errors.length ? (
              <div className="card">
                <div className="card__head" style={{ color: "var(--danger)" }}>Errors <span className="sub">{trace.errors.length} · errors.jsonl</span></div>
                <div className="card__body">
                  {trace.errors.map((row, index) => (
                    <pre key={index} className="yaml" style={{ marginBottom: 6 }}>{JSON.stringify(row, null, 2)}</pre>
                  ))}
                </div>
              </div>
            ) : null}
            {trace.claims?.length ? <Claims trace={trace} /> : null}
            <div className="muted small">
              The lab records each model call's role, model, tokens and spend, and each prompt template's digest in the run manifest — not
              the prompt text or the completion. MCP requests and responses are recorded whole, redacted.
            </div>
            {trace.unjoined_mcp_calls.length ? (
              <div className="card">
                <div className="card__head">MCP calls with no recorded event <span className="sub">listed under the arm the log names</span></div>
                <div className="card__body">
                  {trace.unjoined_mcp_calls.map((call) => (
                    <McpRow key={call.mcp_call} runId={runId} index={call.mcp_call} label={`${call.tool} · ${call.status} · ${call.duration_ms?.toFixed(1)} ms`} />
                  ))}
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <div className="card empty"><span className="spinner" /> reading the trace…</div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="tstat">
      <span className="eyebrow">{label}</span>
      <b style={tone ? { color: `var(--${tone})` } : undefined}>{value}</b>
    </div>
  );
}

function Waterfall({
  trace,
  runId,
  filter,
  onFilter,
  color,
}: {
  trace: Trace;
  runId: string;
  filter: string;
  onFilter: (value: string) => void;
  color: string;
}) {
  const roots = trace.spans;
  const everySpan = useMemo(() => allSpans(roots), [roots]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(roots.length <= 3 ? roots.map((s) => s.span_id) : []));
  const [details, setDetails] = useState<Set<string>>(new Set());
  const [looseOpen, setLooseOpen] = useState(false);
  useEffect(() => {
    setOpen(new Set(roots.length <= 3 ? roots.map((s) => s.span_id) : []));
    setDetails(new Set());
    setLooseOpen(false);
  }, [roots]);

  const origin = trace.window.start;
  const full = Math.max(0.001, trace.window.end - origin);
  const zoom = useTimeZoom(full, { minSpan: full / 400 });
  const [t0, t1] = zoom.range;
  const span = t1 - t0;
  const track = useRef<HTMLDivElement>(null);
  const [trackW, setTrackW] = useState(600);
  useEffect(() => {
    const node = track.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setTrackW(Math.max(100, node.clientWidth - LABEL_W - 16)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const pct = (t: number | null) => (t == null ? 0 : ((t - origin - t0) / span) * 100);
  const drag = useDrag((dx) => zoom.panBy((-dx / trackW) * span), track);
  useWheel(track, (event) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    const rect = track.current!.getBoundingClientRect();
    const fraction = (event.clientX - rect.left - LABEL_W) / trackW;
    zoom.zoomAt(Math.exp(-event.deltaY * 0.004), t0 + Math.min(1, Math.max(0, fraction)) * span);
  });

  const needle = filter.trim().toLowerCase();
  const matches = (event: TraceEvent) =>
    !needle || event.event_type.toLowerCase().includes(needle) || summarize(event).toLowerCase().includes(needle);
  const rows = flatten(roots, open);
  const toggle = (set: Set<string>, id: string, apply: (s: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    apply(next);
  };
  const step = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300].find((s) => s >= span / 6) ?? 600;
  const axis: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-9; t += step) axis.push(t);
  const answers = new Map((trace.answers ?? []).map((a) => [a.question_id, a]));

  return (
    <div className="card">
      <div className="card__head">
        Trace
        <span className="sub">{everySpan.length} spans · {trace.events.length ? `${trace.events.length} events outside a span · ` : ""}times from run start</span>
        <div className="r">
          <input className="input" style={{ width: 200 }} placeholder="filter events…" value={filter} onChange={(e) => onFilter(e.target.value)} />
          <button className="btn btn--small" onClick={() => setOpen(new Set(everySpan.map((s) => s.span_id)))}>Expand all</button>
          <button className="btn btn--small" onClick={() => { setOpen(new Set()); setDetails(new Set()); }}>Collapse</button>
          <ZoomControls
            inline
            onIn={() => zoom.zoomAt(1.6)}
            onOut={() => zoom.zoomAt(1 / 1.6)}
            onFit={zoom.fit}
            fitted={zoom.isFit}
            hint="or Ctrl + scroll over the timeline; drag it to pan"
          />
        </div>
      </div>
      <div
        ref={track}
        className="wf"
        onPointerDown={drag.onPointerDown}
        onPointerMove={drag.onPointerMove}
        onPointerUp={drag.onPointerUp}
        onPointerCancel={drag.onPointerCancel}
        style={{ cursor: zoom.isFit ? undefined : "grab" }}
      >
        <div className="wf__axis" style={{ gridTemplateColumns: `${LABEL_W}px 1fr` }}>
          <span className="eyebrow">span</span>
          <div className="wf__track" data-window={`${t0.toFixed(4)}-${t1.toFixed(4)}`}>
            {axis.map((t) => (
              <span key={t} className="wf__tick" style={{ left: `${((t - t0) / span) * 100}%` }}>
                {seconds(t + origin)}
              </span>
            ))}
          </div>
        </div>
        {rows.map(({ span: node, depth }) => {
          const visibleEvents = node.events.filter(matches);
          const expanded = details.has(node.span_id);
          const answer = node.attributes.question_id ? answers.get(node.attributes.question_id) : undefined;
          const failed = node.status && node.status !== "ok";
          return (
            <div key={node.span_id} className={`wf__row${expanded ? " wf__row--open" : ""}`}>
              <div className="wf__line" style={{ gridTemplateColumns: `${LABEL_W}px 1fr` }} onClick={() => toggle(details, node.span_id, setDetails)}>
                <div className="wf__label" style={{ paddingLeft: 8 + depth * 16 }}>
                  {node.children.length ? (
                    <button
                      className="wf__caret"
                      aria-label={open.has(node.span_id) ? "Collapse children" : "Expand children"}
                      data-nodrag
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle(open, node.span_id, setOpen);
                      }}
                    >
                      {open.has(node.span_id) ? "▾" : "▸"}
                    </button>
                  ) : (
                    <span className="wf__caret" />
                  )}
                  <span className="mono" style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{node.name}</span>
                  <span className="muted small wf__attr">
                    {node.attributes.question_id ?? node.attributes.subtopic_id ?? node.attributes.stage ?? ""}
                  </span>
                  {failed ? <span className="pill pill--danger">{node.status}</span> : null}
                  <span className="muted small" style={{ marginLeft: "auto" }}>{node.duration_ms != null ? `${node.duration_ms.toFixed(1)} ms` : ""}</span>
                </div>
                <div className="wf__track">
                  <span
                    className="wf__bar"
                    style={{
                      left: `${pct(node.start)}%`,
                      width: `max(3px, ${pct(node.end ?? node.start) - pct(node.start)}%)`,
                      background: failed ? "var(--danger)" : color,
                    }}
                  />
                  {node.events.map((event) =>
                    event.t != null ? (
                      <span
                        key={event.event_id}
                        className={`wf__ev wf__ev--${eventTone(event)}${matches(event) ? "" : " wf__ev--dim"}`}
                        style={{ left: `${pct(event.t)}%` }}
                        title={`${event.event_type} · ${seconds(event.t)}\n${summarize(event)}`}
                      />
                    ) : null,
                  )}
                </div>
              </div>
              {expanded ? (
                <div className="wf__detail" data-nodrag>
                  <dl className="kv">
                    <dt>span</dt><dd className="mono">{node.span_id}{node.parent_span_id ? ` ← ${node.parent_span_id}` : ""}</dd>
                    <dt>window</dt><dd>{seconds(node.start)} → {seconds(node.end)}</dd>
                    {Object.entries(node.attributes).map(([k, v]) => (
                      <FragmentKV key={k} k={k} v={String(v)} />
                    ))}
                    {node.error ? (<><dt>error</dt><dd style={{ color: "var(--danger)" }}>{node.error}</dd></>) : null}
                  </dl>
                  {answer ? <AnswerBlock answer={answer} /> : null}
                  <EventList events={visibleEvents} total={node.events.length} runId={runId} />
                </div>
              ) : null}
            </div>
          );
        })}
        {trace.events.length ? (
          <div className={`wf__row${looseOpen ? " wf__row--open" : ""}`}>
            <div className="wf__line" style={{ gridTemplateColumns: `${LABEL_W}px 1fr` }} onClick={() => setLooseOpen(!looseOpen)}>
              <div className="wf__label" style={{ paddingLeft: 8 }}>
                <span className="wf__caret">{looseOpen ? "▾" : "▸"}</span>
                <span style={{ fontWeight: 600, whiteSpace: "nowrap" }}>outside any span</span>
                <span className="muted small wf__attr">{trace.events.length} events the tracer recorded without an exported span</span>
              </div>
              <div className="wf__track">
                {trace.events.map((event) =>
                  event.t != null ? (
                    <span
                      key={event.event_id}
                      className={`wf__ev wf__ev--${eventTone(event)}${matches(event) ? "" : " wf__ev--dim"}`}
                      style={{ left: `${pct(event.t)}%` }}
                      title={`${event.event_type} · ${seconds(event.t)}\n${summarize(event)}`}
                    />
                  ) : null,
                )}
              </div>
            </div>
            {looseOpen ? (
              <div className="wf__detail" data-nodrag>
                <EventList events={trace.events.filter(matches)} total={trace.events.length} runId={runId} />
              </div>
            ) : null}
          </div>
        ) : null}
        {!rows.length && !trace.events.length ? <div className="empty">This actor recorded nothing.</div> : null}
      </div>
    </div>
  );
}

function FragmentKV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>{k}</dt>
      <dd className="mono">{v}</dd>
    </>
  );
}

function EventList({ events, total, runId }: { events: TraceEvent[]; total: number; runId: string }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  if (!total) return <div className="muted small" style={{ marginTop: 8 }}>No events in this span.</div>;
  return (
    <div className="tev-list">
      <div className="eyebrow" style={{ margin: "10px 0 4px" }}>
        events {events.length !== total ? `· ${events.length} of ${total} match the filter` : `· ${total}`}
      </div>
      {events.map((event) => {
        const isOpen = open.has(event.event_id);
        return (
          <div key={event.event_id} className={`tev tev--${eventTone(event)}`}>
            <button
              className="tev__line"
              onClick={() => {
                const next = new Set(open);
                if (isOpen) next.delete(event.event_id);
                else next.add(event.event_id);
                setOpen(next);
              }}
            >
              <span className="mono muted">#{event.sequence}</span>
              <span className="mono muted">{seconds(event.t)}</span>
              <span className="mono tev__ty">{event.event_type}</span>
              <span className="tev__sum">{summarize(event)}</span>
              {event.placed ? (
                <span className="pill" title="This event's own span was never exported; it is placed in the span of its actor and question open at the time.">
                  placed {event.placed.replace("_", " ")}
                </span>
              ) : null}
              {event.status && event.status !== "ok" && event.status !== "succeeded" ? (
                <span className={`pill ${event.status === "failed" ? "pill--danger" : ""}`}>{event.status}</span>
              ) : null}
              {event.mcp_call != null ? <span className="pill pill--info">MCP</span> : null}
            </button>
            {isOpen ? (
              <div className="tev__body">
                <div className="eyebrow">payload</div>
                <pre className="yaml">{JSON.stringify(event.payload, null, 2)}</pre>
                {event.mcp_call != null ? <McpBody runId={runId} index={event.mcp_call} /> : null}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function McpRow({ runId, index, label }: { runId: string; index: number; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="tev tev--pheasant">
      <button className="tev__line" onClick={() => setOpen(!open)}>
        <span className="pill pill--info">MCP</span>
        <span className="tev__sum">{label}</span>
      </button>
      {open ? <div className="tev__body"><McpBody runId={runId} index={index} /></div> : null}
    </div>
  );
}

/**
 * MCP returns a tool's answer as JSON *inside* a text block. Shown verbatim
 * that is one escaped line; decoded it is the answer. The view decodes for
 * reading and says so — the stored record is untouched.
 */
function decodeContent(response: unknown): { value: unknown; decoded: number } {
  let decoded = 0;
  const walk = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(walk);
    if (node && typeof node === "object") {
      const out: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
        if (key === "text" && typeof value === "string" && /^\s*[[{]/.test(value)) {
          try {
            out["text (decoded JSON)"] = JSON.parse(value);
            decoded += 1;
            continue;
          } catch {
            /* not JSON after all */
          }
        }
        out[key] = walk(value);
      }
      return out;
    }
    return node;
  };
  return { value: walk(response), decoded };
}

function McpBody({ runId, index }: { runId: string; index: number }) {
  const [call, setCall] = useState<McpCall | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState(false);
  useEffect(() => {
    void api.mcpCall(runId, index).then(setCall).catch((e: Error) => setError(e.message));
  }, [runId, index]);
  if (error) return <div className="pill pill--danger">{error}</div>;
  if (!call) return <span className="spinner" />;
  const response = raw ? { value: call.response, decoded: 0 } : decodeContent(call.response);
  return (
    <div className="mcp-pair">
      <div>
        <div className="eyebrow">request · {call.tool} · attempt {call.attempt}</div>
        <pre className="yaml">{JSON.stringify(call.request, null, 2)}</pre>
      </div>
      <div>
        <div className="eyebrow" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          response · {call.status} · {call.duration_ms?.toFixed(1)} ms
          <button className="btn btn--ghost btn--small" style={{ marginLeft: "auto", padding: "0 4px" }} onClick={() => setRaw(!raw)}>
            {raw ? "decode text blocks" : response.decoded ? `as recorded (${response.decoded} text block decoded)` : "as recorded"}
          </button>
        </div>
        <pre className="yaml">{JSON.stringify(response.value, null, 2)}</pre>
      </div>
    </div>
  );
}

function AnswerBlock({ answer }: { answer: TraceAnswer }) {
  return (
    <div className="tanswer">
      <div className="eyebrow">question · {answer.question_type ?? ""} {answer.cohorts?.length ? `· ${answer.cohorts.join(", ")}` : ""}</div>
      <div style={{ fontWeight: 600, margin: "2px 0 8px" }}>{answer.question ?? answer.question_id}</div>
      <div className="eyebrow">
        answer {answer.abstained ? <span className="pill pill--warn">abstained{answer.abstention_reason ? ` · ${answer.abstention_reason}` : ""}</span> : null}
        {answer.error ? <span className="pill pill--danger">{answer.error}</span> : null}
      </div>
      <div className="tanswer__text">{answer.answer_text || <span className="muted">no answer text</span>}</div>
      {answer.claims.length ? (
        <>
          <div className="eyebrow" style={{ marginTop: 8 }}>claims · {answer.claims.length}</div>
          <ol className="tanswer__claims">
            {answer.claims.map((claim, index) => (
              <li key={index}>
                {claim.text ?? claim.claim_text}
                {claim.citations?.length ? <div className="mono muted small">{claim.citations.join(" · ")}</div> : null}
              </li>
            ))}
          </ol>
        </>
      ) : null}
      <dl className="kv" style={{ marginTop: 8 }}>
        <dt>queries</dt><dd>{answer.queries_used.length ? answer.queries_used.map((q, i) => <div key={i} className="mono">{q}</div>) : "—"}</dd>
        <dt>read</dt><dd>{answer.read_passages.length ? answer.read_passages.map((p, i) => <div key={i} className="mono small">{p.artifact_id ?? p.source_id}{p.text_source ? ` · ${p.text_source}` : ""}</div>) : "—"}</dd>
        <dt>model</dt><dd className="mono">{answer.model ?? "—"}</dd>
        <dt>latency</dt><dd>{answer.latency_ms != null ? `${answer.latency_ms.toFixed(1)} ms` : "—"} · {usd(answer.cost_usd, 4)}</dd>
        {answer.snapshot_id ? (<><dt>snapshot</dt><dd className="mono">{answer.snapshot_id}</dd></>) : null}
      </dl>
    </div>
  );
}

function Claims({ trace }: { trace: Trace }) {
  return (
    <div className="card">
      <div className="card__head">Claims extracted <span className="sub">{trace.claims!.length} · claims.jsonl</span></div>
      <div style={{ maxHeight: 420, overflow: "auto" }}>
        <table className="table">
          <thead>
            <tr><th>claim</th><th>type</th><th>facets</th><th>source</th><th>round</th></tr>
          </thead>
          <tbody>
            {trace.claims!.map((claim) => (
              <tr key={claim.claim_id}>
                <td>{claim.claim_text}</td>
                <td><span className="pill">{claim.claim_type ?? "—"}</span>{claim.support && claim.support !== "supports" ? <span className="pill pill--warn">{claim.support}</span> : null}</td>
                <td className="mono small">{(claim.facet_ids ?? []).join(", ")}</td>
                <td className="mono small">{claim.source_id}{claim.locator ? ` · ${claim.locator}` : ""}</td>
                <td>{claim.round ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
