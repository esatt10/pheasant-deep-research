import { useEffect, useState } from "react";

/**
 * A destructive action, said out loud before it happens. `confirmText`, when
 * given, must be typed - for deleting a whole run, where the click alone is
 * too easy to make by accident and the run cannot be brought back.
 */
export function ConfirmDialog({
  title,
  body,
  action,
  confirmText,
  onConfirm,
  onClose,
}: {
  title: string;
  body: React.ReactNode;
  action: string;
  confirmText?: string;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  const ready = !confirmText || typed.trim() === confirmText;
  return (
    <div className="modal-scrim" onClick={() => !busy && onClose()}>
      <form
        className="modal modal--narrow"
        role="alertdialog"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
        onSubmit={async (event) => {
          event.preventDefault();
          if (!ready) return;
          setBusy(true);
          setError(null);
          try {
            await onConfirm();
            onClose();
          } catch (caught) {
            setError((caught as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <header className="modal__header">
          <h2>{title}</h2>
        </header>
        <div className="small soft">{body}</div>
        {confirmText ? (
          <label className="field">
            <span>
              Type <code>{confirmText}</code> to confirm
            </span>
            <input className="text-input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
          </label>
        ) : null}
        {error ? <p className="error small">{error}</p> : null}
        <footer className="modal__footer">
          <button className="btn" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn--danger" type="submit" disabled={!ready || busy} autoFocus={!confirmText}>
            {busy ? <span className="spinner" /> : null} {action}
          </button>
        </footer>
      </form>
    </div>
  );
}
