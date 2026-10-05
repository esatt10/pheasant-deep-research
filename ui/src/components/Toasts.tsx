import { useEffect, useState } from "react";
import type { Notice } from "../types";
import { clock } from "../format";

const ICON: Record<Notice["tone"], string> = { warn: "⧗", danger: "!", info: "i", ok: "✓" };

/**
 * Region notices for the run being watched — one per state the lab saw
 * Pheasant in, updated in place rather than stacked (a barrier that polls
 * twenty times is one wait). Dismissal is per code and per run.
 */
export function Toasts({ notices, runId }: { notices: Notice[]; runId?: string }) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  useEffect(() => setDismissed(new Set()), [runId]);
  const visible = notices.filter((n) => !dismissed.has(n.code)).slice(-3);
  if (!visible.length) return null;
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
          <button
            className="btn btn--ghost btn--small"
            onClick={() => setDismissed((s) => new Set([...s, notice.code]))}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
