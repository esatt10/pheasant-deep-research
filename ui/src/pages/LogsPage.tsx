import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { usePoll } from "../hooks/usePoll";
import { ago, bytes } from "../format";
import type { LogInventory, LogPage, RetentionAction, RetentionPolicy } from "../types";

/**
 * Logging control: what the console has accumulated, reading it, deleting
 * it, and how long it is kept (console/logs.py).
 *
 * A run's raw trace is kept whole or deleted whole - rule 5 - so the choices
 * here are launch logs, a run's rebuildable projection, or the run itself.
 * Nothing a launch is still writing can be deleted, and every deletion lands
 * in the audit list at the bottom.
 *
 * Run *logging settings* (level, format, a log file, MCP transcript bodies)
 * are configuration, so they live on Configure as `--set` overrides like
 * every other setting; this page links there.
 */

type Selection = { kind: "launch"; id: string; live: boolean } | { kind: "run"; id: string; path: string };

type Pending =
  | { kind: "launch"; id: string }
  | { kind: "run"; id: string }
  | { kind: "projection"; id: string }
  | { kind: "apply"; plan: RetentionAction[] };

const CATEGORY_COLOR: Record<string, string> = {
  trace: "var(--r-res)",
  projection: "var(--r-plan)",
  reports: "var(--accent)",
  metrics: "var(--r-aud)",
  benchmark: "var(--r-orch)",
  supervisor: "var(--muted)",
  integrity: "var(--border-strong)",
  logs: "var(--r-pheasant)",
  other: "var(--bg-active)",
};

const NUMBER_FIELDS: { key: keyof RetentionPolicy; label: string; unit: string; hint: string }[] = [
  { key: "launch_log_days", label: "Launch logs older than", unit: "days", hint: "finished launches only" },
  { key: "max_launch_logs", label: "Keep at most", unit: "launch logs", hint: "newest kept" },
  { key: "projection_days", label: "Projections of runs idle", unit: "days", hint: "rebuilt by `replay`" },
  { key: "run_days", label: "Whole runs idle for", unit: "days", hint: "deletes the trace" },
  { key: "max_runs", label: "Keep at most", unit: "runs", hint: "newest kept" },
];

export function LogsPage() {
  const inventory = usePoll<LogInventory>(api.logsQuiet, (data) =>
    data?.launches.some((l) => l.status === "running") ? 3000 : 10000,
  );
  const [selection, setSelection] = useState<Selection | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const data = inventory.data;

  // Esc clears the selection, as it does on Live.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending && !(event.target as HTMLElement).closest?.("input, select, textarea")) {
        setSelection(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending]);

  const confirm = async () => {
    if (!pending) return;
    if (pending.kind === "launch") {
      const row = await api.deleteLaunchLog(pending.id);
      setNotice(`Deleted launch log ${pending.id} · freed ${bytes(row.freed_bytes)}`);
      if (selection?.kind === "launch" && selection.id === pending.id) setSelection(null);
    } else if (pending.kind === "run") {
      const row = await api.deleteRun(pending.id);
      setNotice(`Deleted ${pending.id} · freed ${bytes(row.freed_bytes)}`);
      if (selection?.kind === "run" && selection.id === pending.id) setSelection(null);
    } else if (pending.kind === "projection") {
      const row = await api.dropProjection(pending.id);
      setNotice(`Dropped the projection of ${pending.id} · freed ${bytes(row.freed_bytes)} · \`pheasant-lab replay\` rebuilds it`);
    } else {
      const result = await api.applyRetention();
      const done = result.applied.filter((row) => !row.skipped);
      const freed = done.reduce((sum, row) => sum + (row.freed_bytes ?? 0), 0);
      setNotice(`Retention applied: ${done.length} item(s), ${bytes(freed)} freed${result.applied.length > done.length ? `, ${result.applied.length - done.length} skipped` : ""}`);
    }
    await inventory.refresh();
  };

  if (inventory.loading) {
    return (
      <div className="page page--fit">
        <h1>Logs</h1>
        <div className="card">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="skel skel-row" style={{ width: `${90 - i * 9}%` }} />
          ))}
        </div>
      </div>
    );
  }
  if (!data) return <div className="empty">{inventory.error?.message ?? "No log inventory."}</div>;

  return (
    <div className="page page--fit page--wide">
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1>Logs</h1>
        <span className="muted small">
          {bytes(data.totals.runs)} in {data.runs.length} run(s), {bytes(data.totals.projections)} of it rebuildable projections ·{" "}
          {bytes(data.totals.launch_logs)} in {data.launches.length} launch log(s)
        </span>
        <Link to="/configure#run-logging" className="btn btn--small" style={{ marginLeft: "auto", textDecoration: "none" }}>
          Run logging settings →
        </Link>
        <button className="btn btn--small" onClick={() => void inventory.refresh()}>
          ↻ Refresh
        </button>
      </div>
      {notice ? (
        <div className="pill pill--accent" style={{ whiteSpace: "normal", alignSelf: "flex-start" }}>
          {notice}
          <button className="btn btn--ghost btn--small" onClick={() => setNotice(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ) : null}

      <div className="logs">
        <div className="logs__lists fit-scroll">
          <div className="card">
            <div className="card__head">
              Launch logs <span className="sub">stdout of every command the console started</span>
            </div>
            <table className="table">
              <tbody>
                {data.launches.map((launch) => {
                  const on = selection?.kind === "launch" && selection.id === launch.launch_id;
                  return (
                    <tr
                      key={launch.launch_id}
                      className={`clickable${on ? " row--sel" : ""}`}
                      onClick={() =>
                        setSelection(on ? null : { kind: "launch", id: launch.launch_id, live: launch.status === "running" })
                      }
                    >
                      <td style={{ width: 120 }}>
                        <span className={`pill${launch.status === "running" ? " pill--info" : launch.status === "succeeded" ? " pill--accent" : ""}`}>
                          {launch.status === "running" ? <span className="spinner" /> : null}
                          {launch.status.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td>
                        <b>{launch.kind}</b> <span className="muted mono">{launch.launch_id}</span>
                        <div className="muted small">
                          {launch.run_id ?? "no run"} · {ago(launch.finished_at ?? launch.started_at)}
                        </div>
                      </td>
                      <td className="mono muted" style={{ textAlign: "right" }}>{bytes(launch.size_bytes)}</td>
                      <td style={{ textAlign: "right", width: 70 }}>
                        <button
                          className="btn btn--small btn--danger"
                          disabled={!launch.deletable}
                          title={launch.deletable ? "Delete this launch log and its record" : "Stop the launch first"}
                          onClick={(event) => {
                            event.stopPropagation();
                            setPending({ kind: "launch", id: launch.launch_id });
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {data.launches.length === 0 ? <div className="empty">Nothing launched from this console yet.</div> : null}
          </div>

          <div className="card">
            <div className="card__head">
              Runs <span className="sub">kept whole or deleted whole; the projection alone is rebuildable</span>
            </div>
            {data.runs.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                expanded={expanded === run.run_id}
                onToggle={() => setExpanded(expanded === run.run_id ? null : run.run_id)}
                selectedPath={selection?.kind === "run" && selection.id === run.run_id ? selection.path : null}
                onOpen={(path) => setSelection({ kind: "run", id: run.run_id, path })}
                onKeep={async (keep) => {
                  await api.keepRun(run.run_id, keep);
                  await inventory.refresh();
                }}
                onDropProjection={() => setPending({ kind: "projection", id: run.run_id })}
                onDelete={() => setPending({ kind: "run", id: run.run_id })}
              />
            ))}
            {data.runs.length === 0 ? <div className="empty">No runs under {data.output_root}.</div> : null}
          </div>

          <Retention
            policy={data.policy}
            onSaved={() => void inventory.refresh()}
            onApply={(plan) => setPending({ kind: "apply", plan })}
          />

          <div className="card">
            <div className="card__head">
              Deletion audit <span className="sub">.console/retention-log.jsonl</span>
            </div>
            <table className="table">
              <tbody>
                {data.audit.map((row, index) => (
                  <tr key={`${row.at}-${index}`}>
                    <td className="muted" style={{ width: 90 }}>{ago(row.at)}</td>
                    <td>
                      <span className="pill">{row.kind.replace("_", " ")}</span> <span className="mono">{row.target}</span>
                      <div className="muted small">{row.reason}</div>
                    </td>
                    <td className="mono muted" style={{ textAlign: "right" }}>{bytes(row.freed_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.audit.length === 0 ? <div className="empty">Nothing deleted yet.</div> : null}
          </div>
        </div>

        <div className="logs__viewer">
          {selection ? (
            <LogViewer selection={selection} onClose={() => setSelection(null)} />
          ) : (
            <div className="card empty" style={{ height: "100%", display: "grid", placeItems: "center" }}>
              <div>
                Select a launch log, or a file inside a run, to read it here.
                <div className="small" style={{ marginTop: 6 }}>
                  Filter by text or level, follow a running launch, page through the rest. <kbd>Esc</kbd> deselects.
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {pending ? (
        <ConfirmDialog
          title={
            pending.kind === "launch"
              ? "Delete this launch log?"
              : pending.kind === "run"
                ? "Delete this whole run?"
                : pending.kind === "projection"
                  ? "Drop this run's projection?"
                  : `Apply retention to ${pending.plan.length} item(s)?`
          }
          body={
            pending.kind === "run" ? (
              <>
                Removes <code>{pending.id}</code> - its raw trace, receipts, benchmark, metrics and reports. It cannot be
                resumed, replayed or verified afterwards.
              </>
            ) : pending.kind === "projection" ? (
              <>
                Removes <code>{pending.id}/projections/</code>. The raw trace is untouched; <code>pheasant-lab replay --run {pending.id}</code> rebuilds it.
              </>
            ) : pending.kind === "launch" ? (
              <>Removes the log file and launch record. The run it started, if any, is untouched.</>
            ) : (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {pending.plan.map((row) => (
                  <li key={`${row.kind}-${row.target}`}>
                    <b>{row.kind.replace("_", " ")}</b> <span className="mono">{row.target}</span> - {row.reason}
                  </li>
                ))}
              </ul>
            )
          }
          action={pending.kind === "apply" ? "Apply" : pending.kind === "projection" ? "Drop" : "Delete"}
          confirmText={pending.kind === "run" ? pending.id : undefined}
          onConfirm={confirm}
          onClose={() => setPending(null)}
        />
      ) : null}
    </div>
  );
}

function RunRow({
  run,
  expanded,
  onToggle,
  selectedPath,
  onOpen,
  onKeep,
  onDropProjection,
  onDelete,
}: {
  run: LogInventory["runs"][number];
  expanded: boolean;
  onToggle: () => void;
  selectedPath: string | null;
  onOpen: (path: string) => void;
  onKeep: (keep: boolean) => Promise<void>;
  onDropProjection: () => void;
  onDelete: () => void;
}) {
  const categories = Object.entries(run.categories).filter(([, size]) => size > 0).sort((a, b) => b[1] - a[1]);
  const logFiles = run.files.filter((f) => f.path.startsWith("logs/") || f.path.startsWith("supervisor/") || f.path.startsWith("raw/"));
  const otherFiles = run.files.filter((f) => !logFiles.includes(f));
  return (
    <div className={`logrun${expanded ? " logrun--open" : ""}`}>
      <div className="logrun__line" onClick={onToggle} role="button" aria-expanded={expanded} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onToggle()}>
        <span className="wf__caret">{expanded ? "▾" : "▸"}</span>
        <div style={{ minWidth: 0 }}>
          <div className="mono">{run.run_id}</div>
          <div className="muted small">
            {ago(run.updated_at)}
            {run.live ? <span className="pill pill--info" style={{ marginLeft: 6 }}><span className="spinner" /> running</span> : null}
            {run.reported ? <span className="pill" style={{ marginLeft: 6 }}>reported</span> : null}
            {run.kept ? <span className="pill pill--accent" style={{ marginLeft: 6 }}>kept</span> : null}
          </div>
        </div>
        <div className="sizebar" title={categories.map(([c, s]) => `${c}: ${bytes(s)}`).join("\n")}>
          {categories.map(([category, size]) => (
            <i key={category} style={{ flex: size, background: CATEGORY_COLOR[category] ?? CATEGORY_COLOR.other }} />
          ))}
        </div>
        <span className="mono muted" style={{ textAlign: "right" }}>{bytes(run.size_bytes)}</span>
      </div>
      {expanded ? (
        <div className="logrun__body">
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            <button className={`btn btn--small${run.kept ? " btn--primary" : ""}`} onClick={() => void onKeep(!run.kept)} title="A kept run is exempt from every retention rule">
              {run.kept ? "★ Kept" : "☆ Keep"}
            </button>
            <button className="btn btn--small" disabled={run.live || !run.categories.projection} onClick={onDropProjection}>
              Drop projection ({bytes(run.categories.projection ?? 0)})
            </button>
            <Link className="btn btn--small" to={`/reports/${run.run_id}`} style={{ textDecoration: "none" }}>
              Reports
            </Link>
            <button className="btn btn--small btn--danger" disabled={run.live} onClick={onDelete} style={{ marginLeft: "auto" }} title={run.live ? "Stop its launch first" : "Delete the whole run"}>
              Delete run
            </button>
          </div>
          <div className="legend">
            {categories.map(([category, size]) => (
              <span key={category} className="small">
                <i style={{ background: CATEGORY_COLOR[category] ?? CATEGORY_COLOR.other }} /> {category} {bytes(size)}
              </span>
            ))}
          </div>
          {[
            { title: "Logs and traces", files: logFiles },
            { title: "Everything else readable", files: otherFiles },
          ].map((group) =>
            group.files.length ? (
              <div key={group.title} style={{ marginTop: 8 }}>
                <div className="eyebrow">{group.title}</div>
                {group.files.map((file) => (
                  <button
                    key={file.path}
                    className={`tnode${selectedPath === file.path ? " tnode--sel" : ""}`}
                    onClick={() => onOpen(file.path)}
                  >
                    <span className="name mono">{file.path}</span>
                    <span className="meta">{bytes(file.size_bytes)}</span>
                  </button>
                ))}
              </div>
            ) : null,
          )}
        </div>
      ) : null}
    </div>
  );
}

const PAGE = 400;

function LogViewer({ selection, onClose }: { selection: Selection; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [level, setLevel] = useState("");
  const [follow, setFollow] = useState(selection.kind === "launch" && selection.live);
  const [page, setPage] = useState<LogPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const key = selection.kind === "launch" ? selection.id : `${selection.id}/${selection.path}`;

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query), 220);
    return () => window.clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    setPage(null);
    setLoading(true);
    setFollow(selection.kind === "launch" && selection.live);
  }, [key, selection]);

  const load = useCallback(
    async (options: { offset?: number; tail?: boolean; quiet?: boolean; append?: "before" | "after" }) => {
      try {
        const request = { q: debounced || undefined, level: level || undefined, limit: PAGE, ...options };
        const next =
          selection.kind === "launch"
            ? await api.readLaunchLog(selection.id, request)
            : await api.readRunFile(selection.id, selection.path, request);
        setPage((current) => {
          if (!current || !options.append) return next;
          const lines =
            options.append === "before" ? [...next.lines, ...current.lines] : [...current.lines, ...next.lines];
          return { ...next, lines, offset: Math.min(current.offset, next.offset) };
        });
        setError(null);
      } catch (caught) {
        setError((caught as Error).message);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, debounced, level],
  );

  // Open at the end: the newest lines are what a person opening a log wants.
  useEffect(() => {
    void load({ tail: true });
  }, [load]);

  useEffect(() => {
    if (!follow) return;
    const timer = window.setInterval(() => void load({ tail: true, quiet: true }), 1500);
    return () => window.clearInterval(timer);
  }, [follow, load]);

  useEffect(() => {
    if (follow && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [page, follow]);

  const needle = debounced.toLowerCase();
  const rendered = useMemo(
    () =>
      (page?.lines ?? []).map((line) => {
        const upper = line.text.slice(0, 48).toUpperCase();
        const tone = /ERROR|CRITICAL|TRACEBACK|REFUSED/.test(upper) ? "danger" : /WARN/.test(upper) ? "warn" : "";
        if (!needle) return { ...line, tone, parts: [line.text] };
        const parts: (string | JSX.Element)[] = [];
        let rest = line.text;
        let index = rest.toLowerCase().indexOf(needle);
        let k = 0;
        while (index >= 0) {
          parts.push(rest.slice(0, index), <mark key={k++}>{rest.slice(index, index + needle.length)}</mark>);
          rest = rest.slice(index + needle.length);
          index = rest.toLowerCase().indexOf(needle);
        }
        parts.push(rest);
        return { ...line, tone, parts };
      }),
    [page, needle],
  );
  const first = page?.lines[0]?.n;

  return (
    <div className="card logview">
      <div className="card__head">
        <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{key}</span>
        <div className="r">
          {page ? <span className="muted small">{page.matched} of {page.total_lines} lines · {bytes(page.size_bytes)}</span> : null}
          <button className="btn btn--ghost btn--small" onClick={onClose} aria-label="Close the viewer" title="Close (Esc)">
            ×
          </button>
        </div>
      </div>
      <div className="logview__tools">
        <input className="input" placeholder="Filter lines…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ flex: 1 }} aria-label="Filter lines" />
        <select className="input" value={level} onChange={(e) => setLevel(e.target.value)} style={{ width: 130 }} aria-label="Minimum level">
          <option value="">all levels</option>
          <option value="INFO">info and up</option>
          <option value="WARNING">warnings and up</option>
          <option value="ERROR">errors only</option>
        </select>
        <label className="small soft" style={{ display: "flex", alignItems: "center", gap: 4, whiteSpace: "nowrap" }}>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow
        </label>
        {query || level ? (
          <button className="btn btn--ghost btn--small" onClick={() => { setQuery(""); setLevel(""); }}>
            Clear
          </button>
        ) : null}
      </div>
      <div className="logview__body" ref={body}>
        {loading ? (
          Array.from({ length: 10 }, (_, i) => <div key={i} className="skel skel-row" style={{ width: `${40 + ((i * 37) % 55)}%` }} />)
        ) : error ? (
          <div className="empty">{error}</div>
        ) : (
          <>
            {page && page.offset > 0 ? (
              <button className="btn btn--small logview__more" onClick={() => void load({ offset: Math.max(0, page.offset - PAGE), append: "before" })}>
                ↑ {page.offset} earlier line(s)
              </button>
            ) : null}
            {rendered.map((line) => (
              <div key={line.n} className={`logline${line.tone ? ` logline--${line.tone}` : ""}`}>
                <span className="logline__n">{line.n}</span>
                <span className="logline__t">{line.parts}</span>
              </div>
            ))}
            {rendered.length === 0 ? <div className="empty">{debounced || level ? "No line matches the filter." : "This log is empty."}</div> : null}
            {page && first != null && page.has_more && !follow ? (
              <button className="btn btn--small logview__more" onClick={() => void load({ offset: page.offset + page.lines.length, append: "after" })}>
                ↓ more
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function Retention({
  policy,
  onSaved,
  onApply,
}: {
  policy: RetentionPolicy;
  onSaved: () => void;
  onApply: (plan: RetentionAction[]) => void;
}) {
  const [draft, setDraft] = useState<RetentionPolicy>(policy);
  const [plan, setPlan] = useState<RetentionAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(policy);

  useEffect(() => setDraft(policy), [policy]);

  // Preview as you type: what this draft would delete right now.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      api
        .previewRetention(draft)
        .then((result) => {
          setPlan(result.plan);
          setError(null);
        })
        .catch((caught: Error) => setError(caught.message));
    }, 300);
    return () => window.clearTimeout(handle);
  }, [draft]);

  const field = (key: keyof RetentionPolicy, raw: string) =>
    setDraft((current) => ({ ...current, [key]: raw === "" ? null : Number(raw) }));

  return (
    <div className="card">
      <div className="card__head">
        Retention <span className="sub">blank keeps forever · live runs and kept runs are never touched</span>
      </div>
      <div className="card__body">
        <div className="grid3">
          {NUMBER_FIELDS.map((f) => (
            <div key={f.key} className={`fld${draft[f.key] != null ? " fld--set" : ""}`}>
              <label>
                {f.label} <span className="muted">({f.unit})</span>
              </label>
              <input
                className="input"
                type="number"
                min={f.unit === "days" ? 0 : 1}
                step={f.unit === "days" ? 0.5 : 1}
                placeholder="forever"
                value={draft[f.key] == null ? "" : String(draft[f.key])}
                onChange={(e) => field(f.key, e.target.value)}
              />
              <div className="h">{f.hint}</div>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 10, flexWrap: "wrap" }}>
          <label className="small soft" style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={draft.protect_reported} onChange={(e) => setDraft({ ...draft, protect_reported: e.target.checked })} />
            never delete a run that has reports
          </label>
          <label className="small soft" style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={draft.auto_apply} onChange={(e) => setDraft({ ...draft, auto_apply: e.target.checked })} />
            apply automatically every 10 minutes while the console runs
          </label>
        </div>
        {error ? <p className="error small">{error}</p> : null}
        <div className="eyebrow" style={{ marginTop: 12 }}>
          Would delete now {plan ? `· ${plan.length} item(s)` : ""}
        </div>
        {plan == null ? (
          <div className="skel skel-row" style={{ marginLeft: 0 }} />
        ) : plan.length === 0 ? (
          <div className="muted small">Nothing.</div>
        ) : (
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18, maxHeight: 140, overflow: "auto" }}>
            {plan.map((row) => (
              <li key={`${row.kind}-${row.target}`}>
                <b>{row.kind.replace("_", " ")}</b> <span className="mono">{row.target}</span> <span className="muted">- {row.reason}</span>
              </li>
            ))}
          </ul>
        )}
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <button
            className="btn btn--small btn--primary"
            disabled={!dirty || saving}
            onClick={async () => {
              setSaving(true);
              try {
                await api.setRetention(draft);
                onSaved();
              } catch (caught) {
                setError((caught as Error).message);
              } finally {
                setSaving(false);
              }
            }}
          >
            {saving ? <span className="spinner" /> : null} Save policy
          </button>
          {dirty ? (
            <button className="btn btn--small" onClick={() => setDraft(policy)}>
              Revert
            </button>
          ) : null}
          <button
            className="btn btn--small btn--danger"
            style={{ marginLeft: "auto" }}
            disabled={dirty || !plan?.length}
            title={dirty ? "Save the policy first: apply runs the saved one" : undefined}
            onClick={() => plan && onApply(plan)}
          >
            Apply now…
          </button>
        </div>
      </div>
    </div>
  );
}
