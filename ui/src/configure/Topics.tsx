import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { TopicDoc, TopicDraft, TopicList } from "../types";

/**
 * Research topics: the ones the config already sees, and a form to add one.
 *
 * A topic is content rather than a setting, so saving one is the console's
 * single file write — ``configs/topics.local.yaml``, the current topics plus
 * the new one (console/topics.py). The run then uses it through an ordinary
 * ``--set experiment.topics_file=...``, so the argv still tells the whole
 * story. The server validates with the same model ``load_config`` uses; this
 * form only stops the obvious before a round trip.
 *
 * A topic can start from an **intent** — what you want to find out, in your
 * own words — instead of (or as well as) seed terms. "Draft from intent" asks
 * the planner's model, through the CLI's `draft-topic` under its own small
 * budget, to propose a title, seed terms and facets for you to edit. The
 * intent is saved on the topic, and the planner reads it on every run.
 */

const SOURCE_TYPES = [
  "journal_article",
  "review",
  "preprint",
  "proceedings",
  "book_chapter",
  "dataset",
  "company_publication",
  "filing",
  "job_posting",
  "interview",
  "press",
  "essay",
];
const FAMILY_KEYS = ["first_author_affiliation", "corresponding_author"];

interface FacetDraft {
  label: string;
  id: string;
  idEdited: boolean;
  weight: string;
}

const slug = (text: string, prefix = "") => {
  const body = text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .replace(/[_\s]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
  return body ? `${prefix}${body}` : "";
};

const emptyFacet = (): FacetDraft => ({ label: "", id: "", idEdited: false, weight: "1" });

function toYaml(topic: TopicDoc): string {
  const q = (s: string) => (/^[\w .-]+$/.test(s) && !/^\d/.test(s) ? s : JSON.stringify(s));
  const lines = [
    `- id: ${topic.id || "…"}`,
    `  title: ${q(topic.title || "…")}`,
    ...(topic.intent ? [`  intent: ${JSON.stringify(topic.intent)}`] : []),
    "  seed_terms:",
    ...(topic.seed_terms.length ? topic.seed_terms.map((t) => `    - ${q(t)}`) : ["    []"]),
    `  date_range: { from: ${topic.date_range.from ?? "null"}, to: ${topic.date_range.to ?? "null"} }`,
    "  facets:",
    ...topic.facets.map((f) => `    - { id: ${f.id || "…"}, label: ${JSON.stringify(f.label)}, weight: ${f.weight} }`),
    "  source_authority:",
    `    family_key: [${topic.source_authority.family_key.join(", ")}]`,
    `    preferred_types: [${topic.source_authority.preferred_types.join(", ")}]`,
    `    minimum_peer_reviewed: ${topic.source_authority.minimum_peer_reviewed}`,
  ];
  return lines.join("\n");
}

export function Topics({
  config,
  set,
  selected,
  onSelect,
  onSaved,
}: {
  config: string;
  set: string[];
  selected?: string;
  onSelect: (topicId: string | undefined) => void;
  onSaved: (override: string, topicId: string) => void;
}) {
  const [list, setList] = useState<TopicList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const key = `${config}|${set.join("|")}`;

  useEffect(() => {
    let live = true;
    void api
      .topics(config, set)
      .then((rows) => live && setList(rows))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const active = selected ?? list?.topics[0]?.id;

  return (
    <div className="card" id="topics">
      <div className="card__head">
        Research topics
        <span className="sub mono">{list?.topics_file}</span>
        <div className="r">
          {!adding ? (
            <button className="btn btn--small btn--primary" onClick={() => { setAdding(true); setSaved(null); }}>
              + New topic
            </button>
          ) : null}
        </div>
      </div>
      {saved ? (
        <div className="card__body" style={{ paddingBottom: 0 }}>
          <div className="toast toast--ok" style={{ boxShadow: "none" }}>
            <span className="toast__icon">✓</span>
            <div>
              <b>Saved to {list?.local_file}</b>
              <span className="soft">
                This run now uses <code>--set {list?.override}</code> and topic <code>{saved}</code>. The shipped topics files are unchanged.
              </span>
            </div>
          </div>
        </div>
      ) : null}
      {error ? <div className="card__body"><span className="pill pill--danger" style={{ whiteSpace: "normal" }}>{error}</span></div> : null}
      {adding ? (
        <TopicForm
          config={config}
          set={set}
          existing={list?.topics.map((t) => t.id) ?? []}
          onCancel={() => setAdding(false)}
          onSave={async (topic) => {
            const result = await api.addTopic({ config, set, topic });
            setAdding(false);
            setSaved(topic.id);
            onSaved(result.override, topic.id);
          }}
        />
      ) : null}
      <div className="topics">
        {(list?.topics ?? []).map((topic) => {
          const on = topic.id === active;
          return (
            <button key={topic.id} className={`topic${on ? " topic--on" : ""}`} onClick={() => onSelect(on && selected ? undefined : topic.id)}>
              <div className="topic__head">
                <span className={`radio${on ? " radio--on" : ""}`} />
                <b>{topic.title}</b>
              </div>
              <div className="mono muted small topic__id">{topic.id}</div>
              {topic.intent ? <div className="topic__intent">“{topic.intent}”</div> : null}
              <div className="topic__facets">
                {topic.facets.map((f) => (
                  <span key={f.id} className="pill" title={f.id}>
                    {f.label} <span className="muted">×{f.weight}</span>
                  </span>
                ))}
              </div>
              <div className="muted small">
                {topic.seed_terms.length
                  ? `${topic.seed_terms.length} seed term${topic.seed_terms.length === 1 ? "" : "s"}`
                  : "intent only · the planner derives the vocabulary"}
                {topic.date_range.from || topic.date_range.to ? ` · ${topic.date_range.from ?? "…"} → ${topic.date_range.to ?? "now"}` : ""}
                {topic.source_authority.preferred_types.length ? ` · prefers ${topic.source_authority.preferred_types.join(", ")}` : ""}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TopicForm({
  config,
  set,
  existing,
  onCancel,
  onSave,
}: {
  config: string;
  set: string[];
  existing: string[];
  onCancel: () => void;
  onSave: (topic: TopicDoc) => Promise<void>;
}) {
  const [intent, setIntent] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draft, setDraft] = useState<TopicDraft | null>(null);
  const [title, setTitle] = useState("");
  const [id, setId] = useState("");
  const [idEdited, setIdEdited] = useState(false);
  const [seeds, setSeeds] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [facets, setFacets] = useState<FacetDraft[]>([emptyFacet(), emptyFacet()]);
  const [families, setFamilies] = useState<string[]>(FAMILY_KEYS);
  const [types, setTypes] = useState<string[]>(["journal_article", "review"]);
  const [minimum, setMinimum] = useState("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const topicId = idEdited ? id : slug(title, "topic-");
  const topic: TopicDoc = useMemo(
    () => ({
      id: topicId,
      title: title.trim(),
      intent: intent.trim() || null,
      seed_terms: seeds.split("\n").map((s) => s.trim()).filter(Boolean),
      date_range: { from: from || null, to: to || null },
      facets: facets
        .filter((f) => f.label.trim())
        .map((f) => ({ id: f.idEdited ? f.id : slug(f.label), label: f.label.trim(), weight: Number(f.weight) || 1 })),
      source_authority: { family_key: families, preferred_types: types, minimum_peer_reviewed: Number(minimum) || 0 },
    }),
    [topicId, title, intent, seeds, from, to, facets, families, types, minimum],
  );

  const problems: string[] = [];
  if (!topic.title) problems.push("a title");
  if (!topic.id) problems.push("an id");
  if (existing.includes(topic.id)) problems.push(`an id other than ${topic.id}, which already exists`);
  if (!topic.facets.length) problems.push("at least one facet — coverage is measured against them");
  if (!topic.seed_terms.length && !topic.intent) problems.push("seed terms or an intent");

  const setFacet = (index: number, patch: Partial<FacetDraft>) =>
    setFacets((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const toggle = (list: string[], value: string, apply: (v: string[]) => void) =>
    apply(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  // Fills the form from a draft. Everything stays editable; nothing is saved.
  const runDraft = async () => {
    setDrafting(true);
    setError(null);
    try {
      const given = seeds.split("\n").map((t) => t.trim()).filter(Boolean);
      const next = await api.draftTopic({ config, set, intent, seed_terms: given });
      setDraft(next);
      setTitle(next.title);
      setId(next.id);
      setIdEdited(true);
      setSeeds(next.seed_terms.join("\n"));
      setFrom(next.date_range.from ?? "");
      setTo(next.date_range.to ?? "");
      setFacets(
        next.facets.map((f) => ({ label: f.label, id: f.id, idEdited: true, weight: String(f.weight) })),
      );
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setDrafting(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave(topic);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="topicform" aria-label="New research topic">
      <div className="fld">
        <label htmlFor="topic-intent">
          Intent <span className="muted">· what you want to find out, in your own words — optional if you give seed terms</span>
        </label>
        <textarea
          id="topic-intent"
          className="input"
          rows={3}
          placeholder="e.g. I want to understand how solid-state lithium batteries suppress dendrite growth, whether stack pressure or interphase chemistry matters more, and which critical current density claims failed to replicate."
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
          <button className="btn btn--small" disabled={drafting || intent.trim().length < 12} onClick={() => void runDraft()}>
            {drafting ? <span className="spinner" /> : "✦"} Draft from intent
          </button>
          <span className="h" style={{ margin: 0 }}>
            the planner's model proposes a title, seed terms and facets for you to edit — saved with the topic, the intent is the planner's brief on every run
          </span>
        </div>
        {draft ? (
          <div className={`pill ${draft.drafted_by.deterministic ? "pill--warn" : "pill--info"}`} style={{ whiteSpace: "normal", marginTop: 8 }}>
            {draft.drafted_by.deterministic
              ? `Offline draft (replay provider). ${draft.notes}`
              : `Drafted by ${draft.drafted_by.provider} · ${draft.drafted_by.model} for $${draft.drafted_by.cost_usd.toFixed(4)}${draft.notes ? ` — ${draft.notes}` : ""}`}
          </div>
        ) : null}
      </div>
      <div className="topicform__grid" style={{ marginTop: 12 }}>
        <div className="fld" style={{ gridColumn: "1 / 3" }}>
          <label htmlFor="topic-title">Title</label>
          <input id="topic-title" className="input" placeholder="e.g. Solid-state battery dendrite suppression" value={title} onChange={(e) => setTitle(e.target.value)} />
          <div className="h">the research question the swarm is collecting for</div>
        </div>
        <div className="fld">
          <label htmlFor="topic-id">id</label>
          <input
            id="topic-id"
            className="input mono"
            value={topicId}
            onChange={(e) => {
              setIdEdited(true);
              setId(e.target.value);
            }}
          />
          <div className="h">{idEdited ? "set by you" : "from the title"} · lowercase, digits, hyphens</div>
        </div>
        <div className="fld" style={{ gridColumn: "1 / 3", gridRow: "span 2" }}>
          <label htmlFor="topic-seeds">Seed terms <span className="muted">· one per line · optional with an intent</span></label>
          <textarea
            id="topic-seeds"
            className="input mono"
            rows={5}
            placeholder={"solid electrolyte lithium dendrite\ninterphase engineering garnet LLZO\ncritical current density"}
            value={seeds}
            onChange={(e) => setSeeds(e.target.value)}
          />
          <div className="h">what the planner and researchers start searching from</div>
        </div>
        <div className="fld">
          <label htmlFor="topic-from">Published from</label>
          <input id="topic-from" className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="fld">
          <label htmlFor="topic-to">Published to</label>
          <input id="topic-to" className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          <div className="h">empty means "up to now"</div>
        </div>
      </div>

      <div className="eyebrow" style={{ margin: "12px 0 4px" }}>Facets · the denominator of facet coverage</div>
      <div className="facetrows">
        <div className="facetrow facetrow--head muted small">
          <span>label</span><span>id</span><span>weight</span><span />
        </div>
        {facets.map((facet, index) => (
          <div key={index} className="facetrow">
            <input
              className="input"
              aria-label={`Facet ${index + 1} label`}
              placeholder={index === 0 ? "e.g. Interphase chemistry and stability" : "another aspect the corpus must cover"}
              value={facet.label}
              onChange={(e) => setFacet(index, { label: e.target.value })}
            />
            <input
              className="input mono"
              aria-label={`Facet ${index + 1} id`}
              value={facet.idEdited ? facet.id : slug(facet.label)}
              onChange={(e) => setFacet(index, { id: e.target.value, idEdited: true })}
            />
            <input
              className="input"
              aria-label={`Facet ${index + 1} weight`}
              type="number"
              min="0.5"
              step="0.5"
              value={facet.weight}
              onChange={(e) => setFacet(index, { weight: e.target.value })}
            />
            <button className="btn btn--ghost btn--small" aria-label={`Remove facet ${index + 1}`} onClick={() => setFacets((rows) => rows.filter((_, i) => i !== index))} disabled={facets.length === 1}>
              ✕
            </button>
          </div>
        ))}
      </div>
      <button className="btn btn--small" style={{ marginTop: 6 }} onClick={() => setFacets((rows) => [...rows, emptyFacet()])}>
        + Facet
      </button>

      <div className="eyebrow" style={{ margin: "14px 0 4px" }}>Source authority · recorded with the topic</div>
      <div className="topicform__grid">
        <div className="fld" style={{ gridColumn: "1 / 3" }}>
          <label>Preferred source types</label>
          <div className="chips">
            {SOURCE_TYPES.map((type) => (
              <button key={type} className={`pill${types.includes(type) ? " pill--accent" : ""}`} onClick={() => toggle(types, type, setTypes)}>
                {type}
              </button>
            ))}
          </div>
        </div>
        <div className="fld">
          <label htmlFor="topic-min">minimum_peer_reviewed</label>
          <input id="topic-min" className="input" type="number" min="0" value={minimum} onChange={(e) => setMinimum(e.target.value)} />
        </div>
        <div className="fld" style={{ gridColumn: "1 / 3" }}>
          <label>Family key · two papers from one family are one independent source</label>
          <div className="chips">
            {FAMILY_KEYS.map((key) => (
              <button key={key} className={`pill${families.includes(key) ? " pill--accent" : ""}`} onClick={() => toggle(families, key, setFamilies)}>
                {key}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="eyebrow" style={{ margin: "14px 0 4px" }}>Will be written as</div>
      <pre className="yaml">{toYaml(topic)}</pre>

      {error ? <div className="pill pill--danger" style={{ whiteSpace: "normal", marginTop: 8 }}>Refused: {error}</div> : null}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
        <span className="muted small">{problems.length ? `Needs ${problems.join(", ")}.` : "Ready — the server re-resolves the whole config before keeping it."}</span>
        <button className="btn" style={{ marginLeft: "auto" }} onClick={onCancel}>Cancel</button>
        <button className="btn btn--primary" disabled={busy || problems.length > 0} onClick={() => void save()}>
          {busy ? <span className="spinner" /> : null} Save topic
        </button>
      </div>
    </div>
  );
}
