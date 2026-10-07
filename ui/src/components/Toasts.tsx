import { useEffect, useState } from "react";
import type { Notice } from "../types";
import { clock } from "../format";

const ICON: Record<Notice["tone"], string> = { warn: "⧗", danger: "!", info: "i", ok: "✓" };
// Good news fades on its own; a warning stays until someone dismisses it.
const SETTLE_MS = 9000;

/**
 * Region notices for the run being watched — one per state the lab saw
 * Pheasant in, updated in place rather than stacked (a barrier that polls
 * twenty times is one wait). Dismissal is per code and per run.
 *
 * `inline` lays them out as a strip inside the page rather than floating
 * over it: on the live page every corner of the canvas holds something a
 * person clicks, and a notice that covers the thing it is about is worse
 * than one that moves it down by a line.
 */
export function Toasts({ notices, runId, inline }: { notices: Notice[]; runId?: string; inline?: boolean }) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  useEffect(() => setDismissed(new Set()), [runId]);
  const settled = notices
    .filter((n) => (n.tone === "ok" || n.tone === "info") && !dismissed.has(n.code))
    .map((n) => `${n.code}\u0000${n.detail}`)
    .join("\u0001");
  useEffect(() => {
    if (!settled) return;
    const codes = settled.split("\u0001").map((key) => key.split("\u0000")[0]);
    const timer = window.setTimeout(() => setDismissed((s) => new Set([...s, ...codes])), SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [settled]);
  const visible = notices.filter((n) => !dismissed.has(n.code)).slice(-3);
  if (!visible.length) return null;
  const dismiss = (code: string) => setDismissed((s) => new Set([...s, code]));
  if (inline) {
    return (
      <div className="notices" aria-live="polite">
        {visible.map((notice) => (
          <div key={notice.code} className={`notice notice--${notice.tone}`} role="status" title={notice.detail}>
            <span className="toast__icon">{ICON[notice.tone]}</span>
            <b>{notice.title}</b>
            <span className="soft notice__detail">{notice.detail}</span>
            {notice.at != null ? <span className="muted mono small">at {clock(notice.at)}</span> : null}
            <button className="btn btn--ghost btn--small" onClick={() => dismiss(notice.code)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="toasts" aria-live="polite">
      {visible.map((notice) => (
        <div key={notice.code} className={`toast toast--${notice.tone}`} role="status">
          <span className="toast__icon">{ICON[notice.tone]}</span>
          <div>
            <b>{notice.title}</b>
            <span className="soft">{notice.detail}</span>
            {notice.at != null ? <div className="muted small mono">at {clock(notice.at)}</div> : null}
          </div>
          <button className="btn btn--ghost btn--small" onClick={() => dismiss(notice.code)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
