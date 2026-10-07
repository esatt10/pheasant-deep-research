import { useLayoutEffect, useRef, useState } from "react";
import type { Bar, Lane, Tick } from "../types";
import { ROLE_COLOR, armColor, clock } from "../format";
import { ZoomControls, useDrag, useTimeZoom, useWheel } from "../components/zoom";

/**
 * One lane per actor, one bar per closed span, one tick per event (Option A).
 * The Indexer lane is drawn from the region's own words: a hatched bar is a
 * sync Pheasant *queued* and no indexer had claimed yet, so a wait shows up
 * as the pre-claim interval it is rather than as an idle gap.
 */

const LABEL_W = 168;
const AXIS_H = 22;

function laneColor(lane: Lane): string {
  if (lane.kind === "arm") return armColor(lane.label);
  return ROLE_COLOR[lane.kind] ?? "var(--muted)";
}

function niceStep(span: number, target: number): number {
  const raw = span / Math.max(1, target);
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  return steps.find((s) => s >= raw) ?? 3600;
}

/** Zoomed in, whole seconds repeat; show as many decimals as the step needs. */
function axisLabel(t: number, step: number): string {
  if (step < 0.1) return `${t.toFixed(2)}s`;
  if (step < 1) return `${t.toFixed(1)}s`;
  return clock(t);
}

function tickColor(tick: Tick, lane: Lane): string {
  if (tick.kind === "tool_failed" || tick.kind.endsWith("timed_out")) return "var(--danger)";
  if (tick.kind === "sync_queued" || tick.kind === "barrier_waiting") return "var(--warn)";
  if (tick.kind === "barrier_crossed") return "var(--accent)";
  if (lane.kind === "pheasant" && tick.arm) return armColor(String(tick.arm));
  return laneColor(lane);
}

export function Swimlanes({
  lanes,
  horizon,
  now,
  live,
  rowHeight = 30,
  marker,
  selectedLane,
  onSelectLane,
}: {
  lanes: Lane[];
  horizon: number;
  now: number;
  live: boolean;
  rowHeight?: number;
  marker?: number | null;
  selectedLane?: string | null;
  onSelectLane?: (laneId: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useLayoutEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.observe(node);
    setWidth(node.clientWidth);
    return () => observer.disconnect();
  }, []);

  // Leave headroom to the right of "now" while live, so the newest work is
  // never drawn against the edge.
  const full = Math.max(1, live ? Math.max(horizon, now) * 1.08 : horizon * 1.02);
  const plot = Math.max(100, width - LABEL_W - 12);
  const zoom = useTimeZoom(full);
  const [t0, t1] = zoom.range;
  const span = t1 - t0;
  // Off-window positions are clamped just outside the plot, which the clip
  // path then hides — a bar that starts before the window still draws from
  // its left edge.
  const x = (t: number) => LABEL_W + (Math.min(Math.max(t - t0, -span * 0.01), span * 1.01) / span) * plot;
  const at = (clientX: number) => {
    const left = host.current?.getBoundingClientRect().left ?? 0;
    return t0 + ((clientX - left - LABEL_W) / plot) * span;
  };
  const drag = useDrag((dx) => zoom.panBy((-dx / plot) * span), host);
  useWheel(host, (event) => {
    // Ctrl / ⌘ + wheel (and a pinch, which browsers report the same way)
    // zooms time; a sideways scroll pans it; a plain wheel scrolls the lanes.
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      zoom.zoomAt(Math.exp(-event.deltaY * 0.004), at(event.clientX));
    } else if (Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.shiftKey) {
      if (zoom.isFit) return;
      event.preventDefault();
      zoom.panBy(((event.shiftKey ? event.deltaY : event.deltaX) / plot) * span);
    }
  });
  const height = AXIS_H + lanes.length * rowHeight + 4;
  const step = niceStep(span, Math.floor(plot / 70));
  const axis: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-9; t += step) axis.push(t);
  const visible = (t: number) => t >= t0 - span * 0.01 && t <= t1 + span * 0.01;
  const barH = Math.min(18, rowHeight - 10);

  const drawBar = (bar: Bar, lane: Lane, row: number, key: number) => {
    const end = bar.end ?? now;
    const x0 = x(bar.start);
    const w = Math.max(3, x(end) - x0);
    const y = AXIS_H + row * rowHeight + (rowHeight - barH) / 2;
    const pre = bar.status === "pre_claim";
    const color = laneColor(lane);
    const light = lane.kind === "researcher" && (bar.label === "search" || bar.label === "branch");
    const label = bar.label === "answer" ? "" : bar.label;
    return (
      <g key={key}>
        <title>
          {`${lane.label}: ${bar.label} · ${clock(bar.start)} → ${bar.end == null ? "now" : clock(bar.end)}`}
        </title>
        <rect
          x={x0}
          y={y}
          width={w}
          height={barH}
          rx={4}
          fill={pre ? "url(#lane-hatch)" : color}
          opacity={pre ? 1 : light ? 0.42 : bar.status === "error" ? 0.6 : 0.9}
          stroke={pre ? "var(--warn)" : bar.status === "error" ? "var(--danger)" : "none"}
        />
        {label && w > label.length * 5.6 + 8 ? (
          <text x={x0 + 5} y={y + barH / 2 + 3.5} fontSize={10.5} fontWeight={600} fill={pre ? "var(--warn)" : "#fff"}>
            {label}
          </text>
        ) : null}
      </g>
    );
  };

  return (
    <div className="zoomhost" style={{ width: "100%", height: "100%" }}>
    <div
      ref={host}
      style={{ width: "100%", height: "100%", overflow: "auto", cursor: zoom.isFit ? undefined : "grab" }}
      onPointerDown={drag.onPointerDown}
      onPointerMove={drag.onPointerMove}
      onPointerUp={drag.onPointerUp}
      onPointerCancel={drag.onPointerCancel}
    >
      <svg width={width} height={height} role="img" aria-label="Swimlane timeline" data-window={`${t0.toFixed(3)}-${t1.toFixed(3)}`}>
        <defs>
          <clipPath id="lane-plot">
            <rect x={LABEL_W} y={0} width={plot} height={height} />
          </clipPath>
          <pattern id="lane-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--warn-soft)" />
            <rect width="2.4" height="6" fill="var(--warn)" opacity="0.5" />
          </pattern>
        </defs>
        <rect x={LABEL_W} y={0} width={plot} height={height} fill="var(--graph-bg)" />
        {axis.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={AXIS_H - 4} y2={height} stroke="var(--graph-grid)" />
            <text x={x(t)} y={13} fontSize={10} fill="var(--muted)" textAnchor="middle" fontFamily="var(--font-mono)">
              {axisLabel(t, step)}
            </text>
          </g>
        ))}
        {lanes.map((lane, row) => {
          const y = AXIS_H + row * rowHeight;
          const selected = lane.id === selectedLane;
          return (
            <g
              key={lane.id}
              className={selectedLane && !selected ? "dimmed" : undefined}
              onClick={() => onSelectLane?.(lane.id)}
              style={{ cursor: onSelectLane ? "pointer" : "default" }}
            >
              {selected ? <rect x={0} y={y} width={width} height={rowHeight} fill="var(--accent-soft)" opacity={0.7} /> : null}
              <line x1={0} x2={width} y1={y + rowHeight} y2={y + rowHeight} stroke="var(--border)" />
              <rect x={8} y={y + rowHeight / 2 - 4} width={8} height={8} rx={2} fill={laneColor(lane)} />
              <text x={22} y={y + rowHeight / 2 + 4} fontSize={11.5} fill="var(--text)" fontWeight={500}>
                {lane.label.length > 24 ? `${lane.label.slice(0, 23)}…` : lane.label}
              </text>
              <g clipPath="url(#lane-plot)">
              {lane.bars.map((bar, index) =>
                (bar.end ?? now) >= t0 && bar.start <= t1 ? drawBar(bar, lane, row, index) : null,
              )}
              {lane.ticks
                .filter((tick) => visible(tick.t))
                // The barrier's polls are the bar itself; drawing each one as a
                // dot on top of it only hides the hatching that says "waiting".
                .filter((tick) => !(lane.kind === "indexer" && tick.kind === "barrier_waiting"))
                .map((tick, index) => (
                <circle
                  key={index}
                  cx={x(tick.t)}
                  cy={lane.kind === "researcher" ? y + rowHeight - 4 : y + rowHeight / 2}
                  r={lane.kind === "researcher" ? 2.2 : tick.kind.startsWith("barrier") || tick.kind.startsWith("sync") ? 4.5 : 3}
                  fill={tickColor(tick, lane)}
                  stroke="var(--bg)"
                  strokeWidth={1}
                >
                  <title>{`${tick.kind} · ${String(tick.label ?? "")} · ${clock(tick.t)}`}</title>
                </circle>
              ))}
              </g>
            </g>
          );
        })}
        {marker != null && visible(marker) ? (
          <line x1={x(marker)} x2={x(marker)} y1={AXIS_H - 6} y2={height} stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="3 3" />
        ) : null}
        {live && visible(now) ? (
          <g>
            <line x1={x(now)} x2={x(now)} y1={AXIS_H - 6} y2={height} stroke="var(--danger)" strokeWidth={1.5} />
            <rect x={x(now) - 17} y={1} width={34} height={15} rx={7} fill="var(--danger)" />
            <text x={x(now)} y={12} fontSize={9.5} fill="#fff" textAnchor="middle" fontWeight={600}>
              now
            </text>
          </g>
        ) : null}
      </svg>
    </div>
      <ZoomControls
        onIn={() => zoom.zoomAt(1.6)}
        onOut={() => zoom.zoomAt(1 / 1.6)}
        onFit={zoom.fit}
        fitted={zoom.isFit}
        hint="or Ctrl + scroll; drag to pan"
      />
    </div>
  );
}
