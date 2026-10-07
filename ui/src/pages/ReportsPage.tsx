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
  }, [runId, view]);

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
      </div>
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
