import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api } from "../api";
import { usePoll } from "../hooks/usePoll";
import type { RunRow } from "../types";
import { Traces } from "../reports/Traces";

/**
 * The run's own Markdown reports, rendered, and every agent's full trace.
 * Nothing is recomputed here. The view is in the path (``/reports/<run>`` or
 * ``/reports/<run>/traces/<actor>``), so a reload lands where it was.
 */
export function ReportsPage({ view = "reports" }: { view?: "reports" | "traces" }) {
  const { runId, actor } = useParams();
  const navigate = useNavigate();
  const runs = usePoll<RunRow[]>(api.runsQuiet, 10000);
  const [names, setNames] = useState<string[]>([]);
  const [name, setName] = useState("summary.md");
  const [html, setHtml] = useState("");
  const [loadingReport, setLoadingReport] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const current = runs.data?.find((run) => run.run_id === runId);

  // Reports are derived from the raw trace: rendering them again is a
  // `report` launch, and deleting them loses nothing a re-render cannot bring back.
  const regenerate = async () => {
    if (!runId) return;
    setNotice(null);
    try {
      const launch = await api.generateReports(runId);
      setNotice(`Rendering reports (launch ${launch.launch_id})…`);
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((r) => setTimeout(r, 1000));
        const row = (await api.launches()).find((l) => l.launch_id === launch.launch_id);
        if (row && row.status !== "running") {
          setNotice(row.status === "succeeded" || row.status === "completed_with_findings" ? "Reports rendered." : `Report launch ${row.status.replace(/_/g, " ")}.`);
          break;
        }
      }
      setEpoch((n) => n + 1);
    } catch (caught) {
      setNotice((caught as Error).message);
    }
  };
  const dropReports = async () => {
    if (!runId || !window.confirm("Delete this run's rendered reports? Regenerate renders them again from the raw trace.")) return;
    try {
      await api.deleteReports(runId);
      setNotice("Reports deleted.");
      setEpoch((n) => n + 1);
    } catch (caught) {
      setNotice((caught as Error).message);
    }
  };

  useEffect(() => {
    if (!runId && runs.data?.length)
      navigate(`/reports/${runs.data[0].run_id}${view === "traces" ? "/traces" : ""}`, { replace: true });
  }, [runId, runs.data, navigate, view]);

  useEffect(() => {
    if (!runId || view !== "reports") return;
    void api
      .reports(runId)
      .then((r) => {
        setNames(r.reports);
        if (!r.reports.includes(name) && r.reports[0]) setName(r.reports[0]);
      })
      .catch(() => setNames([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, view, epoch]);

  useEffect(() => {
    if (!runId || !names.includes(name)) {
      setHtml("");
      return;
    }
    setLoadingReport(true);
    void api
      .report(runId, name)
      .then((r) => setHtml(DOMPurify.sanitize(marked.parse(r.markdown) as string)))
      .catch((caught: Error) => setHtml(`<p class="error">${DOMPurify.sanitize(caught.message)}</p>`))
      .finally(() => setLoadingReport(false));
  }, [runId, name, names]);

  return (
    <div className="page page--fit" style={view === "traces" ? { maxWidth: 1560 } : undefined}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h1>Reports</h1>
        <select
          className="input"
          style={{ width: 320 }}
          value={runId ?? ""}
          onChange={(e) => navigate(`/reports/${e.target.value}${view === "traces" ? "/traces" : ""}`)}
        >
          {(runs.data ?? []).map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {run.label ? `${run.label} · ` : ""}
              {run.run_id} · {run.topic_title ?? run.experiment}
            </option>
          ))}
        </select>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={view === "reports"} className={`tab${view === "reports" ? " tab--on" : ""}`} onClick={() => runId && navigate(`/reports/${runId}`)}>
            Reports
          </button>
          <button role="tab" aria-selected={view === "traces"} className={`tab${view === "traces" ? " tab--on" : ""}`} onClick={() => runId && navigate(`/reports/${runId}/traces`)}>
            Agent traces
          </button>
        </div>
        {view === "reports" && runId ? (
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button className="btn btn--small" disabled={current?.live} onClick={() => void regenerate()}>
              {names.length ? "Regenerate reports" : "Render reports"}
            </button>
            <button className="btn btn--small btn--danger" disabled={current?.live || names.length === 0} onClick={() => void dropReports()}>
              Delete reports
            </button>
          </div>
        ) : null}
      </div>
      {notice ? <div className="pill pill--info" style={{ whiteSpace: "normal" }}>{notice}</div> : null}
      {view === "traces" ? (
        runId ? <Traces runId={runId} actorId={actor} /> : <div className="card empty">No runs yet.</div>
      ) : (
      <div className="reports">
        <div className="card fit-scroll" style={{ padding: 6 }}>
          {names.length === 0 ? <div className="muted small" style={{ padding: 8 }}>No reports yet — they are written by <code>report</code>.</div> : null}
          {names.map((n) => (
            <button key={n} className={`tnode${n === name ? " tnode--sel" : ""}`} onClick={() => setName(n)}>
              <span className="name">{n}</span>
            </button>
          ))}
        </div>
        {loadingReport && !html ? (
          <div className="card card__body fit-scroll">
            {Array.from({ length: 8 }, (_, i) => <div key={i} className="skel skel-row" style={{ width: `${95 - ((i * 13) % 40)}%`, marginLeft: 0 }} />)}
          </div>
        ) : (
          <div className={`card card__body markdown fit-scroll${loadingReport ? " is-refreshing" : ""}`} dangerouslySetInnerHTML={{ __html: html }} />
        )}
      </div>
      )}
    </div>
  );
}
