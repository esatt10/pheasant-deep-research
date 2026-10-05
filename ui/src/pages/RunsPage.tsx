import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { usePoll } from "../hooks/usePoll";
import { ago } from "../format";
import type { Launch, RunRow } from "../types";

const STAGES = ["collect", "freeze-benchmark", "evaluate"];

const LAUNCH_TONE: Record<Launch["status"], string> = {
  running: "pill pill--info",
  succeeded: "pill pill--accent",
  completed_with_findings: "pill pill--warn",
  failed: "pill pill--danger",
  refused: "pill pill--danger",
  stopped: "pill",
  crashed_resumable: "pill pill--warn",
  interrupted: "pill pill--warn",
  ended: "pill",
};

export function RunsPage() {
  const navigate = useNavigate();
  const runs = usePoll<RunRow[]>(api.runs, 4000);
  const launches = usePoll<Launch[]>(api.launches, (data) =>
    data?.some((l) => l.status === "running") ? 1000 : 4000,
  );
  const [resuming, setResuming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Resume = `pheasant-lab run --resume <run>` under the run's own config and
  // overrides: it skips every stage the checkpoint records as completed and
  // continues the one a crash interrupted from its last durable boundary.
  const resume = async (run: RunRow) => {
    if (!run.config) return;
    setResuming(run.run_id);
    setError(null);
    try {
      await api.launch({ kind: "resume", config: run.config, set: run.overrides, run_id: run.run_id });
      void launches.refresh();
      void runs.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setResuming(null);
    }
  };

  return (
    <div className="page page--fit">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h1>Runs</h1>
        <span className="muted">every run directory under the console's output root</span>
        <Link to="/configure" className="btn btn--primary" style={{ marginLeft: "auto", textDecoration: "none" }}>
          + New run
        </Link>
      </div>

      {error ? <div className="pill pill--danger" style={{ whiteSpace: "normal" }}>{error}</div> : null}

      {launches.data && launches.data.length > 0 ? (
        <div className="card runs__launches">
          <div className="card__head">
            Launched from this console{" "}
            <span className="sub">detached processes — they keep running if the console stops, and it re-attaches on restart</span>
          </div>
          <table className="table">
            <tbody>
              {launches.data.map((launch) => (
                <tr key={launch.launch_id}>
                  <td style={{ width: 150 }}>
                    <span className={LAUNCH_TONE[launch.status]}>
                      {launch.status === "running" ? <span className="spinner" /> : null}
                      {launch.status.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td>
                    <b>{launch.kind}</b> <span className="muted mono">{launch.config}</span>
                    {launch.step ? <span className="muted"> · step {launch.step}</span> : null}
                  </td>
                  <td className="mono muted">{launch.output_tail.at(-1)?.slice(0, 90)}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {launch.run_id ? (
                      <Link className="btn btn--small" to={`/live/${launch.run_id}`}>
                        Watch live
                      </Link>
                    ) : null}{" "}
                    {launch.status === "running" ? (
                      <button className="btn btn--small btn--danger" onClick={() => void api.stop(launch.launch_id)}>
                        Stop
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="card fit-scroll runs__table">
        <table className="table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Topic</th>
              <th>Stages</th>
              <th>Region</th>
              <th>Updated</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(runs.data ?? []).map((run) => (
              <tr key={run.run_id} className="clickable" onClick={() => navigate(`/live/${run.run_id}`)}>
                <td>
                  <div className="mono">{run.run_id}</div>
                  <div className="muted small">{run.command}</div>
                </td>
                <td>
                  {run.topic_title ?? "—"}
                  <div className="muted small">{run.experiment}</div>
                </td>
                <td>
                  <span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {STAGES.map((stage) => (
                      <span
                        key={stage}
                        className={run.stages[stage] === "completed" ? "pill pill--accent" : "pill"}
                      >
                        {run.stages[stage] === "completed" ? "✓ " : ""}
                        {stage.replace("-benchmark", "")}
                      </span>
                    ))}
                    {run.reported ? <span className="pill pill--accent">report</span> : null}
                  </span>
                </td>
                <td>{run.mock ? <span className="pill">mock</span> : <span className="pill pill--info">live</span>}</td>
                <td className="muted">
                  {ago(run.updated_at)}
                  {run.live ? (
                    <div><span className="pill pill--info"><span className="spinner" /> running</span></div>
                  ) : run.interrupted ? (
                    <div title="A stage was running when its process stopped. Its checkpoint is on disk.">
                      <span className="pill pill--warn">interrupted</span>
                    </div>
                  ) : null}
                </td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {run.resumable && run.config ? (
                    <button
                      className="btn btn--small btn--primary"
                      title={`pheasant-lab run --resume ${run.run_id}`}
                      disabled={resuming === run.run_id}
                      onClick={(e) => {
                        e.stopPropagation();
                        void resume(run);
                      }}
                    >
                      {resuming === run.run_id ? <span className="spinner" /> : null} Resume
                    </button>
                  ) : null}{" "}
                  <Link className="btn btn--small" to={`/reports/${run.run_id}`} onClick={(e) => e.stopPropagation()}>
                    Reports
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.data && runs.data.length === 0 ? (
          <div className="empty">No runs yet. Configure one and launch it — the offline demo costs nothing.</div>
        ) : null}
      </div>
    </div>
  );
}
