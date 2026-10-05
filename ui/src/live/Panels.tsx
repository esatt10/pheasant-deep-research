import type { RunModel } from "../types";
import { STAGE_LABEL, usd } from "../format";

export function BudgetPanel({ model }: { model: RunModel }) {
  const { budget } = model;
  return (
    <div className="card">
      <div className="card__head">
        Budget <span className="sub">reserved before spent</span>
        <div className="r mono muted">{usd(budget.committed_usd)} of {usd(budget.total_usd)}</div>
      </div>
      <div className="card__body">
        {budget.buckets.map((bucket) => (
          <div key={bucket.bucket} className="bucket">
            <span className="soft">{bucket.bucket}</span>
            <div className="bar" style={{ height: 8 }}>
              <i style={{ width: `${Math.min(100, (bucket.committed_usd / Math.max(1e-9, bucket.budget_usd)) * 100)}%` }} />
            </div>
            <span className="mono" style={{ textAlign: "right" }}>
              {usd(bucket.committed_usd)}/{bucket.budget_usd}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function FacetPanel({ model }: { model: RunModel }) {
  return (
    <div className="card">
      <div className="card__head">Facet coverage <span className="sub">the stopping minimums</span></div>
      <div className="card__body">
        {model.facets.length === 0 ? <span className="muted">No audit yet — the auditor runs after each round.</span> : null}
        {model.facets.map((facet) => {
          const match = /(\d+)<(\d+)/.exec(facet.unmet[0] ?? "");
          const fraction = facet.meets_minimum ? 1 : match ? Number(match[1]) / Number(match[2]) : 0;
          return (
            <div key={facet.facet_id} className="facet">
              <div className="facet__row">
                <span>{facet.label}</span>
                <span className={facet.meets_minimum ? "" : "muted"}>{facet.meets_minimum ? "✓ met" : facet.unmet[0] ?? "short"}</span>
              </div>
              <div className="bar">
                <i style={{ width: `${fraction * 100}%`, background: facet.meets_minimum ? "var(--accent)" : "var(--warn)" }} />
              </div>
              <div className="muted small">
                {facet.sources} sources · {facet.families} families · authoritative {facet.authoritative} · {facet.claims} claims
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const COLUMNS: { key: string; label: string; pre?: boolean }[] = [
  { key: "discovered", label: "found" },
  { key: "acquired", label: "acquired" },
  { key: "submitted", label: "submitted" },
  { key: "accepted", label: "accepted" },
  { key: "awaiting_claim", label: "awaiting\nclaim", pre: true },
  { key: "claimed", label: "indexer\nclaimed", pre: true },
  { key: "indexed", label: "indexed" },
];

export function CustodyPanel({ model }: { model: RunModel }) {
  const custody = model.custody;
  const max = Math.max(1, custody.discovered ?? 0);
  const pre = (custody.awaiting_claim ?? 0) + (custody.claimed ?? 0);
  const disposition = model.region.sync.disposition;
  return (
    <div className="card">
      <div className="card__head">
        Custody <span className="sub">in Pheasant</span>
        <div className="r">
          {pre ? (
            <span className="pill pill--warn">{custody.awaiting_claim ? `${custody.awaiting_claim} pre-claim` : `${custody.claimed} claimed`}</span>
          ) : disposition === "completed" ? (
            <span className="pill">indexed in-call</span>
          ) : model.region.barrier.state === "crossed" ? (
            <span className="pill pill--accent">barrier crossed</span>
          ) : null}
        </div>
      </div>
      <div className="card__body">
        <div className="funnel">
          {COLUMNS.map((column) => {
            const n = custody[column.key] ?? 0;
            return (
              <div
                key={column.key}
                className={`fcol${column.pre ? " fcol--pre" : ""}${column.pre && n ? (column.key === "claimed" ? " fcol--claimed" : " fcol--live") : ""}`}
                title={STAGE_LABEL[column.key]}
              >
                <span className="fcol__n">{n}</span>
                <div className="fcol__b" style={{ height: `${(n / max) * 64 + 3}px` }} />
                <span className="fcol__l">{column.label.split("\n").map((l, i) => <span key={i} style={{ display: "block" }}>{l}</span>)}</span>
              </div>
            );
          })}
        </div>
        {custody.rejected ? <div className="muted small" style={{ marginTop: 4 }}>{custody.rejected} rejected at receipt</div> : null}
      </div>
    </div>
  );
}
