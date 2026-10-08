import { useCallback, useEffect, useMemo, useState } from "react";
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
import { type Focus, eventMatches, focusFromLane, focusLabel, laneOf, runProgress, sameFocus } from "../live/focus";
import { Toasts } from "../components/Toasts";
import { clock } from "../format";
import type { LabEvent, Launch, RunModel } from "../types";

type View = "split" | "lanes" | "graph";

interface Layout {
  side: boolean;
  feed: boolean;
  bottom: boolean;
}

const LAYOUT_KEY = "pheasant-lab-live-layout";
const OPEN: Layout = { side: true, feed: true, bottom: true };

function storedLayout(): Layout {
  try {
    return { ...OPEN, ...(JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}") as Partial<Layout>) };
  } catch {
    return OPEN;
  }
}

/**
 * The live run: Option A's frame (phases, swarm tree, swimlanes, event
 * stream, budget, facets, custody) around Option B's constellation, agent
 * inspector, replay scrubber and region notices. Everything on it is the
 * server's fold of the run's own trace; a replay position is the same fold
 * stopped at an earlier sequence number.
 *
 * One selection - a branch or an arm - drives every panel: the feed narrows
 * to it, the visuals dim around it, custody and facets narrow to it. It is
 * cleared by selecting it again, clicking empty canvas, the × in the run
 * head or the feed, or Esc. The side panes fold to a spine and the centre
 * expands to the page; which panes are folded is remembered per browser.
 */
export function LivePage({ onCost }: { onCost: (cost: { spent: number; budget: number } | undefined) => void }) {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { model: liveModel, events, state: stream } = useRunStream(runId);
  // The constellation is the default view: the swarm, its sources and the region
  // at a glance. The tabs switch to swimlanes or both.
  const [view, setView] = useState<View>("graph");
  const [focus, setFocus] = useState<Focus>(null);
  const [selected, setSelected] = useState<LabEvent | null>(null);
  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const [replayModel, setReplayModel] = useState<RunModel>();
  const [replayLoading, setReplayLoading] = useState(false);
  const [layout, setLayoutState] = useState<Layout>(storedLayout);
  const [expanded, setExpanded] = useState(false);
  const launches = usePoll<Launch[]>(api.launches, 2000);

  const setLayout = useCallback((patch: Partial<Layout>) => {
    setLayoutState((current) => {
      const next = { ...current, ...patch };
      try {
        localStorage.setItem(LAYOUT_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  }, []);

  const toggleFocus = useCallback((next: Focus) => setFocus((current) => (sameFocus(current, next) ? null : next)), []);
  const clearAll = useCallback(() => {
    setFocus(null);
    setSelected(null);
  }, []);

  useEffect(() => {
    if (runId) return;
    void api
      .runs()
      .then((runs) => {
        if (runs[0]) navigate(`/live/${runs[0].run_id}`, { replace: true });
      })
      .catch(() => undefined); // a refused key opens the key dialog; the page remounts on connect
  }, [runId, navigate]);

  useEffect(() => {
    setFocus(null);
    setSelected(null);
    setReplaySeq(null);
  }, [runId]);

  // Esc steps back one level: an expanded canvas first, then the selection.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.("input, select, textarea, .modal")) return;
      if (expanded) setExpanded(false);
      else clearAll();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, clearAll]);

  useEffect(() => {
    if (replaySeq == null || !runId) {
      setReplayModel(undefined);
      setReplayLoading(false);
      return;
    }
    setReplayLoading(true);
    let current = true;
    const handle = window.setTimeout(() => {
      void api
        .run(runId, replaySeq)
        .then((value) => current && setReplayModel(value))
        .catch(() => undefined)
        .finally(() => current && setReplayLoading(false));
    }, 120);
    return () => {
      current = false;
      window.clearTimeout(handle);
    };
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
  const shownEvents = useMemo(
    () => (replaySeq == null ? events : events.filter((e) => e.sequence <= replaySeq)),
    [events, replaySeq],
  );

  const density = useMemo(() => {
    const bins = 96;
    const counts = Array.from({ length: bins }, () => ({ n: 0, warn: false, focus: 0 }));
    for (const event of events) {
      const index = Math.min(bins - 1, Math.floor(((event.sequence - 1) / Math.max(1, maxSeq)) * bins));
      counts[index].n += 1;
      if (focus && eventMatches(event, focus)) counts[index].focus += 1;
      if (tone(event) === "warn" || tone(event) === "danger") counts[index].warn = true;
    }
    return counts;
  }, [events, maxSeq, focus]);
  const peak = Math.max(1, ...density.map((d) => d.n));

  if (!runId) return <div className="empty">No runs yet — configure one first.</div>;
  if (!model) return <LiveSkeleton runId={runId} stream={stream} />;

  const marker = selected && startedAt != null ? Date.parse(selected.occurred_at) / 1000 - startedAt : null;
  const state = replaySeq != null ? "REPLAY" : live ? "LIVE" : model.run.finished ? "FINISHED" : "STOPPED";
  const lanes = model.lanes.filter((lane) => lane.bars.length || lane.ticks.length || lane.kind === "indexer");
  const progress = runProgress(model);
  const label = focusLabel(focus, model);
  const agentFocus = focus?.kind === "agent" ? focus.id : null;

  const center = (
    <div className="card center">
      <div className="card__head">
        {view === "graph" ? "Constellation" : view === "lanes" ? "Swimlanes" : "Constellation · swimlanes"}
        <span className="sub">
          {focus
            ? `${label} selected — the rest is dimmed`
            : view === "graph"
              ? "sources orbit their branch; the region holds what it was sent"
              : "every bar a span, every tick an event"}
        </span>
        <div className="r">
          <div className="tabs" role="tablist">
            {(["graph", "lanes", "split"] as View[]).map((key) => (
              <button key={key} className={`tab${view === key ? " tab--on" : ""}`} onClick={() => setView(key)} role="tab" aria-selected={view === key}>
                {key === "split" ? "Both" : key === "graph" ? "Constellation" : "Swimlanes"}
              </button>
            ))}
          </div>
          <button
            className="btn btn--ghost btn--small"
            onClick={() => setExpanded((v) => !v)}
            aria-pressed={expanded}
            aria-label={expanded ? "Restore the layout" : "Expand the visual"}
            title={expanded ? "Restore (Esc)" : "Expand to the whole page"}
          >
            {expanded ? "⤡" : "⤢"}
          </button>
        </div>
      </div>
      <Toasts notices={model.notices} runId={runId} inline />
      <div className="center__canvas" style={{ display: "flex", flexDirection: "column" }}>
        {view !== "lanes" ? (
          <div style={{ flex: view === "split" ? "0 0 52%" : 1, minHeight: 0, borderBottom: view === "split" ? "1px solid var(--border)" : undefined }}>
            <Constellation model={model} focus={focus} onFocus={setFocus} live={live} />
          </div>
        ) : null}
        {view !== "graph" ? (
          <div style={{ flex: 1, minHeight: 0 }}>
            <Swimlanes
              lanes={lanes}
              horizon={model.run.horizon_seconds}
              now={model.run.elapsed_seconds}
              live={live}
              rowHeight={view === "split" && !expanded ? 22 : 30}
              marker={marker}
              selectedLane={laneOf(focus)}
              onSelectLane={(id) => toggleFocus(focusFromLane(id))}
            />
          </div>
        ) : null}
        {replayLoading ? (
          <div className="canvas-busy" role="status">
            <span className="spinner" /> folding seq {replaySeq}…
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
              <g key={index} opacity={replaySeq != null && (index / density.length) * maxSeq > replaySeq ? 0.35 : 1}>
                <rect
                  x={index + 0.1}
                  y={22 - (bin.n / peak) * 20 - 1}
                  width={0.8}
                  height={(bin.n / peak) * 20 + 1}
                  fill={bin.warn ? "var(--warn)" : "var(--accent-border)"}
                  opacity={focus ? 0.35 : 1}
                />
                {focus && bin.focus ? (
                  <rect x={index + 0.1} y={22 - (bin.focus / peak) * 20 - 1} width={0.8} height={(bin.focus / peak) * 20 + 1} fill="var(--accent)" />
                ) : null}
              </g>
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

  const classes = [
    "live",
    layout.side ? "" : "live--side-closed",
    layout.feed ? "" : "live--feed-closed",
    layout.bottom ? "" : "live--bottom-closed",
    expanded ? "live--expanded" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <div className="card runhead">
        <span className={`pill ${state === "LIVE" ? "pill--accent" : state === "REPLAY" ? "pill--info" : ""}`}>
          {state === "LIVE" ? <span className="dot dot--live" /> : null}
          {state}
        </span>
        <div style={{ minWidth: 0 }}>
          <h1>{model.run.topic_title ?? model.run.topic_id ?? model.run.run_id}</h1>
          <div className="muted" style={{ fontSize: 11 }}>
            {model.run.experiment ?? "Research run"} · {model.run.created_at ? new Date(model.run.created_at).toLocaleString() : "date unavailable"}
          </div>
        </div>
        <div className="steps">
          {model.run.phases.map((phase, index) => (
            <span key={phase.key} className={`step step--${phase.state}`}>
              <i>{phase.state === "done" ? "✓" : phase.state === "active" && live ? <span className="spinner spinner--tiny" /> : index + 1}</i>
              {phase.label}
              {phase.key === "collect" && phase.state === "active" && model.rounds.length ? ` · round ${model.rounds.length + 1}` : ""}
            </span>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {focus ? (
            <span className="pill pill--accent focuschip">
              {label}
              <button className="btn btn--ghost btn--small" onClick={clearAll} aria-label="Clear the selection" title="Clear the selection (Esc)">
                ×
              </button>
            </span>
          ) : null}
          <span className="mono muted" style={{ whiteSpace: "nowrap" }}>
            {progress.label ? `${progress.label} · ` : ""}
            {clock(model.run.elapsed_seconds)} elapsed
          </span>
          {launch ? (
            <button className="btn btn--small btn--danger" onClick={() => void api.stop(launch.launch_id)}>
              ■ Stop
            </button>
          ) : null}
          <button className="btn btn--small" onClick={() => navigate(`/reports/${model.run.run_id}`)}>
            Reports
          </button>
        </div>
        <div
          className={`runprogress${live ? " runprogress--live" : ""}`}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress.fraction * 100)}
          aria-label={progress.label}
          title={progress.label}
        >
          <i style={{ width: `${progress.fraction * 100}%` }} />
        </div>
      </div>

      {layout.side ? (
        agentFocus ? (
          <Inspector model={model} agentId={agentFocus} events={shownEvents} onClose={() => setFocus(null)} />
        ) : (
          <SwarmTree model={model} focus={focus} onFocus={toggleFocus} onCollapse={() => setLayout({ side: false })} />
        )
      ) : (
        <Spine label="Swarm" side="left" onOpen={() => setLayout({ side: true })} badge={focus ? label : undefined} />
      )}
      {center}
      {layout.feed ? (
        <Feed
          events={shownEvents}
          sources={model.sources}
          startedAt={startedAt}
          stream={stream}
          selected={selected?.sequence ?? null}
          onSelect={(event) => {
            setSelected(event);
            if (event?.agent_id && model.agents.some((a) => a.agent_id === event.agent_id)) {
              setFocus({ kind: "agent", id: event.agent_id });
            }
          }}
          focus={focus}
          focusLabel={label}
          onClearFocus={clearAll}
          onCollapse={() => setLayout({ feed: false })}
        />
      ) : (
        <Spine label="Event stream" side="right" onOpen={() => setLayout({ feed: true })} badge={stream !== "live" ? stream : undefined} />
      )}
      <div className="bottom">
        <button
          className="bottom__toggle"
          onClick={() => setLayout({ bottom: !layout.bottom })}
          aria-expanded={layout.bottom}
          title={layout.bottom ? "Collapse budget, facets and custody" : "Show budget, facets and custody"}
        >
          {layout.bottom ? "▾" : "▸"} Budget · facets · custody
          {!layout.bottom ? (
            <span className="muted small">
              {" "}
              — ${model.budget.committed_usd.toFixed(2)} of ${model.budget.total_usd.toFixed(2)} · {model.facets.filter((f) => f.meets_minimum).length}/{model.facets.length} facets met · {model.custody.indexed ?? 0} indexed
            </span>
          ) : null}
        </button>
        {layout.bottom ? (
          <>
            <BudgetPanel model={model} />
            <FacetPanel model={model} focus={focus} />
            <CustodyPanel model={model} focus={focus} />
          </>
        ) : null}
      </div>
    </div>
  );
}

/** A folded pane: a thin strip that says what is there and opens it again. */
function Spine({ label, side, onOpen, badge }: { label: string; side: "left" | "right"; onOpen: () => void; badge?: string }) {
  return (
    <button className={`card spine spine--${side}`} onClick={onOpen} aria-label={`Show ${label.toLowerCase()}`} title={`Show ${label.toLowerCase()}`}>
      <span className="spine__arrow">{side === "left" ? "⇥" : "⇤"}</span>
      <span className="spine__label">{label}</span>
      {badge ? <span className="spine__badge">{badge}</span> : null}
    </button>
  );
}

/** The page's own shape while the first fold arrives, instead of a lone spinner. */
function LiveSkeleton({ runId, stream }: { runId: string; stream: string }) {
  return (
    <div className="live live--loading" aria-busy="true">
      <div className="card runhead">
        <span className="spinner" />
        <div>
          <h1>Folding {runId}…</h1>
          <div className="muted small">{stream === "locked" ? "waiting for the console's access key" : "reading raw/*.jsonl"}</div>
        </div>
      </div>
      <div className="card side">{Array.from({ length: 7 }, (_, i) => <div key={i} className="skel skel-row" />)}</div>
      <div className="card center"><div className="skel" style={{ margin: 14, flex: 1 }} /></div>
      <div className="card feed">{Array.from({ length: 10 }, (_, i) => <div key={i} className="skel skel-row" style={{ width: `${55 + ((i * 29) % 40)}%` }} />)}</div>
      <div className="bottom">
        <div className="card"><div className="skel skel-row" /></div>
        <div className="card"><div className="skel skel-row" /></div>
        <div className="card"><div className="skel skel-row" /></div>
      </div>
    </div>
  );
}
