import { useDraftText } from "./fields";

/**
 * The budget: the run's hard dollar ceiling, its time ceiling, how the dollars
 * are split across phases, and a cap on the next launch alone. The guard
 * reserves every call's worst case against these before the call is made.
 */

const SHARES = ["planning", "collection", "benchmark", "evaluation", "reserve"];

export function Budget({
  render,
  allocation,
  launchCap,
  onLaunchCap,
}: {
  render: (key: string, compact?: boolean) => React.ReactNode;
  allocation: number[];
  launchCap: number | null;
  onLaunchCap: (cap: number | null) => void;
}) {
  const total = allocation.reduce((sum, share) => sum + share, 0);
  const balanced = Math.abs(total - 1) < 1e-6;
  const cap = useDraftText(launchCap == null ? "" : String(launchCap), (text) => {
    const trimmed = text.trim();
    if (trimmed === "") return onLaunchCap(null);
    const value = Number(trimmed);
    if (Number.isFinite(value) && value > 0) onLaunchCap(value);
  });
  return (
    <div className="card" id="budget">
      <div className="card__head">
        Budget <span className="sub">reserved before every call, reconciled after — a run cannot overshoot it</span>
      </div>
      <div className="card__body grid3">
        {render("experiment.cost_budget_usd")}
        {render("experiment.runtime_budget_minutes")}
        <div className="fld">
          <label htmlFor="launch-cap">this launch only (USD)</label>
          <input
            id="launch-cap"
            className="input mono"
            inputMode="decimal"
            placeholder="no extra cap"
            value={cap.text}
            onChange={(e) => cap.onChange(e.target.value)}
            onFocus={cap.onFocus}
            onBlur={cap.onBlur}
            onKeyDown={cap.onKeyDown}
          />
          <div className="h">
            <code>--max-cost-usd</code>: a lower ceiling for the next launch; not part of the config digest
          </div>
        </div>
      </div>
      <div className="card__body" style={{ paddingTop: 0 }}>
        <div className="eyebrow" style={{ margin: "4px 0", display: "flex", gap: 8 }}>
          Allocation by phase
          <span className={`pill ${balanced ? "pill--accent" : "pill--danger"}`} style={{ textTransform: "none", letterSpacing: 0 }}>
            sum {total.toFixed(2)} {balanced ? "✓" : "— must be 1.00"}
          </span>
        </div>
        <div className="sharebar" aria-hidden>
          {allocation.map((share, i) => (
            <span key={SHARES[i]} style={{ flex: Math.max(share, 0.0001) }} title={`${SHARES[i]} ${Math.round(share * 100)}%`}>
              {share >= 0.08 ? `${SHARES[i]} ${Math.round(share * 100)}%` : ""}
            </span>
          ))}
        </div>
        <div className="grid5" style={{ marginTop: 8 }}>
          {SHARES.map((share) => render(`budget.allocation.${share}`, true))}
        </div>
        <div className="grid3" style={{ marginTop: 8 }}>
          {render("stopping.evaluation_budget_reserve_fraction", true)}
          {render("budget.reserve_output_at_max_tokens", true)}
          {render("budget.assumed_tool_call_output_tokens", true)}
        </div>
      </div>
    </div>
  );
}
