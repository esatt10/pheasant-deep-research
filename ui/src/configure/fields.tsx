import { useEffect, useId, useRef, useState } from "react";
import type { CatalogField } from "../types";

/**
 * One setting from the catalog, as an input that can be typed into.
 *
 * The input owns its text while it has focus. It used to render
 * `override ?? resolved` directly, so clearing a field deleted the override
 * and the resolved value snapped back mid-keystroke - typing "12" over "6"
 * gave "612", and an experiment name could not be cleared at all. Now the
 * server's value is adopted only while the field is idle, and an edit is
 * committed after a pause, on Enter or on blur - never per keystroke.
 */

const COMMIT_DELAY_MS = 600;

export type Commit = (key: string, value: unknown | null) => void;

/** How a resolved value reads in a text box. */
export function display(field: CatalogField, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (field.kind === "list" && Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Text typed into a field, as the value its `--set` takes; `undefined` = not valid yet. */
export function parse(field: CatalogField, text: string): unknown | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  switch (field.kind) {
    case "int": {
      if (!/^-?\d+$/.test(trimmed)) return undefined;
      return Number.parseInt(trimmed, 10);
    }
    case "float": {
      const value = Number(trimmed);
      return Number.isFinite(value) ? value : undefined;
    }
    case "list":
      return trimmed.split(",").map((item) => item.trim()).filter(Boolean);
    case "str":
      // Quoted, so "2026" stays a string when the CLI reads it as YAML.
      return JSON.stringify(text);
    default:
      return trimmed;
  }
}

function outOfRange(field: CatalogField, value: unknown): string | null {
  if (typeof value !== "number") return null;
  if (field.minimum != null && value < field.minimum) return `at least ${field.minimum}`;
  if (field.maximum != null && value > field.maximum) return `at most ${field.maximum}`;
  return null;
}

/** Text the person owns while focused; the server's value otherwise. */
export function useDraftText(value: string, onCommit: (text: string) => void) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  const last = useRef(value);
  const timer = useRef<number>();
  useEffect(() => {
    if (!focused.current) {
      setText(value);
      last.current = value;
    }
  }, [value]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const flush = (next: string) => {
    window.clearTimeout(timer.current);
    if (next !== last.current) {
      last.current = next;
      onCommit(next);
    }
  };
  return {
    text,
    onChange: (next: string) => {
      setText(next);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => flush(next), COMMIT_DELAY_MS);
    },
    onFocus: () => {
      focused.current = true;
    },
    onBlur: () => {
      focused.current = false;
      flush(text);
    },
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.key === "Enter" && !(event.target instanceof HTMLTextAreaElement)) flush(text);
    },
  };
}

/** What a field means, what it takes, and what we recommend. */
export function FieldHelp({ field, open }: { field: CatalogField; open: boolean }) {
  if (!open) return null;
  const range =
    field.minimum != null || field.maximum != null
      ? `${field.minimum ?? "…"} – ${field.maximum ?? "…"}${field.unit ? ` ${field.unit}` : ""}`
      : null;
  return (
    <div className="fldhelp" role="note">
      <div>{field.help}</div>
      {field.values ? (
        <div>
          <b>Values</b> {field.values}
        </div>
      ) : null}
      <div className="muted">
        {range ? <>range {range} · </> : null}
        default <code>{field.default === null || field.default === undefined ? "unset" : JSON.stringify(field.default)}</code>
        {field.moves_digest ? " · changes the config digest" : " · does not change the digest"}
        {" · "}
        <code>--set {field.key}=…</code>
      </div>
      {field.recommended ? (
        <div>
          <b>Recommended</b> <code>{field.recommended}</code>
        </div>
      ) : null}
    </div>
  );
}

export function SettingField({
  field,
  value,
  overridden,
  onCommit,
  compact,
}: {
  field: CatalogField;
  value: unknown;
  overridden: boolean;
  onCommit: Commit;
  compact?: boolean;
}) {
  const [help, setHelp] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // A field can be shown twice (its own card and "Every setting"), so its
  // element id is per instance; `data-key` names the setting.
  const id = useId();
  const shown = display(field, value);
  const draft = useDraftText(shown, (text) => {
    const parsed = parse(field, text);
    if (parsed === undefined) {
      setProblem(field.kind === "int" ? "a whole number" : "a number");
      return;
    }
    const range = outOfRange(field, parsed);
    setProblem(range);
    if (range) return;
    onCommit(field.key, parsed);
  });

  const label = (
    <label htmlFor={id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <span>{field.label}</span>
      {field.unit ? <span className="muted">({field.unit})</span> : null}
      <button
        type="button"
        className="fldq"
        aria-label={`What is ${field.label}?`}
        aria-expanded={help}
        title={field.help}
        onClick={() => setHelp((v) => !v)}
      >
        ?
      </button>
      {field.recommended && field.recommended !== value ? (
        <button
          type="button"
          className="fldrec"
          title={`Recommended: ${field.recommended}`}
          onClick={() => onCommit(field.key, field.recommended)}
        >
          use {field.recommended}
        </button>
      ) : null}
    </label>
  );

  let control: React.ReactNode;
  if (field.kind === "bool") {
    const on = value === true;
    control = (
      <button
        id={id}
        data-key={field.key}
        type="button"
        className={`btn btn--small${on ? " btn--primary" : ""}`}
        onClick={() => onCommit(field.key, !on)}
        aria-pressed={on}
      >
        {on ? "on" : "off"}
      </button>
    );
  } else if (field.kind === "enum" && field.choices) {
    control = (
      <select
        id={id}
        data-key={field.key}
        className="input"
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(event) => {
          const next = event.target.value;
          onCommit(field.key, next === "" ? (field.nullable ? "null" : null) : next);
        }}
      >
        {field.choices.map((choice) => (
          <option key={String(choice)} value={choice === null ? "" : String(choice)}>
            {choice === null ? "(default)" : String(choice)}
          </option>
        ))}
      </select>
    );
  } else if (field.kind === "map" || field.kind === "any") {
    control = (
      <textarea
        id={id}
        data-key={field.key}
        className="input mono"
        rows={2}
        value={draft.text}
        placeholder="YAML or JSON"
        onChange={(event) => draft.onChange(event.target.value)}
        onFocus={draft.onFocus}
        onBlur={draft.onBlur}
      />
    );
  } else {
    const listId = field.suggestions ? `${id}-list` : undefined;
    control = (
      <>
        <input
          id={id}
        data-key={field.key}
          className={`input${field.kind === "str" || field.kind === "list" ? "" : " mono"}`}
          inputMode={field.kind === "int" ? "numeric" : field.kind === "float" ? "decimal" : undefined}
          list={field.kind === "list" ? undefined : listId}
          value={draft.text}
          placeholder={field.nullable ? "unset" : undefined}
          onChange={(event) => draft.onChange(event.target.value)}
          onFocus={draft.onFocus}
          onBlur={draft.onBlur}
          onKeyDown={draft.onKeyDown}
          spellCheck={false}
          autoComplete="off"
        />
        {listId ? (
          <datalist id={listId}>
            {field.suggestions!.map((option) => (
              <option key={option} value={option} />
            ))}
          </datalist>
        ) : null}
      </>
    );
  }

  return (
    <div className={`fld${overridden ? " fld--set" : ""}`}>
      {label}
      {control}
      <div className="h">
        {problem ? (
          <span className="danger-text">needs {problem}</span>
        ) : overridden ? (
          <button type="button" className="btn btn--ghost btn--small" style={{ padding: 0 }} onClick={() => onCommit(field.key, null)}>
            set by you · reset
          </button>
        ) : compact ? null : (
          field.values || "from the file or the profile"
        )}
        {field.kind === "list" && field.suggestions ? (
          <span className="muted"> · e.g. {field.suggestions.slice(0, 5).join(", ")}</span>
        ) : null}
      </div>
      <FieldHelp field={field} open={help} />
    </div>
  );
}
