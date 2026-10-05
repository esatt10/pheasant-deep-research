import { useState } from "react";
import type { LabEvent } from "../types";
import { FILTERS, summarize, tone } from "./summary";

function relative(event: LabEvent, startedAt: number | null): string {
  if (startedAt == null) return "";
  const t = Date.parse(event.occurred_at) / 1000 - startedAt;
  const m = Math.floor(t / 60);
  return `${m}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

export function Feed({
  events,
  startedAt,
  connected,
  selected,
  onSelect,
}: {
  events: LabEvent[];
  startedAt: number | null;
  connected: boolean;
  selected: number | null;
  onSelect: (event: LabEvent) => void;
}) {
  const [filter, setFilter] = useState("all");
  const test = FILTERS.find((f) => f.key === filter)?.test ?? (() => true);
  const rows = events.filter(test).slice(-300).reverse();
  const warnings = events.filter(FILTERS[FILTERS.length - 1].test).length;
  return (
    <div className="card feed">
      <div className="card__head">
        Event stream <span className="sub">seq {events.at(-1)?.sequence ?? 0} · raw/events.jsonl</span>
        <div className="r">
          <span className={connected ? "pill pill--accent" : "pill"}>
            <span className={`dot${connected ? " dot--live" : ""}`} />
            {connected ? "SSE" : "offline"}
          </span>
        </div>
      </div>
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
            onClick={() => onSelect(event)}
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
        {rows.length === 0 ? <div className="empty">No events yet.</div> : null}
      </div>
    </div>
  );
}
