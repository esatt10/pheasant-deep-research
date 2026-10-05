import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { usePoll } from "../hooks/usePoll";
import { useRunStream } from "../hooks/useRunStream";
import { Constellation } from "../live/Constellation";
import { Feed } from "../live/Feed";
import { BudgetPanel, CustodyPanel, FacetPanel } from "../live/Panels";
import { Inspector, SwarmTree } from "../live/Side";
import { Swimlanes } from "../live/Swimlanes";
import { tone } from "../live/summary";
import { Toasts } from "../components/Toasts";
import { clock } from "../format";
import type { LabEvent, Launch, RunModel } from "../types";

type View = "split" | "lanes" | "graph";

/**
 * The live run: Option A's frame (phases, swarm tree, swimlanes, event
 * stream, budget, facets, custody) around Option B's constellation, agent
 * inspector, replay scrubber and region notices. Everything on it is the
 * server's fold of the run's own trace; a replay position is the same fold
 * stopped at an earlier sequence number.
 */
export function LivePage({ onCost }: { onCost: (cost: { spent: number; budget: number } | undefined) => void }) {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { model: liveModel, events, connected } = useRunStream(runId);
  const [view, setView] = useState<View>("split");
  const [agent, setAgent] = useState<string | null>(null);
  const [selected, setSelected] = useState<LabEvent | null>(null);
  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const [replayModel, setReplayModel] = useState<RunModel>();
  const launches = usePoll<Launch[]>(api.launches, 2000);

  useEffect(() => {
    if (runId) return;
    void api.runs().then((runs) => {
      if (runs[0]) navigate(`/live/${runs[0].run_id}`, { replace: true });
    });
  }, [runId, navigate]);

  useEffect(() => {
    setAgent(null);
    setSelected(null);
    setReplaySeq(null);
  }, [runId]);

  useEffect(() => {
    if (replaySeq == null || !runId) {
      setReplayModel(undefined);
      return;
    }
    const handle = window.setTimeout(() => {
      void api.run(runId, replaySeq).then(setReplayModel);
    }, 120);
    return () => window.clearTimeout(handle);
  }, [replaySeq, runId]);

  const model = replaySeq != null ? replayModel ?? liveModel : liveModel;
  useEffect(() => {
    if (liveModel) onCost({ spent: liveModel.budget.committed_usd, budget: liveModel.budget.total_usd });
    return () => onCost(undefined);
  }, [liveModel, onCost]);

  const launch = launches.data?.find((l) => l.run_id === runId && l.status === "running");
  const startedAt = events.length ? Date.parse(events[0].occurred_at) / 1000 : null;
  const live = replaySeq == null && !!launch;
  const maxSeq = liveModel?.run.sequence ?? 0;
  const shownEvents = replaySeq == null ? events : events.filter((e) => e.sequence <= replaySeq);

  const density = useMemo(() => {
    const bins = 96;
    const counts = Array.from({ length: bins }, () => ({ n: 0, warn: false }));
    for (const event of events) {
      const index = Math.min(bins - 1, Math.floor(((event.sequence - 1) / Math.max(1, maxSeq)) * bins));
      counts[index].n += 1;
      if (tone(event) === "warn" || tone(event) === "danger") counts[index].warn = true;
    }
    return counts;
  }, [events, maxSeq]);
  const peak = Math.max(1, ...density.map((d) => d.n));

  if (!runId) return <div className="empty">No runs yet — configure one first.</div>;
  if (!model) {
    return (
      <div className="empty">
        <span className="spinner" /> Folding {runId}…
      </div>
    );
  }

  const marker = selected && startedAt != null ? Date.parse(selected.occurred_at) / 1000 - startedAt : null;
  const state = replaySeq != null ? "REPLAY" : live ? "LIVE" : model.run.finished ? "FINISHED" : "STOPPED";
  const lanes = model.lanes.filter((lane) => lane.bars.length || lane.ticks.length || lane.kind === "indexer");
  const center = (
    <div className="card center">
      <div className="card__head">
        {view === "graph" ? "Constellation" : view === "lanes" ? "Swimlanes" : "Constellation · swimlanes"}
        <span className="sub">
          {view === "graph"
            ? "sources orbit their branch; the region holds what it was sent"
            : "every bar a span, every tick an event"}
        </span>
        <div className="r">
          <div className="tabs" role="tablist">
            {(["split", "graph", "lanes"] as View[]).map((key) => (
              <button key={key} className={`tab${view === key ? " tab--on" : ""}`} onClick={() => setView(key)} role="tab" aria-selected={view === key}>
                {key === "split" ? "Both" : key === "graph" ? "Constellation" : "Swimlanes"}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="center__canvas" style={{ display: "flex", flexDirection: "column" }}>
        {view !== "lanes" ? (
          <div style={{ flex: view === "split" ? "0 0 52%" : 1, minHeight: 0, borderBottom: view === "split" ? "1px solid var(--border)" : undefined }}>
            <Constellation model={model} selectedAgent={agent} onSelectAgent={setAgent} live={live} />
          </div>
        ) : null}
        {view !== "graph" ? (
          <div style={{ flex: 1, minHeight: 0 }}>
            <Swimlanes
              lanes={lanes}
              horizon={model.run.horizon_seconds}
              now={model.run.elapsed_seconds}
              live={live}
              rowHeight={view === "split" ? 22 : 30}
              marker={marker}
              selectedLane={agent ? `agent:${agent}` : null}
              onSelectLane={(id) => setAgent(id.startsWith("agent:") ? id.slice(6) : null)}
            />
          </div>
        ) : null}
      </div>
      <div className="scrub">
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn--small" onClick={() => setReplaySeq(1)} title="Back to the start">
            ⏮
          </button>
          <button
            className={`btn btn--small${replaySeq == null ? " btn--primary" : ""}`}
            onClick={() => setReplaySeq(null)}
            title="Follow the run"
          >
            ● Live
          </button>
        </div>
        <div style={{ position: "relative", height: 34 }}>
          <svg width="100%" height="22" preserveAspectRatio="none" viewBox={`0 0 ${density.length} 22`} style={{ position: "absolute", top: 0 }}>
            {density.map((bin, index) => (
              <rect
                key={index}
                x={index + 0.1}
                y={22 - (bin.n / peak) * 20 - 1}
                width={0.8}
                height={(bin.n / peak) * 20 + 1}
                fill={bin.warn ? "var(--warn)" : "var(--accent-border)"}
                opacity={replaySeq != null && (index / density.length) * maxSeq > replaySeq ? 0.35 : 1}
              />
            ))}
          </svg>
          <input
            type="range"
            min={1}
            max={Math.max(1, maxSeq)}
            value={replaySeq ?? maxSeq}
            onChange={(e) => {
              const value = Number(e.target.value);
              setReplaySeq(value >= maxSeq ? null : value);
            }}
            style={{ position: "absolute", left: 0, right: 0, bottom: -4 }}
            aria-label="Replay position"
          />
        </div>
        <span className="mono muted">
          seq {replaySeq ?? maxSeq} · {clock(model.run.elapsed_seconds)}
        </span>
      </div>
    </div>
  );

  return (
    <div className="live">
      <div className="card runhead">
        <span className={`pill ${state === "LIVE" ? "pill--accent" : state === "REPLAY" ? "pill--info" : ""}`}>
          {state === "LIVE" ? <span className="dot dot--live" /> : null}
          {state}
        </span>
        <div style={{ minWidth: 0 }}>
          <h1>{model.run.topic_title ?? model.run.topic_id ?? model.run.run_id}</h1>
          <div className="mono muted" style={{ fontSize: 11 }}>
            {model.run.run_id} · cfg {model.run.config_digest?.slice(7, 15)}…
          </div>
        </div>
        <div className="steps">
          {model.run.phases.map((phase, index) => (
            <span key={phase.key} className={`step step--${phase.state}`}>
              <i>{phase.state === "done" ? "✓" : index + 1}</i>
              {phase.label}
              {phase.key === "collect" && phase.state === "active" && model.rounds.length ? ` · round ${model.rounds.length + 1}` : ""}
            </span>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          <span className="mono muted">{clock(model.run.elapsed_seconds)} elapsed</span>
          {launch ? (
            <button className="btn btn--small btn--danger" onClick={() => void api.stop(launch.launch_id)}>
              ■ Stop
            </button>
          ) : null}
          <button className="btn btn--small" onClick={() => navigate(`/reports/${model.run.run_id}`)}>
            Reports
          </button>
        </div>
      </div>

      {agent ? (
        <Inspector model={model} agentId={agent} events={shownEvents} onClose={() => setAgent(null)} />
      ) : (
        <SwarmTree model={model} selected={agent} onSelect={setAgent} />
      )}
      {center}
      <Feed
        events={shownEvents}
        startedAt={startedAt}
        connected={connected}
        selected={selected?.sequence ?? null}
        onSelect={(event) => {
          setSelected(event);
          if (event.agent_id && model.agents.some((a) => a.agent_id === event.agent_id)) setAgent(event.agent_id);
        }}
      />
      <div className="bottom">
        <BudgetPanel model={model} />
        <FacetPanel model={model} />
        <CustodyPanel model={model} />
      </div>
      <Toasts notices={model.notices} runId={runId} />
    </div>
  );
}
