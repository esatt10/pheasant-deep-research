import { useLayoutEffect, useRef, useState } from "react";
import type { Agent, Facet, RunModel, Source } from "../types";
import { STAGE_LABEL, armColor, clock } from "../format";
import { ZoomControls, usePanZoom } from "../components/zoom";
import type { Focus } from "./focus";

/**
 * The swarm as a place (Option B). Research branches orbit the orchestrator;
 * each branch's sources ring it, coloured by how far through custody they
 * have got; documents travel to a Pheasant node drawn as the three zones a
 * submission passes through — landing, the index queue, indexed. The queue
 * zone is where the pre-claim interval lives, so a waiting barrier is
 * something you can point at.
 */

const STAGE_FILL: Record<string, { fill: string; stroke?: string; dash?: string }> = {
  discovered: { fill: "var(--bg)", stroke: "var(--r-res)" },
  acquired: { fill: "var(--r-res)" },
  submitted: { fill: "var(--r-res)", stroke: "var(--r-pheasant)" },
  accepted: { fill: "var(--bg)", stroke: "var(--r-pheasant)" },
  awaiting_claim: { fill: "var(--warn-soft)", stroke: "var(--warn)", dash: "2 2" },
  claimed: { fill: "var(--warn)", stroke: "var(--warn)" },
  indexed: { fill: "var(--r-pheasant)" },
  rejected: { fill: "var(--danger-soft)", stroke: "var(--danger)" },
};

function coverage(agent: Agent, model: RunModel): { fraction: number; met: boolean } | null {
  const subtopic = model.subtopics.find((s) => s.subtopic_id === agent.subtopic_id);
  const facets: Facet[] = model.facets.filter((f) => subtopic?.facet_ids.includes(f.facet_id));
  if (!facets.length) return null;
  const facet = facets[0];
  if (facet.meets_minimum) return { fraction: 1, met: true };
  // "sources 2<3" -> 2/3; the first unmet minimum is the one holding it back.
  const match = /(\d+)<(\d+)/.exec(facet.unmet[0] ?? "");
  const fraction = match ? Number(match[1]) / Math.max(1, Number(match[2])) : 0;
  return { fraction: Math.min(0.97, fraction), met: false };
}

function arc(cx: number, cy: number, r: number, fraction: number): string {
  const a0 = -Math.PI / 2;
  const a1 = a0 + Math.max(0.0001, fraction) * Math.PI * 2;
  const large = fraction > 0.5 ? 1 : 0;
  return `M${cx + r * Math.cos(a0)},${cy + r * Math.sin(a0)} A${r},${r} 0 ${large} 1 ${cx + r * Math.cos(a1)},${cy + r * Math.sin(a1)}`;
}

export function Constellation({
  model,
  focus,
  onFocus,
  live,
}: {
  model: RunModel;
  focus: Focus;
  onFocus: (focus: Focus) => void;
  live: boolean;
}) {
  const selectedAgent = focus?.kind === "agent" ? focus.id : null;
  const selectedArm = focus?.kind === "arm" ? focus.id : null;
  // Selecting dims the rest rather than hiding it: where a branch sits among
  // the others is half of what the picture says.
  const dimAgent = (agentId: string) => !!focus && agentId !== selectedAgent;
  const dimArm = (arm: string) => !!focus && arm !== selectedArm;
  const host = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 420 });
  useLayoutEffect(() => {
    const node = host.current;
    if (!node) return;
    const update = () => setSize({ w: node.clientWidth, h: Math.max(260, node.clientHeight) });
    const observer = new ResizeObserver(update);
    observer.observe(node);
    update();
    return () => observer.disconnect();
  }, []);
  const zoom = usePanZoom(host);

  const { w, h } = size;
  const researchers = model.agents.filter((a) => a.role === "researcher");
  const orch = { x: w * 0.09, y: h * 0.5 };
  const planner = { x: w * 0.2, y: h * 0.5 };
  const auditor = { x: w * 0.06, y: h * 0.9 };
  const regionW = Math.min(230, w * 0.27);
  const regionH = Math.min(h - 40, 290);
  const region = { x: w - regionW - 16, y: (h - regionH) / 2 };
  // Crowded swarms stagger into two columns, so a later round's branches sit
  // beside the first round's instead of on top of them.
  const columns = researchers.length > 4 && h < 520 ? 2 : 1;
  const perColumn = Math.ceil(researchers.length / columns);
  const radius = Math.max(13, Math.min(28, (h - 40) / Math.max(1, perColumn) / 2 - 14));
  const bySource = new Map<string, Source[]>();
  for (const source of model.sources) {
    const key = source.agent_id ?? "";
    bySource.set(key, [...(bySource.get(key) ?? []), source]);
  }
  const positions = researchers.map((agent, index) => {
    const column = columns === 2 ? index % 2 : 0;
    const slot = columns === 2 ? Math.floor(index / 2) : index;
    const n = perColumn;
    const y = n === 1 ? h / 2 : 26 + radius + ((h - 52 - 2 * radius) * slot) / Math.max(1, n - 1);
    // A gentle arc: the middle branches sit further out, so edges do not pile up.
    const bow = Math.sin((Math.PI * (slot + 0.5)) / Math.max(1, n)) * w * 0.04;
    return { agent, x: w * (columns === 2 ? 0.4 + column * 0.14 : 0.47) + bow, y, column };
  });
  const custody = model.custody;
  const queue = model.region.queue ?? [];
  const pre = custody.awaiting_claim ?? 0;
  const claimed = custody.claimed ?? 0;
  const indexed = custody.indexed ?? 0;
  const zoneH = (regionH - 60) / 3;
  const roomy = zoneH >= 52;
  const curve = (a: { x: number; y: number }, b: { x: number; y: number }) =>
    `M${a.x},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x},${b.y}`;
  const armIds = model.arms.map((a) => a.arm_id);
  const pheasantTicks = model.lanes.find((l) => l.id === "pheasant")?.ticks ?? [];
  const armTraffic = (arm: string) => pheasantTicks.filter((t) => t.arm === arm).length;
  const armPoint = (arm: string) => {
    const index = armIds.indexOf(arm);
    return {
      x: region.x + 18 + index * ((regionW - 36) / Math.max(1, armIds.length - 1 || 1)),
      y: Math.min(h - 14, region.y + regionH + 26),
    };
  };
  const focusPoint = selectedAgent
    ? positions.find((p) => p.agent.agent_id === selectedAgent) ?? null
    : selectedArm && armIds.includes(selectedArm)
      ? armPoint(selectedArm)
      : null;

  return (
    <div
      ref={host}
      className="zoomhost"
      style={{ width: "100%", height: "100%" }}
      onPointerDown={zoom.drag.onPointerDown}
      onPointerMove={zoom.drag.onPointerMove}
      onPointerUp={zoom.drag.onPointerUp}
      onPointerCancel={zoom.drag.onPointerCancel}
    >
      <svg width={w} height={h} role="img" aria-label="Swarm constellation" onClick={() => onFocus(null)}>
        <defs>
          <pattern id="cg-grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <path d="M28 0H0V28" fill="none" stroke="var(--graph-grid)" />
          </pattern>
          <pattern id="cg-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="7" height="7" fill="var(--warn-soft)" />
            <rect width="2" height="7" fill="var(--warn)" opacity="0.3" />
          </pattern>
          <marker id="cg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0L8 4L0 8z" fill="var(--r-pheasant)" />
          </marker>
        </defs>
        <rect width={w} height={h} fill="var(--graph-bg)" />
        <g transform={zoom.transform} data-testid="constellation-view">
        <rect x={-w * 4} y={-h * 4} width={w * 9} height={h * 9} fill="url(#cg-grid)" />

        <path d={curve(orch, planner)} stroke="var(--graph-edge)" strokeWidth={2} fill="none" />
        <path d={curve(orch, auditor)} stroke="var(--graph-edge)" strokeWidth={1.2} strokeDasharray="4 3" fill="none" />
        {positions.map(({ agent, x, y }) => (
          <path key={`e-${agent.agent_id}`} className={dimAgent(agent.agent_id) ? "dimmed" : undefined} d={curve(planner, { x: x - radius, y })} stroke="var(--graph-edge)" strokeWidth={1.6} fill="none" />
        ))}

        {/* submit traffic: branch -> region landing */}
        {positions.map(({ agent, x, y }, index) => {
          const from = { x: x + radius + 4, y };
          const to = { x: region.x - 4, y: region.y + 40 + zoneH * 0.5 + (index - (positions.length - 1) / 2) * 6 };
          const active = agent.submitted > 0;
          return (
            <g key={`s-${agent.agent_id}`} className={dimAgent(agent.agent_id) ? "dimmed" : undefined}>
              <path
                d={curve(from, to)}
                stroke="var(--r-pheasant)"
                strokeWidth={active ? 1.2 + Math.min(3, agent.submitted) * 0.5 : 0.8}
                strokeDasharray={active ? "5 9" : "2 4"}
                className={active && live ? "flow-dash" : undefined}
                fill="none"
                opacity={active ? 0.75 : 0.3}
                markerEnd={active ? "url(#cg-arrow)" : undefined}
              />
            </g>
          );
        })}

        {/* nodes */}
        <g>
          <circle cx={orch.x} cy={orch.y} r={24} fill="var(--r-orch)" />
          <text x={orch.x} y={orch.y + 5} fontSize={15} textAnchor="middle" fill="var(--bg)">◎</text>
          <text x={orch.x} y={orch.y + 40} fontSize={11.5} fontWeight={600} textAnchor="middle" fill="var(--text)">Orchestrator</text>
          <text x={orch.x} y={orch.y + 53} fontSize={10} textAnchor="middle" fill="var(--muted)">
            {model.rounds.length ? `round ${model.rounds.length}` : "planning"}
          </text>
        </g>
        <g>
          <circle cx={planner.x} cy={planner.y} r={13} fill="var(--bg-raised)" stroke="var(--r-plan)" strokeWidth={2} />
          <text x={planner.x} y={planner.y + 28} fontSize={11.5} fontWeight={600} textAnchor="middle" fill="var(--text)">Planner</text>
          <text x={planner.x} y={planner.y + 41} fontSize={10} textAnchor="middle" fill="var(--muted)">{model.subtopics.length} subtopics</text>
        </g>
        <g>
          <circle cx={auditor.x} cy={auditor.y} r={11} fill="var(--bg-raised)" stroke="var(--r-aud)" strokeWidth={2} />
          <text x={auditor.x + 18} y={auditor.y + 4} fontSize={11} fill="var(--text-soft)">
            Auditor{model.audit.coverage_fraction != null ? ` · coverage ${Number(model.audit.coverage_fraction).toFixed(2)}` : ""}
          </text>
        </g>

        {positions.map(({ agent, x, y }) => {
          const sources = bySource.get(agent.agent_id) ?? [];
          const cover = coverage(agent, model);
          const selected = agent.agent_id === selectedAgent;
          return (
            <g
              key={agent.agent_id}
              style={{ cursor: "pointer" }}
              className={dimAgent(agent.agent_id) ? "dimmed" : undefined}
              data-agent={agent.agent_id}
              onClick={(event) => {
                event.stopPropagation();
                onFocus(selected ? null : { kind: "agent", id: agent.agent_id });
              }}
            >
              <title>{`${agent.short_id} · ${agent.subtopic_label ?? ""}\n${agent.searches} searches · ${agent.acquired} acquired`}</title>
              {sources.map((source, index) => {
                const angle = (-90 + (index / Math.max(1, sources.length)) * 360) * (Math.PI / 180);
                const orbit = radius + 14 + (index % 2) * 6;
                const sx = x + Math.cos(angle) * orbit;
                const sy = y + Math.sin(angle) * orbit;
                const style = STAGE_FILL[source.stage] ?? STAGE_FILL.discovered;
                return (
                  <g key={source.source_id}>
                    <line x1={x} y1={y} x2={sx} y2={sy} stroke="var(--graph-edge)" strokeWidth={0.6} opacity={0.6} />
                    <circle cx={sx} cy={sy} r={4} fill={style.fill} stroke={style.stroke ?? "none"} strokeWidth={1.3} strokeDasharray={style.dash}>
                      <title>{`${source.title ?? source.source_id}\n${STAGE_LABEL[source.stage]}`}</title>
                    </circle>
                  </g>
                );
              })}
              <circle cx={x} cy={y} r={radius + 7} fill="none" stroke="var(--bg-active)" strokeWidth={4} />
              {cover ? (
                cover.fraction >= 1 ? (
                  <circle cx={x} cy={y} r={radius + 7} fill="none" stroke="var(--accent)" strokeWidth={4} />
                ) : (
                  <path d={arc(x, y, radius + 7, cover.fraction)} fill="none" stroke="var(--warn)" strokeWidth={4} strokeLinecap="round" />
                )
              ) : null}
              {selected ? <circle cx={x} cy={y} r={radius + 13} fill="none" stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="4 3" /> : null}
              <circle cx={x} cy={y} r={radius} fill="var(--bg-raised)" stroke="var(--r-res)" strokeWidth={2} />
              {agent.status === "searching" && live ? (
                <circle cx={x} cy={y} r={radius} fill="none" stroke="var(--r-res)" strokeWidth={2} className="flow-dash" strokeDasharray="4 6" />
              ) : null}
              <text x={x} y={y + 1} fontSize={radius > 20 ? 12.5 : 10.5} fontWeight={650} textAnchor="middle" fill="var(--text)">
                {agent.short_id.split(" ").pop()}
              </text>
              <text x={x} y={y + 13} fontSize={9.5} textAnchor="middle" fill="var(--muted)">{agent.acquired} src</text>
              {columns === 1 || (positions.find((p) => p.agent === agent)?.column ?? 0) === 0 ? (
                <text x={x - radius - 22} y={y + 4} fontSize={10.5} textAnchor="end" fill="var(--text-soft)" fontWeight={500}>
                  {(agent.subtopic_label ?? "").length > 24 ? `${agent.subtopic_label!.slice(0, 23)}…` : agent.subtopic_label}
                </text>
              ) : null}
            </g>
          );
        })}

        {/* the region */}
        <g>
          <rect x={region.x} y={region.y} width={regionW} height={regionH} rx={16} fill="var(--bg-raised)" stroke="var(--r-pheasant)" strokeWidth={2} />
          <image href="/pheasant.png" x={region.x + 10} y={region.y + 8} width={24} height={24} />
          <text x={region.x + 40} y={region.y + 21} fontSize={12.5} fontWeight={650} fill="var(--text)">Pheasant region</text>
          <text x={region.x + 40} y={region.y + 34} fontSize={10} fill="var(--muted)">
            {model.region.server_name ? `${model.region.server_name} ${model.region.server_version ?? ""}` : "not connected yet"}
          </text>
          {[
            {
              title: "Landing · receipts",
              sub: `${custody.submitted ?? 0} submitted · ${custody.accepted ?? 0} accepted · ${custody.rejected ?? 0} rejected`,
              color: "var(--text-soft)",
              hatch: false,
            },
            {
              title: pre ? "Index queue · pre-claim" : claimed ? "Index queue · claimed" : "Index queue",
              sub: pre
                ? `${pre} doc(s) · waiting ${clock(queue[0]?.waiting_seconds)} · position ${queue[0]?.position ?? "—"}`
                : claimed
                  ? `claimed by ${queue.find((q) => q.state === "claimed")?.claimed_by ?? "an indexer"}`
                  : model.region.sync.disposition === "completed"
                    ? "indexed inside the sync call"
                    : "nothing waiting",
              color: pre || claimed ? "var(--warn)" : "var(--text-soft)",
              hatch: pre > 0,
            },
            {
              title: "Indexed · acknowledged",
              sub: `${indexed} artifact(s)${model.region.barrier.state === "crossed" ? " · barrier crossed" : ""}`,
              color: "var(--accent-text)",
              hatch: false,
            },
          ].map((zone, index) => {
            const zy = region.y + 44 + index * (zoneH + 6);
            return (
              <g key={zone.title}>
                <rect x={region.x + 10} y={zy} width={regionW - 20} height={zoneH} rx={10} fill={zone.hatch ? "url(#cg-hatch)" : "var(--bg-sunken)"} stroke={zone.color} strokeWidth={1.1} />
                <text x={region.x + 20} y={zy + 16} fontSize={11} fontWeight={600} fill={zone.color}>{zone.title}</text>
                <text x={region.x + 20} y={zy + 30} fontSize={10} fill="var(--text-soft)">
                  {zone.sub.length > Math.floor((regionW - 30) / 5.6) ? `${zone.sub.slice(0, Math.floor((regionW - 30) / 5.6) - 1)}…` : zone.sub}
                </text>
                {roomy && index === 1 && (pre || claimed)
                  ? Array.from({ length: Math.min(10, pre || claimed) }, (_, k) => (
                      <rect key={k} x={region.x + 20 + k * 15} y={zy + 38} width={10} height={12} rx={2} fill={claimed ? "var(--warn)" : "var(--bg)"} stroke="var(--warn)" strokeDasharray={claimed ? undefined : "2 2"} />
                    ))
                  : null}
                {roomy && index === 2
                  ? Array.from({ length: Math.min(36, indexed) }, (_, k) => (
                      <rect key={k} x={region.x + 20 + (k % 12) * 15} y={zy + 38 + Math.floor(k / 12) * 13} width={10} height={10} rx={2} fill="var(--r-pheasant)" opacity={0.85} />
                    ))
                  : null}
              </g>
            );
          })}
        </g>

        {/* arms read from the region after the freeze */}
        {armIds.map((arm, index) => {
          const ax = region.x + 18 + index * ((regionW - 36) / Math.max(1, armIds.length - 1 || 1));
          const ay = Math.min(h - 14, region.y + regionH + 26);
          const traffic = armTraffic(arm);
          return (
            <g
              key={arm}
              className={dimArm(arm) ? "dimmed" : undefined}
              style={{ cursor: "pointer" }}
              onClick={(event) => {
                event.stopPropagation();
                onFocus(selectedArm === arm ? null : { kind: "arm", id: arm });
              }}
            >
              {selectedArm === arm ? <circle cx={ax} cy={ay} r={14} fill="none" stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="4 3" /> : null}
              <line x1={ax} y1={region.y + regionH} x2={ax} y2={ay - 9} stroke={armColor(arm)} strokeWidth={traffic ? 1.5 : 0.8} strokeDasharray={traffic ? undefined : "2 3"} />
              <circle cx={ax} cy={ay} r={9} fill={armColor(arm)} />
              <text x={ax} y={ay + 3.5} fontSize={8.5} fontWeight={700} textAnchor="middle" fill="var(--bg)">{arm}</text>
              <title>{`${arm}: ${traffic} Pheasant call(s) — click to filter to this arm`}</title>
            </g>
          );
        })}
        </g>
      </svg>
      <ZoomControls
        onIn={() => zoom.zoomAt(1.3)}
        onOut={() => zoom.zoomAt(1 / 1.3)}
        onFit={zoom.fit}
        fitted={zoom.isFit}
        hint="or scroll; drag to pan"
      >
        {focusPoint ? (
          <button className="zoomctl__btn zoomctl__fit" aria-label="Zoom to the selection" title="Zoom to the selection" onClick={() => zoom.centerOn(focusPoint.x, focusPoint.y, 2)}>
            ◎
          </button>
        ) : null}
      </ZoomControls>
    </div>
  );
}
