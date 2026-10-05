import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { usePoll } from "../hooks/usePoll";
import type { ConfigRow, Resolved } from "../types";
import { armColor, usd } from "../format";
import { Topics } from "../configure/Topics";

/**
 * A form over the lab's own YAML. Every change is a `--set a.b=value`, exactly
 * what the CLI takes, so a run started from this page is reproducible from a
 * terminal with the argv it shows. One exception, because a topic is content
 * rather than a setting: a new research topic is written to
 * `configs/topics.local.yaml`, and the run points at it with — again — a
 * `--set experiment.topics_file=...` (see configure/Topics.tsx).
 */

const PROFILES = [
  { key: "scholarly", title: "scholarly", body: "OpenAlex · Crossref · arXiv · PubMed", note: "authoritative = peer-reviewed" },
  { key: "web", title: "web", body: "Brave · Tavily", note: "authoritative = a primary statement, filing or posting" },
  { key: "balanced", title: "balanced", body: "scholarly + web, capped per provider", note: "peer-reviewed or primary" },
];

const LIMITS: { path: string; label: string; hint?: string }[] = [
  { path: "collection.max_research_agents", label: "max_research_agents" },
  { path: "collection.max_concurrent_agents", label: "max_concurrent_agents" },
  { path: "collection.max_search_rounds_per_agent", label: "max_search_rounds_per_agent" },
  { path: "collection.max_sources_per_subtopic", label: "max_sources_per_subtopic" },
  { path: "collection.max_depth", label: "max_depth" },
  { path: "experiment.cost_budget_usd", label: "cost_budget_usd", hint: "reserved before every call" },
];

const ARMS = [
  { id: "S0", text: "specialist · sources + trace" },
  { id: "C0", text: "prior only" },
  { id: "P0", text: "Pheasant, pinned to the sealed snapshot" },
  { id: "P1", text: "+ memory & steering" },
  { id: "P2", text: "tuned-search replay" },
];

// The page's own edits, per tab: a reload keeps the overrides (including the
// one that points at a topic you just added) instead of quietly dropping them.
const DRAFT_KEY = "pheasant-lab-configure-draft";

interface Draft {
  config?: string;
  overrides: Record<string, string>;
  topic?: string;
}

function loadDraft(config?: string): Draft {
  try {
    const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? "null") as Draft | null;
    if (saved && (!config || saved.config === config)) return saved;
  } catch {
    /* storage unavailable or corrupt */
  }
  return { overrides: {} };
}

function read(tree: Record<string, any> | undefined, path: string): unknown {
  return path.split(".").reduce<any>((node, key) => (node == null ? undefined : node[key]), tree);
}

export function ConfigurePage({ config, onConfig }: { config?: string; onConfig: (c: string) => void }) {
  const navigate = useNavigate();
  const configs = usePoll<ConfigRow[]>(api.configs, 60000);
  const selected = config ?? configs.data?.find((c) => c.default)?.path;
  const [draft] = useState(() => loadDraft(config));
  const [overrides, setOverrides] = useState<Record<string, string>>(draft.overrides);
  const [topic, setTopic] = useState<string | undefined>(draft.topic);
  const [resolved, setResolved] = useState<Resolved>();
  const [base, setBase] = useState<Resolved>();
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<Record<string, any> | null>(null);
  const [doctor, setDoctor] = useState<{ exit_code: number; output: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    // A draft made against another config file does not apply to this one.
    if (selected && draft.config && draft.config !== selected) {
      setOverrides({});
      setTopic(undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ config: selected, overrides, topic }));
    } catch {
      /* storage unavailable */
    }
  }, [selected, overrides, topic]);

  const set = useMemo(() => Object.entries(overrides).map(([k, v]) => `${k}=${v}`), [overrides]);

  useEffect(() => {
    if (!selected) return;
    void api.resolve({ config: selected, set: [] }).then(setBase).catch(() => undefined);
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    const handle = window.setTimeout(async () => {
      try {
        const next = await api.resolve({ config: selected, set });
        setResolved(next);
        setError(null);
        const projected = await api.plan({ config: selected, set, topic });
        setPlan(projected.projection);
      } catch (caught) {
        setError((caught as Error).message);
      }
    }, 350);
    return () => window.clearTimeout(handle);
  }, [selected, set, topic]);

  const change = (path: string, value: string | null) =>
    setOverrides((current) => {
      const next = { ...current };
      if (value === null || value === "") delete next[path];
      else next[path] = value;
      return next;
    });

  const tree = resolved?.resolved;
  const profile = String(read(tree, "collection.profile") ?? "scholarly");
  const arms = (read(tree, "arms") as string[] | undefined) ?? [];
  const pheasant = resolved?.pheasant;
  const isMock = pheasant?.transport === "mock";

  const launch = async (kind: "demo" | "pipeline") => {
    if (!selected) return;
    setBusy(kind);
    try {
      const started = await api.launch({ kind, config: selected, set, topic });
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const row = (await api.launches()).find((l) => l.launch_id === started.launch_id);
        if (row?.run_id) return navigate(`/live/${row.run_id}`);
        if (row && row.status !== "running") break;
        await new Promise((r) => setTimeout(r, 400));
      }
      navigate("/");
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runDoctor = async () => {
    if (!selected) return;
    setBusy("doctor");
    try {
      setDoctor(await api.doctor({ config: selected, set }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page page--fit" style={{ maxWidth: 1400 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h1>Configure</h1>
        <span className="muted">a form over the YAML — every change is a <code>--set</code></span>
      </div>
      {error ? <div className="toast toast--danger" style={{ boxShadow: "none" }}><span className="toast__icon">!</span><div><b>Refused</b><span className="soft">{error}</span></div></div> : null}
      <div className="cfg">
        <div className="fit-scroll" style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <div className="card">
            <div className="card__head">Experiment</div>
            <div className="card__body grid3">
              <div className="fld">
                <label>config file</label>
                <select className="input" value={selected ?? ""} onChange={(e) => { onConfig(e.target.value); setOverrides({}); setTopic(undefined); }}>
                  {(configs.data ?? []).map((c) => (
                    <option key={c.path} value={c.path}>{c.path}</option>
                  ))}
                </select>
              </div>
              <div className="fld">
                <label>topic</label>
                <select className="input" value={topic ?? ""} onChange={(e) => setTopic(e.target.value || undefined)}>
                  <option value="">first in topics file</option>
                  {(resolved?.topics ?? []).map((t) => (
                    <option key={t.id} value={t.id}>{t.title}</option>
                  ))}
                </select>
              </div>
              <div className="fld">
                <label>experiment.name</label>
                <input className="input" value={String(read(tree, "experiment.name") ?? "")} readOnly />
                <div className="h">seed {String(read(tree, "experiment.seed") ?? "—")}</div>
              </div>
            </div>
          </div>

          {selected ? (
            <Topics
              config={selected}
              set={set}
              selected={topic}
              onSelect={setTopic}
              onSaved={(override, topicId) => {
                const [path, value] = override.split("=", 2);
                change(path, value);
                setTopic(topicId);
              }}
            />
          ) : null}

          <div className="card">
            <div className="card__head">Collection profile <span className="sub">supplies defaults, never overrides: keys you set stay set</span></div>
            <div className="card__body grid3">
              {PROFILES.map((p) => (
                <button
                  key={p.key}
                  className={`prof${profile === p.key ? " prof--on" : ""}`}
                  onClick={() => change("collection.profile", base && read(base.resolved, "collection.profile") === p.key ? null : p.key)}
                >
                  <b>{p.title}</b>
                  {p.body}
                  <div className="muted" style={{ marginTop: 4 }}>{p.note}</div>
                </button>
              ))}
            </div>
            <div className="card__body grid3" style={{ paddingTop: 0 }}>
              {LIMITS.map((field) => {
                const isSet = field.path in overrides;
                return (
                  <div key={field.path} className={`fld${isSet ? " fld--set" : ""}`}>
                    <label>{field.label}</label>
                    <input
                      className="input"
                      type="number"
                      value={isSet ? overrides[field.path] : String(read(tree, field.path) ?? "")}
                      onChange={(e) => change(field.path, e.target.value)}
                    />
                    <div className="h">
                      {isSet ? (
                        <button className="btn btn--ghost btn--small" style={{ padding: 0 }} onClick={() => change(field.path, null)}>
                          set by you · reset
                        </button>
                      ) : (
                        field.hint ?? "from the file or the profile"
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card">
            <div className="card__head">Arms <span className="sub">isolation is enforced in <code>arms/base.py</code>; this chooses which run</span></div>
            <div className="card__body" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8 }}>
              {ARMS.map((arm) => {
                const on = arms.includes(arm.id);
                return (
                  <button
                    key={arm.id}
                    className={`armc${on ? "" : " armc--off"}`}
                    onClick={() => {
                      const next = on ? arms.filter((a) => a !== arm.id) : [...arms, arm.id];
                      const order = ARMS.map((a) => a.id).filter((id) => next.includes(id));
                      change("arms", `[${order.join(",")}]`);
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span className="pill" style={{ color: armColor(arm.id) }}>{arm.id}</span>
                      <span className="muted" style={{ marginLeft: "auto" }}>{on ? "on" : "off"}</span>
                    </span>
                    {arm.text}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="card">
            <div className="card__head">
              Pheasant region <span className="sub mono">{resolved?.source_files?.pheasant?.split("/").slice(-2).join("/")}</span>
            </div>
            <div className="card__body grid3">
              <div className="fld"><label>transport · endpoint</label><input className="input mono" readOnly value={isMock ? "mock (in-process)" : `${pheasant?.transport ?? ""} ${pheasant?.url ?? ""}`} /></div>
              <div className="fld"><label>knowledge_base</label><input className="input" readOnly value={pheasant?.knowledge_base ?? ""} /></div>
              <div className="fld"><label>source_name</label><input className="input" readOnly value={pheasant?.source_name ?? ""} /></div>
              {isMock ? (
                <div className={`fld${"mock_claim_seconds" in overrides ? " fld--set" : ""}`}>
                  <label>mock_claim_seconds</label>
                  <input
                    className="input"
                    type="number"
                    step="0.5"
                    value={overrides.mock_claim_seconds ?? String(pheasant?.mock_claim_seconds ?? 0)}
                    onChange={(e) => change("mock_claim_seconds", e.target.value === "0" ? null : e.target.value)}
                  />
                  <div className="h">above 0: behave like a fleet — syncs are queued, then claimed</div>
                </div>
              ) : null}
            </div>
            <div style={{ padding: "0 14px 12px" }}>
              <table className="table">
                <thead><tr><th>capability</th><th>tool</th><th>required</th></tr></thead>
                <tbody>
                  {Object.entries(pheasant?.capabilities ?? {}).map(([name, spec]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td className="mono">{spec.tool}</td>
                      <td>{spec.required ? <span className="pill pill--accent">required</span> : <span className="pill">optional</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="sticky fit-scroll">
          <div className="card">
            <div className="card__head">Plan <span className="sub">worst case, no model or ingest call</span></div>
            <div className="card__body">
              <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em" }}>{usd(Number(plan?.projected_total_usd ?? 0))}</span>
                <span className="muted">of {usd(Number(plan?.budget_usd ?? read(tree, "experiment.cost_budget_usd") ?? 0))}</span>
                {plan ? (
                  <span className={`pill ${plan.fits_budget ? "pill--accent" : "pill--danger"}`} style={{ marginLeft: "auto" }}>
                    {plan.fits_budget ? "fits" : "over budget"}
                  </span>
                ) : null}
              </div>
              {plan ? (
                <dl className="kv" style={{ marginTop: 8 }}>
                  <dt>planning</dt><dd>{usd(Number(plan.planning_usd), 4)}</dd>
                  <dt>collection</dt><dd>{usd(Number(plan.collection_usd), 4)} + search APIs {usd(Number(plan.search_api_usd), 4)}</dd>
                  <dt>benchmark</dt><dd>{usd(Number(plan.benchmark_usd), 4)}</dd>
                  <dt>evaluation</dt><dd>{usd(Number(plan.evaluation_usd), 4)}</dd>
                  <dt>volume</dt><dd>{String(plan.searches)} provider searches · {String(plan.questions)} questions · {String(plan.answers)} answers · {String(plan.mcp_search_calls)} MCP searches</dd>
                </dl>
              ) : <span className="muted">projecting…</span>}
            </div>
          </div>

          <div className="card">
            <div className="card__head">
              Preflight · doctor <span className="sub">refuses before any spend</span>
              <div className="r">
                <button className="btn btn--small" onClick={runDoctor} disabled={busy === "doctor"}>
                  {busy === "doctor" ? <span className="spinner" /> : null} Run doctor
                </button>
              </div>
            </div>
            <div className="card__body">
              {doctor ? (
                <>
                  <span className={`pill ${doctor.exit_code === 0 ? "pill--accent" : "pill--danger"}`}>
                    {doctor.exit_code === 0 ? "PASS" : "FAIL"}
                  </span>
                  <pre className="yaml" style={{ marginTop: 8 }}>{doctor.output.trim()}</pre>
                </>
              ) : (
                <span className="muted">Checks models, providers, the region's tools and every mapped argument against its schema.</span>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card__head">
              Resolved <span className="sub">{Object.keys(overrides).length} override(s)</span>
              <div className="r">
                {Object.keys(overrides).length || topic ? (
                  <button className="btn btn--ghost btn--small" onClick={() => { setOverrides({}); setTopic(undefined); }}>
                    reset all
                  </button>
                ) : null}<span className="mono muted" title={resolved?.digest}>{resolved?.digest.slice(7, 19)}…</span></div>
            </div>
            <div className="card__body">
              <pre className="yaml">
                {`pheasant-lab run --config ${selected ?? ""}${set.map((s) => ` \\\n  --set ${s}`).join("")}${topic ? ` \\\n  --topic ${topic}` : ""}`}
              </pre>
              {base && resolved && base.digest !== resolved.digest ? (
                <div className="pill pill--warn" style={{ marginTop: 8, whiteSpace: "normal" }}>
                  The digest differs from the file's: a new run, not comparable with one started from the file as is.
                </div>
              ) : null}
              {resolved?.unresolved_env.length ? (
                <div className="pill pill--danger" style={{ marginTop: 8, whiteSpace: "normal" }}>
                  unset environment: {resolved.unresolved_env.join(", ")}
                </div>
              ) : null}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" style={{ flex: 1, justifyContent: "center" }} disabled={!!busy || !resolved} onClick={() => void launch("demo")}>
              {busy === "demo" ? <span className="spinner" /> : null} Offline demo
            </button>
            <button className="btn btn--primary" style={{ flex: 2, justifyContent: "center" }} disabled={!!busy || !resolved} onClick={() => void launch("pipeline")}>
              {busy === "pipeline" ? <span className="spinner" /> : null} Launch run ▸
            </button>
          </div>
          <div className="muted small">
            <b>Offline demo</b>: fixtures and the mock region, free. <b>Launch run</b>: <code>pheasant-lab run</code> — collect → freeze → evaluate → replay → report → verify, resuming any stage that crashes and stopping at the first refusal. It runs detached: closing the console does not stop it, and Runs offers <b>Resume</b> for one that was interrupted.
          </div>
        </div>
      </div>
    </div>
  );
}
