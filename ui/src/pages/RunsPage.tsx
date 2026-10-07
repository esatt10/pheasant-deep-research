import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
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
  const runs = usePoll<RunRow[]>(api.runsQuiet, 4000);
  const launches = usePoll<Launch[]>(api.launches, (data) =>
    data?.some((l) => l.status === "running") ? 1000 : 4000,
  );
  const [resuming, setResuming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ run: RunRow; label: string; notes: string } | null>(null);
  const [deleting, setDeleting] = useState<RunRow | null>(null);
  const [dropping, setDropping] = useState<RunRow | null>(null);

  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    try {
      await work();
      void runs.refresh();
      void launches.refresh();
    } catch (caught) {
      setError((caught as Error).message);
    }
  };

  // Resume = `pheasant-lab run --resume <run>` under the run's own config and
  // overrides: it skips every stage the checkpoint records as completed and
  // continues the one a crash interrupted from its last durable boundary.
  const resume = async (run: RunRow) => {
    if (!run.config) return;
    setResuming(run.run_id);
    setError(null);
    try {
      await api.resumeRun(run.run_id);
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
        <span className="muted">
          every run directory under the console's output root
          {runs.data?.[0]?.deployment ? <> · made in <b>{runs.data[0].deployment}</b></> : null}
        </span>
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
                  {run.label ? <div><b>{run.label}</b></div> : null}
                  <div className="mono">
                    {run.run_id}
                    {run.kept ? <span className="pill pill--accent" title="Kept: exempt from every retention rule" style={{ marginLeft: 6 }}>☆ kept</span> : null}
                  </div>
                  {run.notes ? <div className="small">{run.notes}</div> : null}
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
                  </Link>{" "}
                  <RunMenu
                    run={run}
                    onRename={() => setEditing({ run, label: run.label ?? "", notes: run.notes ?? "" })}
                    onKeep={() => void act(() => api.updateRun(run.run_id, { keep: !run.kept }))}
                    onReport={() => void act(() => api.generateReports(run.run_id))}
                    onDropReports={() => setDropping(run)}
                    onDelete={() => setDeleting(run)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.loading ? Array.from({ length: 5 }, (_, i) => <div key={i} className="skel skel-row" style={{ width: `${92 - i * 8}%` }} />) : null}
        {editing ? (
          <div className="modal-scrim" onClick={() => setEditing(null)}>
            <form
              className="modal modal--narrow"
              aria-label="Rename run"
              onClick={(e) => e.stopPropagation()}
              onSubmit={(e) => {
                e.preventDefault();
                const { run, label, notes } = editing;
                setEditing(null);
                void act(() => api.updateRun(run.run_id, { label: label.trim() || null, notes: notes.trim() || null }));
              }}
            >
              <div className="modal__header">Rename {editing.run.run_id}</div>
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="fld">
                  <label htmlFor="run-name">name</label>
                  <input id="run-name" className="input" autoFocus value={editing.label} onChange={(e) => setEditing({ ...editing, label: e.target.value })} />
                </div>
                <div className="fld">
                  <label htmlFor="run-notes">notes</label>
                  <textarea id="run-notes" className="input" rows={3} value={editing.notes} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} />
                  <div className="h">console bookkeeping: the run's own files are never rewritten</div>
                </div>
              </div>
              <div className="modal__footer">
                <button type="button" className="btn" onClick={() => setEditing(null)}>Cancel</button>
                <button type="submit" className="btn btn--primary">Save</button>
              </div>
            </form>
          </div>
        ) : null}
        {deleting ? (
          <ConfirmDialog
            title={`Delete ${deleting.label ?? deleting.run_id}`}
            body={<>The whole run directory — raw trace, projection, reports — is deleted and audited. It cannot be brought back.</>}
            action="Delete run"
            confirmText={deleting.run_id}
            onConfirm={async () => {
              await api.deleteRun(deleting.run_id);
              void runs.refresh();
            }}
            onClose={() => setDeleting(null)}
          />
        ) : null}
        {dropping ? (
          <ConfirmDialog
            title={`Delete the reports of ${dropping.label ?? dropping.run_id}`}
            body={<>Reports are derived from the raw trace; <b>Regenerate reports</b> renders them again.</>}
            action="Delete reports"
            onConfirm={async () => {
              await api.deleteReports(dropping.run_id);
              void runs.refresh();
            }}
            onClose={() => setDropping(null)}
          />
        ) : null}
        {runs.data && runs.data.length === 0 ? (
          <div className="empty">No runs yet. Configure one and launch it — the offline demo costs nothing.</div>
        ) : null}
      </div>
    </div>
  );
}

/** The run's other actions, in a small menu so the row stays readable. */
function RunMenu({
  run,
  onRename,
  onKeep,
  onReport,
  onDropReports,
  onDelete,
}: {
  run: RunRow;
  onRename: () => void;
  onKeep: () => void;
  onReport: () => void;
  onDropReports: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const item = (label: string, action: () => void, danger = false, disabled = false) => (
    <button
      type="button"
      className={`menu__item${danger ? " menu__item--danger" : ""}`}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        setOpen(false);
        action();
      }}
    >
      {label}
    </button>
  );
  return (
    <span className="menu" onClick={(e) => e.stopPropagation()}>
      <button className="btn btn--small btn--ghost" aria-haspopup="menu" aria-expanded={open} aria-label={`More actions for ${run.run_id}`} onClick={() => setOpen((v) => !v)}>
        ⋯
      </button>
      {open ? (
        <span className="menu__list" role="menu" onMouseLeave={() => setOpen(false)}>
          {item("Rename / notes", onRename)}
          {item(run.kept ? "Stop keeping" : "☆ Keep (exempt from retention)", onKeep)}
          {item(run.reported ? "Regenerate reports" : "Render reports", onReport, false, run.live)}
          {item("Delete reports", onDropReports, true, run.live || !run.reported)}
          {item("Delete run…", onDelete, true, run.live)}
        </span>
      ) : null}
    </span>
  );
}
