import { useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks/usePoll";
import type { RegionProbe } from "../types";
import { clock } from "../format";

/**
 * What the configured region says about itself *now*: `/ready`, `/queue` and
 * what it holds for the lab's source, asked directly. Amber whenever anything is pre-claim, busy, draining or
 * behind; green only when it is ready **and** nothing is waiting.
 */
export function RegionChip({ config }: { config: string | undefined }) {
  const [open, setOpen] = useState(false);
  const probe = usePoll<RegionProbe>(
    () => api.region(config),
    (data) => (data?.notices.some((n) => n.tone !== "info") ? 2000 : 8000),
    [config],
  );
  const data = probe.data;
  const notices = data?.notices ?? [];
  const worst = notices.some((n) => n.tone === "danger")
    ? "danger"
    : notices.some((n) => n.tone === "warn")
      ? "warn"
      : data?.reachable === false
        ? "danger"
        : "accent";
  const waiting = (data?.queue?.tasks ?? []).filter((t) => t.state === "awaiting_claim");
  const label = !data
    ? "region · checking"
    : data.mock
      ? "region · mock"
      : data.reachable === false
        ? "region · unreachable"
        : waiting.length
          ? `region · ${waiting.length} sync${waiting.length === 1 ? "" : "s"} awaiting claim`
          : `region · ${data.ready?.status ?? "unknown"}${
              data.ready?.graph_generation?.loaded ? ` · gen ${data.ready.graph_generation.loaded.slice(0, 6)}` : ""
            }`;

  return (
    <>
      <button className={`pill pill--${worst}`} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`dot${worst !== "accent" || waiting.length ? " dot--live" : ""}`} />
        {label}
      </button>
      {open && data ? (
        <div className="region-pop" role="dialog" aria-label="Region status">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <img src="/pheasant.png" width={20} height={20} alt="" />
            <b>Pheasant region</b>
            <span className="muted small" style={{ marginLeft: "auto" }}>
              {data.base_url ?? data.transport}
            </span>
          </div>
          <dl className="kv">
            <dt>/ready</dt>
            <dd>
              <span className={`pill ${data.ready?.status === "ready" ? "pill--accent" : "pill--danger"}`}>
                {data.ready?.status ?? (data.reachable === false ? "unreachable" : "—")}
              </span>{" "}
              {data.ready?.reason ? <span className="muted">{data.ready.reason}</span> : null}
            </dd>
            {data.ready?.role ? (
              <>
                <dt>role</dt>
                <dd>
                  {data.ready.role}
                  {data.ready.indexes_locally === false ? " · publishes syncs (does not index)" : ""}
                  {data.ready.leader === false ? " · standby" : ""}
                </dd>
              </>
            ) : null}
            {data.ready?.graph_generation ? (
              <>
                <dt>generation</dt>
                <dd className="mono">
                  loaded {data.ready.graph_generation.loaded ?? "—"}
                  {data.ready.graph_generation.published &&
                  data.ready.graph_generation.published !== data.ready.graph_generation.loaded
                    ? ` ≠ published ${data.ready.graph_generation.published}`
                    : " = published"}
                </dd>
              </>
            ) : null}
            <dt>lab source</dt>
            <dd>
              {data.inventory ? (
                <>
                  <span className="mono">{data.inventory.source_name}</span> · {data.inventory.documents ?? "—"} document
                  {data.inventory.documents === 1 ? "" : "s"} indexed
                  {data.inventory.last_indexed_at ? <span className="muted"> · last {data.inventory.last_indexed_at}</span> : null}
                </>
              ) : (
                <span className="muted">not registered yet, or not reported by this pheasant version</span>
              )}
            </dd>
            <dt>index queue</dt>
            <dd>
              {data.queue_unsupported
                ? "not reported by this pheasant version"
                : !data.queue
                  ? "—"
                  : !data.queue.enabled
                    ? "off (syncs run where they are asked)"
                    : data.queue.tasks.length === 0
                      ? "empty"
                      : data.queue.tasks
                          .map((t) => `${t.source}: ${t.state.replace("_", " ")} ${clock(t.waiting_seconds)}`)
                          .join(" · ")}
            </dd>
          </dl>
          {notices.map((notice) => (
            <div key={notice.code} className={`toast toast--${notice.tone}`} style={{ boxShadow: "none" }}>
              <span className="toast__icon">{notice.tone === "warn" ? "⧗" : notice.tone === "danger" ? "!" : "i"}</span>
              <div>
                <b>{notice.title}</b>
                <span className="soft">{notice.detail}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}
