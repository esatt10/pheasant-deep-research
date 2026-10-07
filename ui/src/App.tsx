import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, onAuthChanged, onAuthRequired, onInflight } from "./api";
import { AccessDialog } from "./components/AccessDialog";
import { TopBar } from "./components/TopBar";
import { RunsPage } from "./pages/RunsPage";
import { ConfigurePage } from "./pages/ConfigurePage";
import { LivePage } from "./pages/LivePage";
import { LogsPage } from "./pages/LogsPage";
import { ReportsPage } from "./pages/ReportsPage";

const CONFIG_KEY = "pheasant-lab-config";

function storedConfig(): string | undefined {
  try {
    return localStorage.getItem(CONFIG_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export type AccessState = { required: boolean; authenticated: boolean } | undefined;

export function App() {
  const [config, setConfigState] = useState<string | undefined>(storedConfig);
  const [cost, setCost] = useState<{ spent: number; budget: number }>();
  const [access, setAccess] = useState<AccessState>();
  const [askKey, setAskKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const setConfig = (value: string) => {
    setConfigState(value);
    try {
      localStorage.setItem(CONFIG_KEY, value);
    } catch {
      /* storage unavailable */
    }
  };

  // Whether this console has a key at all, and whether this tab holds it.
  useEffect(() => {
    const check = () =>
      void api
        .auth()
        .then((state) => {
          setAccess(state);
          if (state.required && !state.authenticated) setAskKey(true);
        })
        .catch(() => setAccess(undefined));
    check();
    const offChanged = onAuthChanged(check);
    const offRequired = onAuthRequired(() => {
      setAccess((current) => (current ? { ...current, authenticated: false } : current));
      setAskKey(true);
    });
    return () => {
      offChanged();
      offRequired();
    };
  }, []);

  // Debounced, so a request that answers in 40ms never flashes the bar.
  useEffect(() => {
    let timer: number | undefined;
    const off = onInflight((n) => {
      window.clearTimeout(timer);
      if (n > 0) timer = window.setTimeout(() => setBusy(true), 120);
      else setBusy(false);
    });
    return () => {
      window.clearTimeout(timer);
      off();
    };
  }, []);

  return (
    <div className="app">
      <TopBar config={config} cost={cost} access={access} onKey={() => setAskKey(true)} />
      <div className={`loadbar${busy ? " loadbar--on" : ""}`} role="progressbar" aria-hidden={!busy} aria-label="Loading" />
      <main className="app__main">
        <Routes>
          <Route path="/" element={<RunsPage />} />
          <Route path="/configure" element={<ConfigurePage config={config} onConfig={setConfig} />} />
          <Route path="/live" element={<LivePage onCost={setCost} />} />
          <Route path="/live/:runId" element={<LivePage onCost={setCost} />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/reports/:runId" element={<ReportsPage />} />
          <Route path="/reports/:runId/traces" element={<ReportsPage view="traces" />} />
          <Route path="/reports/:runId/traces/:actor" element={<ReportsPage view="traces" />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {askKey ? <AccessDialog onClose={() => setAskKey(false)} /> : null}
    </div>
  );
}
