import { useEffect, useState } from "react";
import { OriginConfig, DestinationConfig } from "./api";
import SetupScreen from "./components/SetupScreen";
import PipelineScreen from "./components/PipelineScreen";

type Tab = "setup" | "pipeline";
type Theme = "light" | "dark";

export default function App() {
  const [tab, setTab] = useState<Tab>("setup");
  const [origins, setOrigins] = useState<OriginConfig[]>([]);
  const [destination, setDestination] = useState<DestinationConfig | null>(null);
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("me-theme") as Theme) || "light",
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("me-theme", theme);
  }, [theme]);

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
      </div>

      {tab === "setup" ? (
        <SetupScreen
          origins={origins}
          destination={destination}
          onOriginsChange={setOrigins}
          onDestinationChange={setDestination}
        />
      ) : (
        <PipelineScreen origins={origins} destination={destination} />
      )}
    </div>
  );
}
