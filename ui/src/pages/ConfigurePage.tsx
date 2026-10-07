import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, type SettingsPatch } from "../api";
import { usePoll } from "../hooks/usePoll";
import type { Catalog, CatalogField, ConfigRow, Settings } from "../types";
import { armColor, usd } from "../format";
import { Topics } from "../configure/Topics";
import { SettingField, useDraftText, type Commit } from "../configure/fields";
import { Connections } from "../configure/Connections";
import { Budget } from "../configure/Budget";
import { Models } from "../configure/Models";

/**
 * A form over the lab's own YAML, and over the one settings draft the console
 * keeps - the same draft its MCP tools edit, so a change an agent makes is the
 * change this page shows. Every change is still a `--set a.b=value`, exactly
 * what the CLI takes, and the page shows the argv a launch would run.
 *
 * Every field is explained by the catalog the server derives from the
 * configuration models (`?` beside each label): what it means, what it takes,
 * its range and default, and - for the agent roles - the recommended model
 * and reasoning level.
 */

const PROFILES = [
  { key: "scholarly", title: "scholarly", body: "OpenAlex · Crossref · arXiv · PubMed", note: "authoritative = peer-reviewed" },
  { key: "web", title: "web", body: "Brave · Tavily", note: "authoritative = a primary statement, filing or posting" },
  { key: "balanced", title: "balanced", body: "scholarly + web, capped per provider", note: "peer-reviewed or primary" },
];

const ARMS = [
  { id: "S0", text: "specialist · sources + trace" },
  { id: "C0", text: "prior only" },
  { id: "P0", text: "Pheasant, pinned to the sealed snapshot" },
  { id: "P1", text: "+ memory & steering" },
  { id: "P2", text: "tuned-search replay" },
];

const COLLECTION_KEYS = [
  "collection.max_research_agents",
  "collection.max_concurrent_agents",
  "collection.max_search_rounds_per_agent",
  "collection.max_sources_per_subtopic",
  "collection.max_depth",
  "collection.providers",
];

/** The shared draft: read, polled, and changed through one function. */
function useSettings() {
  const [settings, setSettings] = useState<Settings>();
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(0);
  const revision = useRef(-1);

  const adopt = useCallback((next: Settings) => {
    revision.current = next.draft.revision;
    setSettings(next);
  }, []);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    try {
      const next = await api.settings();
      // Adopt another surface's change (an MCP tool, another tab) only when
      // nothing of ours is on its way: our own reply is newer.
      if (!inflight.current && next.draft.revision !== revision.current) adopt(next);
    } catch {
      /* the next poll tries again */
    }
  }, [adopt]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const update = useCallback(
    async (patch: SettingsPatch) => {
      inflight.current += 1;
      try {
        adopt(await api.updateSettings(patch));
        setError(null);
      } catch (caught) {
        setError((caught as Error).message);
      } finally {
        inflight.current -= 1;
      }
    },
    [adopt],
  );

  return { settings, setSettings: adopt, update, error, setError, refresh };
}

export function ConfigurePage({ onConfig }: { config?: string; onConfig: (c: string) => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const configs = usePoll<ConfigRow[]>(api.configs, 60000);
  const { settings, setSettings, update, error, setError } = useSettings();
  const [catalog, setCatalog] = useState<Catalog>();
  const [plan, setPlan] = useState<Record<string, any> | null>(null);
  const [doctor, setDoctor] = useState<{ exit_code: number; output: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [section, setSection] = useState("all");
  const [advanced, setAdvanced] = useState(false);

  const draft = settings?.draft;
  const resolved = settings?.resolved ?? undefined;
  const values = resolved?.values ?? {};
  const overrides = draft?.overrides ?? {};
  const selected = draft?.config;
  const set = useMemo(() => Object.entries(overrides).map(([k, v]) => `${k}=${v}`), [overrides]);

  useEffect(() => {
    if (selected) onConfig(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    void api.catalog().then(setCatalog).catch(() => undefined);
  }, [selected]);

  // The plan follows the draft, after it settles.
  useEffect(() => {
    if (!draft) return;
    const handle = window.setTimeout(() => {
      void api
        .draftPlan()
        .then((projected) => setPlan(projected.projection))
        .catch(() => setPlan(null));
    }, 500);
    return () => window.clearTimeout(handle);
  }, [draft?.revision]); // eslint-disable-line react-hooks/exhaustive-deps

  // `/configure#run-logging` (the Logs page links here): scroll once the card exists.
  useEffect(() => {
    if (!location.hash || !resolved) return;
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [location.hash, !!resolved]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit: Commit = useCallback((key, value) => void update({ set: { [key]: value } }), [update]);
  const fields = useMemo(() => new Map((catalog?.fields ?? []).map((f) => [f.key, f])), [catalog]);
  const field = (key: string) => fields.get(key);
  const valueOf = (key: string) => (key in values ? values[key] : field(key)?.current);
  const render = (key: string, compact = false) => {
    const f = field(key);
    if (!f) return null;
    return <SettingField key={key} field={f} value={valueOf(key)} overridden={key in overrides} onCommit={commit} compact={compact} />;
  };

  const profile = String(valueOf("collection.profile") ?? "scholarly");
  const arms = (valueOf("arms") as string[] | undefined) ?? [];
  const pheasant = resolved?.pheasant;

  const launch = async (kind: "demo" | "pipeline") => {
    setBusy(kind);
    try {
      const started = await api.launchDraft({ kind, label: draft?.launch.label ?? undefined });
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

  const shownFields = (catalog?.fields ?? []).filter((f) => {
    if (!advanced && f.advanced && !(f.key in overrides)) return false;
    if (section !== "all" && f.section !== section) return false;
    if (filter) {
      const needle = filter.toLowerCase();
      return (f.key + " " + f.label + " " + f.help).toLowerCase().includes(needle);
    }
    return true;
  });
  const grouped = new Map<string, CatalogField[]>();
  for (const f of shownFields) grouped.set(f.section, [...(grouped.get(f.section) ?? []), f]);

  const refusal = error ?? (settings && !settings.valid ? settings.error : null);

  return (
    <div className="page page--fit" style={{ maxWidth: 1440 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h1>Configure</h1>
        <span className="muted">
          one settings draft, shared with the MCP tools — every change is a <code>--set</code>
        </span>
        {draft?.updated_by && draft.updated_by !== "ui" ? (
          <span className="pill pill--info" title="This draft was last changed outside this page">
            last changed via {draft.updated_by}
          </span>
        ) : null}
      </div>
      {refusal ? (
        <div className="toast toast--danger" style={{ boxShadow: "none" }}>
          <span className="toast__icon">!</span>
          <div>
            <b>{settings && !settings.valid ? "This draft does not resolve" : "Refused"}</b>
            <span className="soft">{refusal}</span>
          </div>
        </div>
      ) : null}
      <div className="cfg">
        <div className="fit-scroll" style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <div className="card">
            <div className="card__head">Experiment</div>
            <div className="card__body grid3">
              <div className="fld">
                <label>config file</label>
                <select
                  className="input"
                  value={selected ?? ""}
                  onChange={(e) => void update({ config: e.target.value })}
                >
                  {(configs.data ?? []).map((c) => (
                    <option key={c.path} value={c.path}>
                      {c.path}
                    </option>
                  ))}
                </select>
                <div className="h">switching file clears this draft's overrides</div>
              </div>
              {render("experiment.name")}
              <RunLabel value={draft?.launch.label ?? ""} onCommit={(label) => void update({ launch: { label: label || null } })} />
              {render("experiment.seed", true)}
              <div className="fld">
                <label>topic</label>
                <select
                  className="input"
                  value={draft?.topic ?? ""}
                  onChange={(e) => void update({ topic: e.target.value || null })}
                >
                  <option value="">first in topics file</option>
                  {(resolved?.topics ?? []).map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {selected ? (
            <Topics
              config={selected}
              set={set}
              selected={draft?.topic ?? undefined}
              draftModels={catalog?.draft_models ?? []}
              onSelect={(topicId) => void update({ topic: topicId ?? null })}
              onSaved={(override, topicId) => {
                const [path, value] = override.split("=", 2);
                void update({ set: { [path]: value }, topic: topicId });
              }}
              onDeleted={(override, topicId) => {
                const [path, value] = override.split("=", 2);
                void update({ set: { [path]: value }, ...(draft?.topic === topicId ? { topic: null } : {}) });
              }}
            />
          ) : null}

          <Connections
            selected={draft?.connection ?? null}
            pheasant={pheasant}
            onSelected={setSettings}
            render={render}
          />

          <Budget
            render={render}
            launchCap={draft?.launch.max_cost_usd ?? null}
            allocation={["planning", "collection", "benchmark", "evaluation", "reserve"].map((k) => Number(valueOf(`budget.allocation.${k}`) ?? 0))}
            onLaunchCap={(cap) => void update({ launch: { max_cost_usd: cap } })}
          />

          <div className="card" id="search">
            <div className="card__head">
              Pheasant search <span className="sub">what every Pheasant arm's search asks the region for</span>
            </div>
            <div className="card__body grid3">
              {(catalog?.fields ?? []).filter((f) => f.section === "search").map((f) => render(f.key))}
            </div>
          </div>

          <Models
            catalog={catalog}
            values={values}
            overrides={overrides}
            onCommit={commit}
            onSettings={setSettings}
            onError={setError}
          />

          <div className="card">
            <div className="card__head">
              Collection profile <span className="sub">supplies defaults, never overrides: keys you set stay set</span>
            </div>
            <div className="card__body grid3">
              {PROFILES.map((p) => (
                <button
                  key={p.key}
                  className={`prof${profile === p.key ? " prof--on" : ""}`}
                  onClick={() => commit("collection.profile", profile === p.key && "collection.profile" in overrides ? null : p.key)}
                >
                  <b>{p.title}</b>
                  {p.body}
                  <div className="muted" style={{ marginTop: 4 }}>
                    {p.note}
                  </div>
                </button>
              ))}
            </div>
            <div className="card__body grid3" style={{ paddingTop: 0 }}>
              {COLLECTION_KEYS.map((key) => render(key))}
            </div>
          </div>

          <div className="card">
            <div className="card__head">
              Arms <span className="sub">isolation is enforced in <code>arms/base.py</code>; this chooses which run</span>
            </div>
            <div className="card__body" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8 }}>
              {ARMS.map((arm) => {
                const on = arms.includes(arm.id);
                return (
                  <button
                    key={arm.id}
                    className={`armc${on ? "" : " armc--off"}`}
                    onClick={() => {
                      const next = on ? arms.filter((a) => a !== arm.id) : [...arms, arm.id];
                      commit("arms", ARMS.map((a) => a.id).filter((id) => next.includes(id)));
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span className="pill" style={{ color: armColor(arm.id) }}>
                        {arm.id}
                      </span>
                      <span className="muted" style={{ marginLeft: "auto" }}>
                        {on ? "on" : "off"}
                      </span>
                    </span>
                    {arm.text}
                  </button>
                );
              })}
            </div>
            <div className="card__body grid3" style={{ paddingTop: 0 }}>
              {render("replay.repetitions")}
              {render("replay.pairing_policy")}
              {render("benchmark.questions_per_topic")}
            </div>
          </div>

          <div className="card" id="run-logging">
            <div className="card__head">
              Run logging <span className="sub mono">{resolved?.source_files?.logging?.split("/").slice(-2).join("/")}</span>
              <div className="r">
                <Link className="btn btn--ghost btn--small" to="/logs" style={{ textDecoration: "none" }}>
                  Retention & deletion →
                </Link>
              </div>
            </div>
            <div className="card__body grid3">
              {render("logging.level")}
              {render("logging.format")}
              {render("logging.file")}
              {render("tracing.mcp_transcript.store_request_body")}
              {render("tracing.mcp_transcript.store_response_body")}
              {render("tracing.mcp_transcript.max_body_bytes")}
            </div>
          </div>

          <div className="card" id="all-settings">
            <div className="card__head">
              Every setting <span className="sub">{shownFields.length} of {catalog?.fields.length ?? 0} · each one explained</span>
              <div className="r" style={{ gap: 6 }}>
                <input
                  className="input"
                  style={{ width: 180 }}
                  placeholder="filter: key, word…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  aria-label="Filter settings"
                />
                <select className="input" style={{ width: 160 }} value={section} onChange={(e) => setSection(e.target.value)} aria-label="Section">
                  <option value="all">all sections</option>
                  {(catalog?.sections ?? []).map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.title}
                    </option>
                  ))}
                </select>
                <button className={`btn btn--small${advanced ? " btn--primary" : ""}`} onClick={() => setAdvanced((v) => !v)} aria-pressed={advanced}>
                  advanced
                </button>
              </div>
            </div>
            {[...grouped.entries()].map(([key, rows]) => {
              const meta = catalog?.sections.find((s) => s.key === key);
              return (
                <div key={key} className="card__body" style={{ borderTop: "1px solid var(--border)" }}>
                  <div className="eyebrow" style={{ marginBottom: 6 }}>
                    {meta?.title ?? key} <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>· {meta?.blurb}</span>
                  </div>
                  <div className="grid3">{rows.map((f) => render(f.key))}</div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="sticky fit-scroll">
          <div className="card">
            <div className="card__head">
              Plan <span className="sub">worst case, no model or ingest call</span>
            </div>
            <div className="card__body">
              <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em" }}>{usd(Number(plan?.projected_total_usd ?? 0))}</span>
                <span className="muted">of {usd(Number(plan?.budget_usd ?? valueOf("experiment.cost_budget_usd") ?? 0))}</span>
                {plan ? (
                  <span className={`pill ${plan.fits_budget ? "pill--accent" : "pill--danger"}`} style={{ marginLeft: "auto" }}>
                    {plan.fits_budget ? "fits" : "over budget"}
                  </span>
                ) : null}
              </div>
              {plan ? (
                <dl className="kv" style={{ marginTop: 8 }}>
                  <dt>planning</dt>
                  <dd>{usd(Number(plan.planning_usd), 4)}</dd>
                  <dt>collection</dt>
                  <dd>
                    {usd(Number(plan.collection_usd), 4)} + search APIs {usd(Number(plan.search_api_usd), 4)}
                  </dd>
                  <dt>benchmark</dt>
                  <dd>{usd(Number(plan.benchmark_usd), 4)}</dd>
                  <dt>evaluation</dt>
                  <dd>{usd(Number(plan.evaluation_usd), 4)}</dd>
                  <dt>volume</dt>
                  <dd>
                    {String(plan.searches)} provider searches · {String(plan.questions)} questions · {String(plan.answers)} answers ·{" "}
                    {String(plan.mcp_search_calls)} MCP searches
                  </dd>
                </dl>
              ) : (
                <span className="muted">{settings && !settings.valid ? "fix the draft to project its cost" : "projecting…"}</span>
              )}
            </div>
          </div>

          {resolved?.advice?.length ? (
            <div className="card">
              <div className="card__head">Advice</div>
              <div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {resolved.advice.map((row) => (
                  <div key={row.key + row.message} className={`pill ${row.level === "warn" ? "pill--warn" : "pill--info"}`} style={{ whiteSpace: "normal" }}>
                    {row.message}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

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
                  <span className={`pill ${doctor.exit_code === 0 ? "pill--accent" : "pill--danger"}`}>{doctor.exit_code === 0 ? "PASS" : "FAIL"}</span>
                  <pre className="yaml" style={{ marginTop: 8 }}>
                    {doctor.output.trim()}
                  </pre>
                </>
              ) : (
                <span className="muted">Checks models, prices, providers, the region's tools and every mapped argument against its schema.</span>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card__head">
              Resolved <span className="sub">{Object.keys(overrides).length} override(s)</span>
              <div className="r">
                {Object.keys(overrides).length || draft?.topic ? (
                  <button className="btn btn--ghost btn--small" onClick={() => void api.resetSettings().then(setSettings)}>
                    reset all
                  </button>
                ) : null}
                <span className="mono muted" title={resolved?.digest}>
                  {resolved?.digest.slice(7, 19)}…
                </span>
              </div>
            </div>
            <div className="card__body">
              <pre className="yaml">
                {`pheasant-lab run --config ${selected ?? ""}${set.map((s) => ` \\\n  --set ${s}`).join("")}${draft?.topic ? ` \\\n  --topic ${draft.topic}` : ""}${draft?.launch.max_cost_usd ? ` \\\n  --max-cost-usd ${draft.launch.max_cost_usd}` : ""}`}
              </pre>
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
            <button className="btn btn--primary" style={{ flex: 2, justifyContent: "center" }} disabled={!!busy || !resolved || !settings?.valid} onClick={() => void launch("pipeline")}>
              {busy === "pipeline" ? <span className="spinner" /> : null} Launch run ▸
            </button>
          </div>
          <div className="muted small">
            <b>Offline demo</b>: fixtures and the mock region, free. <b>Launch run</b>: <code>pheasant-lab run</code> — collect → freeze → evaluate → replay → report → verify,
            resuming any stage that crashes and stopping at the first refusal. It runs detached: closing the console does not stop it, and Runs offers <b>Resume</b> for one that
            was interrupted.
          </div>
        </div>
      </div>
    </div>
  );
}

/** The next run's name: console bookkeeping, never part of the digest. */
function RunLabel({ value, onCommit }: { value: string; onCommit: (label: string) => void }) {
  const draft = useDraftText(value, (text) => onCommit(text.trim()));
  return (
    <div className="fld">
      <label htmlFor="run-label">run name</label>
      <input
        id="run-label"
        className="input"
        placeholder="optional — shown in Runs"
        value={draft.text}
        onChange={(e) => draft.onChange(e.target.value)}
        onFocus={draft.onFocus}
        onBlur={draft.onBlur}
        onKeyDown={draft.onKeyDown}
      />
      <div className="h">a label for the run this launch makes; rename it any time in Runs</div>
    </div>
  );
}
