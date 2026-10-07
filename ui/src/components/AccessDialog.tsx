import { FormEvent, useEffect, useRef, useState } from "react";
import { api, getAccessKey, setAccessKey } from "../api";

/**
 * Paste the console's access key - pheasant-kb's "Connect to this fleet"
 * dialog, for the lab. The key is checked against `/api/auth` before it is
 * kept, so a typo is a message here rather than a page of 401s behind it.
 */
export function AccessDialog({ onClose }: { onClose: () => void }) {
  const [key, setKey] = useState(getAccessKey);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!key.trim()) {
      setError("Paste the console's access key to continue.");
      input.current?.focus();
      return;
    }
    setChecking(true);
    const previous = getAccessKey();
    try {
      sessionStorage.setItem("pheasant-lab.console.key", key.trim());
    } catch {
      /* storage unavailable: the check below still runs on the header */
    }
    try {
      const auth = await api.auth();
      if (!auth.authenticated) {
        setAccessKey(previous);
        setError("That key was refused. It must equal PHEASANT_LAB_CONSOLE_TOKEN on the console's machine.");
        return;
      }
      setAccessKey(key);
      onClose();
    } catch (caught) {
      setAccessKey(previous);
      setError((caught as Error).message);
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form
        className="modal modal--narrow"
        role="dialog"
        aria-label="Connect to this console"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => void save(event)}
      >
        <header className="modal__header">
          <h2>Connect to this console</h2>
          <button className="btn btn--ghost btn--icon" type="button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <p className="muted small">
          This console requires an access key for runs, traces, launches and logs. Paste{" "}
          <code>PHEASANT_LAB_CONSOLE_TOKEN</code> from the machine running <code>pheasant-lab serve</code>.
        </p>
        <label className="field">
          <span>Access key</span>
          <input
            ref={input}
            className="text-input"
            type="password"
            value={key}
            onChange={(event) => {
              setKey(event.target.value);
              setError(null);
            }}
            placeholder="Paste the bearer key"
            autoFocus
            autoComplete="off"
          />
        </label>
        {error ? <p className="error small">{error}</p> : null}
        <p className="muted small">The key is kept only in this tab's session storage.</p>
        <footer className="modal__footer">
          {getAccessKey() ? (
            <button
              className="btn btn--danger"
              type="button"
              onClick={() => {
                setAccessKey("");
                onClose();
              }}
            >
              Disconnect
            </button>
          ) : null}
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn--primary" type="submit" disabled={checking}>
            {checking ? <span className="spinner" /> : null} Connect
          </button>
        </footer>
      </form>
    </div>
  );
}
