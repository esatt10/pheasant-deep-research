import { useEffect, useState } from "react";
import { api } from "../api";
import type { Catalog, Prices, Settings } from "../types";
import { SettingField, type Commit } from "./fields";

/**
 * Agents and their models. Each role says what it does, what it costs in
 * calls, and the model and reasoning level we recommend - with one click to
 * take the recommendation for that role or for all of them. Prices live here
 * too, because a model with no price is refused before any spend.
 */

export function Models({
  catalog,
  values,
  overrides,
  onCommit,
  onSettings,
  onError,
}: {
  catalog?: Catalog;
  values: Record<string, unknown>;
  overrides: Record<string, string>;
  onCommit: Commit;
  onSettings: (settings: Settings) => void;
  onError: (message: string | null) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const roles = catalog?.roles ?? [];
  const field = (key: string) => catalog?.fields.find((f) => f.key === key);
  const value = (key: string) => (key in values ? values[key] : field(key)?.current);

  const recommend = async (only?: string[]) => {
    try {
      onSettings(await api.recommendedModels(only));
      onError(null);
    } catch (caught) {
      onError((caught as Error).message);
    }
  };

  return (
    <div className="card" id="models">
      <div className="card__head">
        Agents & models <span className="sub">a model and reasoning level per role; recommendations from what each role does</span>
        <div className="r">
          <button className="btn btn--small" onClick={() => void recommend()} title="GPT-6.1 Sol (high) for planning, the benchmark and the answering arms; GPT-6 Luna (medium) for researchers and the auditor">
            Use recommended for all
          </button>
        </div>
      </div>
      <div className="card__body" style={{ padding: 0 }}>
        <table className="table roles">
          <thead>
            <tr>
              <th>role</th>
              <th>provider</th>
              <th>model</th>
              <th>reasoning</th>
              <th>max output</th>
              <th>recommended</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((row) => {
              const key = (name: string) => `models.${row.role}.${name}`;
              const cells = ["provider", "model", "reasoning_effort", "max_output_tokens"].map((name) => {
                const f = field(key(name));
                return (
                  <td key={name}>
                    {f ? <SettingField field={{ ...f, label: "" }} value={value(key(name))} overridden={key(name) in overrides} onCommit={onCommit} compact /> : null}
                  </td>
                );
              });
              const matches = value(key("model")) === row.model && value(key("reasoning_effort")) === row.reasoning_effort;
              return (
                <tr key={row.role}>
                  <td style={{ minWidth: 140 }}>
                    <button className="btn btn--ghost btn--small" style={{ padding: 0, fontWeight: 600 }} onClick={() => setOpen(open === row.role ? null : row.role)} aria-expanded={open === row.role}>
                      {open === row.role ? "▾" : "▸"} {row.role}
                    </button>
                    {open === row.role ? (
                      <div className="small" style={{ maxWidth: 260, marginTop: 4 }}>
                        <div>{row.does}</div>
                        <div className="muted">calls: {row.calls}</div>
                        <div className="muted">why: {row.why}</div>
                      </div>
                    ) : null}
                  </td>
                  {cells}
                  <td style={{ whiteSpace: "nowrap" }}>
                    {row.model ? (
                      matches ? (
                        <span className="pill pill--accent">✓ {row.model} · {row.reasoning_effort}</span>
                      ) : (
                        <button className="btn btn--small" onClick={() => void recommend([row.role])} title={row.why}>
                          {row.model} · {row.reasoning_effort}
                        </button>
                      )
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <PriceList onError={onError} onSettings={onSettings} />
    </div>
  );
}

function PriceList({ onError, onSettings }: { onError: (message: string | null) => void; onSettings: (settings: Settings) => void }) {
  const [prices, setPrices] = useState<Prices>();
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");

  const load = () => void api.prices().then(setPrices).catch(() => undefined);
  useEffect(load, []);

  const after = async (next: Prices) => {
    setPrices(next);
    onError(null);
    // A price changes whether the draft resolves; read it again.
    onSettings(await api.settings());
  };

  const add = async () => {
    try {
      await after(await api.setPrice(model.trim(), Number(input), Number(output)));
      setModel("");
      setInput("");
      setOutput("");
    } catch (caught) {
      onError((caught as Error).message);
    }
  };

  return (
    <div className="card__body" id="prices" style={{ borderTop: "1px solid var(--border)" }}>
      <div className="eyebrow" style={{ marginBottom: 6 }}>
        Model prices <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>· USD per million tokens · written to {prices?.local_file}</span>
      </div>
      {prices?.unpriced_in_use.length ? (
        <div className="pill pill--danger" style={{ whiteSpace: "normal", marginBottom: 6 }}>
          No price for {prices.unpriced_in_use.join(", ")} — doctor and the budget guard refuse a model treated as free. Enter its published price below.
        </div>
      ) : null}
      <table className="table">
        <thead>
          <tr>
            <th>model</th>
            <th>input</th>
            <th>output</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {Object.entries(prices?.models ?? {}).map(([name, price]) => (
            <tr key={name}>
              <td className="mono">{name}</td>
              <td>{price.input}</td>
              <td>{price.output}</td>
              <td style={{ textAlign: "right" }}>
                <button className="btn btn--ghost btn--small" onClick={() => { setModel(name); setInput(String(price.input)); setOutput(String(price.output)); }}>
                  edit
                </button>
                <button className="btn btn--ghost btn--small" aria-label={`Remove the price for ${name}`} onClick={async () => { try { await after(await api.deletePrice(name)); } catch (e) { onError((e as Error).message); } }}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
          <tr>
            <td>
              <input className="input mono" placeholder="e.g. gpt-6.1-sol" list="price-models" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model to price" />
              <datalist id="price-models">
                {(prices?.unpriced_in_use ?? []).concat(["gpt-6.1-sol", "gpt-6-luna"]).map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </td>
            <td>
              <input className="input" inputMode="decimal" placeholder="input" value={input} onChange={(e) => setInput(e.target.value)} aria-label="Input price" />
            </td>
            <td>
              <input className="input" inputMode="decimal" placeholder="output" value={output} onChange={(e) => setOutput(e.target.value)} aria-label="Output price" />
            </td>
            <td style={{ textAlign: "right" }}>
              <button className="btn btn--small btn--primary" disabled={!model.trim() || input === "" || output === "" || Number.isNaN(Number(input)) || Number.isNaN(Number(output))} onClick={() => void add()}>
                Set price
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
