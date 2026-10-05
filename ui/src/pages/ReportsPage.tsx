import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api } from "../api";
import { usePoll } from "../hooks/usePoll";
import type { RunRow } from "../types";

/** The run's own Markdown reports, rendered. Nothing is recomputed here. */
export function ReportsPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const runs = usePoll<RunRow[]>(api.runs, 10000);
  const [names, setNames] = useState<string[]>([]);
  const [name, setName] = useState("summary.md");
  const [html, setHtml] = useState("");

  useEffect(() => {
    if (!runId && runs.data?.length) navigate(`/reports/${runs.data[0].run_id}`, { replace: true });
  }, [runId, runs.data, navigate]);

  useEffect(() => {
    if (!runId) return;
    void api.reports(runId).then((r) => {
      setNames(r.reports);
      if (!r.reports.includes(name) && r.reports[0]) setName(r.reports[0]);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    if (!runId || !names.includes(name)) {
      setHtml("");
      return;
    }
    void api.report(runId, name).then((r) => setHtml(DOMPurify.sanitize(marked.parse(r.markdown) as string)));
  }, [runId, name, names]);

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h1>Reports</h1>
        <select className="input" style={{ width: 320 }} value={runId ?? ""} onChange={(e) => navigate(`/reports/${e.target.value}`)}>
          {(runs.data ?? []).map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {run.run_id} · {run.topic_title ?? run.experiment}
            </option>
          ))}
        </select>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 12, alignItems: "start" }}>
        <div className="card" style={{ padding: 6 }}>
          {names.length === 0 ? <div className="muted small" style={{ padding: 8 }}>No reports yet — they are written by <code>report</code>.</div> : null}
          {names.map((n) => (
            <button key={n} className={`tnode${n === name ? " tnode--sel" : ""}`} onClick={() => setName(n)}>
              <span className="name">{n}</span>
            </button>
          ))}
        </div>
        <div className="card card__body markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </div>
  );
}
