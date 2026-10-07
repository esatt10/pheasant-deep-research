import { NavLink } from "react-router-dom";
import { RegionChip } from "./RegionChip";
import { useTheme } from "../hooks/useTheme";
import type { AccessState } from "../App";

export function TopBar({
  config,
  cost,
  access,
  onKey,
}: {
  config?: string;
  cost?: { spent: number; budget: number };
  access?: AccessState;
  onKey: () => void;
}) {
  const [theme, toggle] = useTheme();
  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "navlink active" : "navlink");
  return (
    <header className="topbar">
      <div className="topbar__brand">
        <img src="/pheasant.png" alt="" />
        pheasant <span className="swarm">swarm</span>
        {config ? <span className="topbar__kb">{config}</span> : null}
      </div>
      <nav className="topbar__nav">
        <NavLink to="/" end className={link}>
          Runs
        </NavLink>
        <NavLink to="/configure" className={link}>
          Configure
        </NavLink>
        <NavLink to="/live" className={link}>
          Live
        </NavLink>
        <NavLink to="/reports" className={link}>
          Reports
        </NavLink>
        <NavLink to="/logs" className={link}>
          Logs
        </NavLink>
      </nav>
      <div className="topbar__right">
        <RegionChip config={config} />
        {cost ? (
          <span className="pill">
            <span className="mono">${cost.spent.toFixed(2)}</span>&nbsp;of ${cost.budget.toFixed(2)}
          </span>
        ) : null}
        {access?.required ? (
          <button
            className={`pill${access.authenticated ? " pill--accent" : " keybtn--locked"}`}
            onClick={onKey}
            title={access.authenticated ? "Connected with an access key — click to change or disconnect" : "This console needs an access key"}
          >
            {access.authenticated ? "🔓 connected" : "🔒 key needed"}
          </button>
        ) : null}
        <button className="btn btn--ghost btn--small" onClick={toggle} title="Toggle theme">
          {theme === "light" ? "☾" : "☀"}
        </button>
      </div>
    </header>
  );
}
