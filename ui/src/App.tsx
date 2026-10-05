import { useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { TopBar } from "./components/TopBar";
import { RunsPage } from "./pages/RunsPage";
import { ConfigurePage } from "./pages/ConfigurePage";
import { LivePage } from "./pages/LivePage";
import { ReportsPage } from "./pages/ReportsPage";

const CONFIG_KEY = "pheasant-lab-config";

function storedConfig(): string | undefined {
  try {
    return localStorage.getItem(CONFIG_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export function App() {
  const [config, setConfigState] = useState<string | undefined>(storedConfig);
  const [cost, setCost] = useState<{ spent: number; budget: number }>();
  const setConfig = (value: string) => {
    setConfigState(value);
    try {
      localStorage.setItem(CONFIG_KEY, value);
    } catch {
      /* storage unavailable */
    }
  };
  return (
    <div className="app">
      <TopBar config={config} cost={cost} />
      <main className="app__main">
        <Routes>
          <Route path="/" element={<RunsPage />} />
          <Route path="/configure" element={<ConfigurePage config={config} onConfig={setConfig} />} />
          <Route path="/live" element={<LivePage onCost={setCost} />} />
          <Route path="/live/:runId" element={<LivePage onCost={setCost} />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/reports/:runId" element={<ReportsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
