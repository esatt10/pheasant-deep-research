import { memo, useMemo, useState } from "react";
import type { LabEvent } from "../types";
import type { StreamState } from "../hooks/useRunStream";
import { FILTERS, summarize, tone } from "./summary";
import { eventMatches, type Focus } from "./focus";

function relative(event: LabEvent, startedAt: number | null): string {
  if (startedAt == null) return "";
  const t = Date.parse(event.occurred_at) / 1000 - startedAt;
  const m = Math.floor(t / 60);
  return `${m}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

const STREAM_LABEL: Record<StreamState, { text: string; className: string }> = {
  connecting: { text: "connecting", className: "pill" },
  live: { text: "SSE", className: "pill pill--accent" },
  reconnecting: { text: "reconnecting", className: "pill pill--warn" },
  locked: { text: "key needed", className: "pill pill--danger" },
};

/**
 * The raw event stream. With something selected it narrows to that actor's
 * events - the same selection the visuals dim around - and says so in its
 * head, where the × clears it.
 */
export const Feed = memo(function Feed({
  events,
  startedAt,
  stream,
  selected,
  onSelect,
  focus,
  focusLabel,
  onClearFocus,
  onCollapse,
}: {
  events: LabEvent[];
  startedAt: number | null;
  stream: StreamState;
  selected: number | null;
  onSelect: (event: LabEvent | null) => void;
  focus: Focus;
  focusLabel: string;
  onClearFocus: () => void;
  onCollapse: () => void;
}) {
  const [filter, setFilter] = useState("all");
  const [onlyFocus, setOnlyFocus] = useState(true);
  const test = FILTERS.find((f) => f.key === filter)?.test ?? (() => true);
  const scoped = useMemo(
    () => (focus && onlyFocus ? events.filter((event) => eventMatches(event, focus)) : events),
    [events, focus, onlyFocus],
  );
  const rows = useMemo(() => scoped.filter(test).slice(-300).reverse(), [scoped, test]);
  const warnings = useMemo(() => scoped.filter(FILTERS[FILTERS.length - 1].test).length, [scoped]);
  const label = STREAM_LABEL[stream];
  return (
    <div className="card feed">
      <div className="card__head">
        Event stream <span className="sub">seq {events.at(-1)?.sequence ?? 0} · raw/events.jsonl</span>
        <div className="r">
          <span className={label.className} title={stream === "reconnecting" ? "The stream dropped; resuming after the last sequence seen" : undefined}>
            {stream === "reconnecting" || stream === "connecting" ? <span className="spinner" /> : <span className={`dot${stream === "live" ? " dot--live" : ""}`} />}
            {label.text}
          </span>
          <button className="btn btn--ghost btn--small" onClick={onCollapse} aria-label="Collapse the event stream" title="Collapse">
            ⇥
          </button>
        </div>
      </div>
      {focus ? (
        <div className="focusbar">
          <button className={`pill${onlyFocus ? " pill--accent" : ""}`} onClick={() => setOnlyFocus((v) => !v)} title="Show only the selection's events">
            {onlyFocus ? "only" : "all, not only"} {focusLabel}
          </button>
          <span className="muted small">{scoped.length} event(s)</span>
          <button className="btn btn--ghost btn--small" style={{ marginLeft: "auto" }} onClick={onClearFocus} aria-label="Clear the selection" title="Clear the selection (Esc)">
            ×
          </button>
        </div>
      ) : null}
      <div className="feed__chips">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`pill${filter === f.key ? " pill--accent" : f.key === "warnings" && warnings ? " pill--warn" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
            {f.key === "warnings" && warnings ? ` ${warnings}` : ""}
          </button>
        ))}
      </div>
      <div className="feed__rows">
        {rows.map((event) => (
          <div
            key={event.sequence}
            className={`ev${tone(event) ? ` ev--${tone(event)}` : ""}${selected === event.sequence ? " ev--sel" : ""}`}
            onClick={() => onSelect(selected === event.sequence ? null : event)}
          >
            <span className="ev__seq">{event.sequence}</span>
            <span className="ev__t">{relative(event, startedAt)}</span>
            <div style={{ minWidth: 0 }}>
              <div>
                <span className="ev__ty">{event.event_type}</span>{" "}
                <span className="ev__ag">
                  · {event.arm_id ?? event.agent_id?.replace(/^agent-/, "").replace(/-[0-9a-f]{8}$/, "") ?? event.status}
                </span>
              </div>
              <div className="ev__sum">{summarize(event)}</div>
            </div>
          </div>
        ))}
        {rows.length === 0 ? (
          <div className="empty">{stream === "connecting" ? <><span className="spinner" /> Connecting…</> : focus && onlyFocus ? "No events for the selection under this filter." : "No events yet."}</div>
        ) : null}
      </div>
    </div>
  );
});
