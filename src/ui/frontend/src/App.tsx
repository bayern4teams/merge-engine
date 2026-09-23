import { useEffect, useState } from "react";
import { OriginConfig, DestinationConfig } from "./api";
import SetupScreen from "./components/SetupScreen";
import PipelineScreen from "./components/PipelineScreen";
import SettingsScreen from "./components/SettingsScreen";
import InvestigateScreen from "./components/InvestigateScreen";
import MetadataGraphScreen from "./components/MetadataGraphScreen";
import SplinkWorkbenchScreen from "./components/SplinkWorkbenchScreen";
import {
  loadOriginsFromStorage,
  saveOriginsToStorage,
  loadDestinationFromStorage,
  saveDestinationToStorage,
  loadActiveTabFromStorage,
  saveActiveTabToStorage,
} from "./store";

type Tab = "setup" | "pipeline" | "settings" | "investigate" | "graph" | "splink";
type Theme = "light" | "dark";

export default function App() {
  const [tab, setTabState] = useState<Tab>(() => (loadActiveTabFromStorage() as Tab) || "setup");
  const [origins, setOriginsState] = useState<OriginConfig[]>(() => loadOriginsFromStorage());
  const [destination, setDestinationState] = useState<DestinationConfig | null>(() =>
    loadDestinationFromStorage(),
  );
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("me-theme") as Theme) || "light",
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("me-theme", theme);
  }, [theme]);

  function setTab(t: Tab) {
    setTabState(t);
    saveActiveTabToStorage(t);
  }

  function setOrigins(o: OriginConfig[]) {
    setOriginsState(o);
    saveOriginsToStorage(o);
  }

  function setDestination(d: DestinationConfig | null) {
    setDestinationState(d);
    saveDestinationToStorage(d);
  }

  const ready = origins.length > 0 && !!destination;

  return (
    <div className="app">
      <div className="app-header">
        <div className="logo">⇄</div>
        <h1>merge-engine</h1>
        <button
          className="theme-toggle"
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label="Toggle dark mode"
        >
          {theme === "dark" ? "☀️" : "🌙"}
        </button>
      </div>
      <p className="app-subtitle">
        Merge multiple databases into one, with a human check at every step.
      </p>

      <div className="tabs">
        <button
          className={`tab ${tab === "setup" ? "active" : ""}`}
          onClick={() => setTab("setup")}
        >
          Setup
          {(origins.length > 0 || destination) && (
            <span className="tab-badge">{origins.length + (destination ? 1 : 0)}</span>
          )}
        </button>
        <button
          className={`tab ${tab === "pipeline" ? "active" : ""}`}
          onClick={() => setTab("pipeline")}
        >
          Pipeline
          {ready && <span className="tab-badge">✓</span>}
        </button>
        <button
          className={`tab ${tab === "settings" ? "active" : ""}`}
          onClick={() => setTab("settings")}
        >
          Settings
        </button>
        <button
          className={`tab ${tab === "investigate" ? "active" : ""}`}
          onClick={() => setTab("investigate")}
        >
          Investigate
        </button>
        <button
          className={`tab ${tab === "graph" ? "active" : ""}`}
          onClick={() => setTab("graph")}
        >
          Graph
        </button>
        <button
          className={`tab ${tab === "splink" ? "active" : ""}`}
          onClick={() => setTab("splink")}
        >
          Splink
        </button>
      </div>

      <div style={{ display: tab === "setup" ? "block" : "none" }}>
        <SetupScreen
          origins={origins}
          destination={destination}
          onOriginsChange={setOrigins}
          onDestinationChange={setDestination}
        />
      </div>
      <div style={{ display: tab === "pipeline" ? "block" : "none" }}>
        <PipelineScreen origins={origins} destination={destination} />
      </div>
      <div style={{ display: tab === "settings" ? "block" : "none" }}>
        <SettingsScreen />
      </div>
      <div style={{ display: tab === "investigate" ? "block" : "none" }}>
        <InvestigateScreen />
      </div>
      <div style={{ display: tab === "graph" ? "block" : "none" }}>
        <MetadataGraphScreen />
      </div>
      <div style={{ display: tab === "splink" ? "block" : "none" }}>
        <SplinkWorkbenchScreen />
      </div>
    </div>
  );
}
