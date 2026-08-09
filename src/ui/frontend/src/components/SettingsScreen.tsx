import { useEffect, useState } from "react";
import { EffectiveConfig, getConfig, resetEnvOverrides, updateEnvOverrides, updatePlatformConfig } from "../api";

// Part 1.6.A: platform/system settings -- ports, base_url, retry/extraction
// defaults. Distinct from the Pipeline screen's Advanced panel: everything
// here is the DEFAULT a run starts from on this machine, persisted to a
// gitignored overlay file (never config.yaml, never a run-by-run value).

interface FormState {
  destination: { type: string; host: string; port: string; schema: string };
  llm: { base_url: string };
  retry: { max_attempts: string; backoff: string; min_backoff_seconds: string; max_backoff_seconds: string };
  extraction: { default_batch_size: string; max_batch_size: string };
}

function toForm(effective: Record<string, any>): FormState {
  const d = effective.destination || {};
  const l = effective.llm || {};
  const r = effective.retry || {};
  const e = effective.extraction || {};
  return {
    destination: {
      type: String(d.type ?? ""),
      host: String(d.host ?? ""),
      port: d.port != null ? String(d.port) : "",
      schema: String(d.schema ?? ""),
    },
    llm: { base_url: String(l.base_url ?? "") },
    retry: {
      max_attempts: r.max_attempts != null ? String(r.max_attempts) : "",
      backoff: String(r.backoff ?? ""),
      min_backoff_seconds: r.min_backoff_seconds != null ? String(r.min_backoff_seconds) : "",
      max_backoff_seconds: r.max_backoff_seconds != null ? String(r.max_backoff_seconds) : "",
    },
    extraction: {
      default_batch_size: e.default_batch_size != null ? String(e.default_batch_size) : "",
      max_batch_size: e.max_batch_size != null ? String(e.max_batch_size) : "",
    },
  };
}

function Provenance({ source }: { source?: string }) {
  if (!source) return null;
  const label = source === "override" ? "overridden here" : source === "env" ? "from .env" : "config.yaml default";
  const cls = source === "override" ? "banner-success" : source === "env" ? "banner-info" : "";
  return <span className={`provenance-badge ${cls}`}>{label}</span>;
}

export default function SettingsScreen() {
  const [cfg, setCfg] = useState<EffectiveConfig | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [baseline, setBaseline] = useState<FormState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Session environment override state
  const [envInputs, setEnvInputs] = useState<Record<string, string>>({});
  const [envSaving, setEnvSaving] = useState(false);
  const [envMsg, setEnvMsg] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getConfig()
      .then((c) => {
        setCfg(c);
        const f = toForm(c.effective);
        setForm(f);
        setBaseline(f);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  /** Only the fields that changed since baseline, undefined for the rest. */
  function changedOnly<S extends Record<string, string>>(
    current: S,
    base: S,
  ): Partial<Record<keyof S, string>> {
    const out: Partial<Record<keyof S, string>> = {};
    for (const key of Object.keys(current) as (keyof S)[]) {
      if (current[key] !== base[key] && current[key] !== "") out[key] = current[key];
    }
    return out;
  }

  async function save() {
    if (!form || !baseline) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const dest = changedOnly(form.destination, baseline.destination);
      const llm = changedOnly(form.llm, baseline.llm);
      const retry = changedOnly(form.retry, baseline.retry);
      const extraction = changedOnly(form.extraction, baseline.extraction);
      await updatePlatformConfig({
        destination: {
          type: dest.type,
          host: dest.host,
          port: dest.port !== undefined ? Number(dest.port) : undefined,
          schema: dest.schema,
        },
        llm: { base_url: llm.base_url },
        retry: {
          max_attempts: retry.max_attempts !== undefined ? Number(retry.max_attempts) : undefined,
          backoff: retry.backoff,
          min_backoff_seconds:
            retry.min_backoff_seconds !== undefined ? Number(retry.min_backoff_seconds) : undefined,
          max_backoff_seconds:
            retry.max_backoff_seconds !== undefined ? Number(retry.max_backoff_seconds) : undefined,
        },
        extraction: {
          default_batch_size:
            extraction.default_batch_size !== undefined ? Number(extraction.default_batch_size) : undefined,
          max_batch_size:
            extraction.max_batch_size !== undefined ? Number(extraction.max_batch_size) : undefined,
        },
      });
      setSaved(true);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleApplyEnv() {
    setEnvSaving(true);
    setEnvMsg(null);
    try {
      const res = await updateEnvOverrides(envInputs);
      if (cfg) {
        setCfg({
          ...cfg,
          env_present: res.env_present,
          env_details: res.env_details,
        });
      }
      setEnvInputs({});
      setEnvMsg("✓ Session environment overrides applied to runtime memory.");
    } catch (e) {
      setEnvMsg(`Error: ${String(e)}`);
    } finally {
      setEnvSaving(false);
    }
  }

  async function handleResetEnv() {
    setEnvSaving(true);
    setEnvMsg(null);
    try {
      const res = await resetEnvOverrides();
      if (cfg) {
        setCfg({
          ...cfg,
          env_present: res.env_present,
          env_details: res.env_details,
        });
      }
      setEnvInputs({});
      setEnvMsg("✓ Reset all session overrides back to .env defaults.");
    } catch (e) {
      setEnvMsg(`Error: ${String(e)}`);
    } finally {
      setEnvSaving(false);
    }
  }

  if (loading) return <div className="card">Loading settings…</div>;
  if (error && !cfg) return <div className="card"><div className="banner banner-error">{error}</div></div>;
  if (!cfg || !form) return null;

  const src = cfg.sources;
  const envEntries: Array<[string, { status: string; is_set: boolean; masked_value?: string }]> = cfg.env_details
    ? Object.entries(cfg.env_details)
    : Object.entries(cfg.env_present || {}).map(([name, present]) => [
        name,
        { status: present ? "set" : "not_set", is_set: present, masked_value: present ? "****" : "" },
      ]);

  return (
    <div>
      <div className="card">
        <h3 className="card-title">Platform defaults</h3>
        <p className="card-hint">
          These are the defaults every new run starts from on this machine —
          saved to a gitignored overlay file, never to config.yaml. Moving
          this project to another machine means setting these here, not
          editing a tracked file. A run's own Advanced panel can still
          override any of this for a single merge.
        </p>

        <div className="section-label">Destination</div>
        <div className="form-grid">
          <div className="field">
            <label>Type <Provenance source={src.destination?.type} /></label>
            <select
              value={form.destination.type}
              onChange={(e) => setForm({ ...form, destination: { ...form.destination, type: e.target.value } })}
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="clickhouse">ClickHouse</option>
            </select>
          </div>
          <div className="field">
            <label>Default host <Provenance source={src.destination?.host} /></label>
            <input
              value={form.destination.host}
              onChange={(e) => setForm({ ...form, destination: { ...form.destination, host: e.target.value } })}
            />
          </div>
          <div className="field">
            <label>Default port <Provenance source={src.destination?.port} /></label>
            <input
              value={form.destination.port}
              onChange={(e) => setForm({ ...form, destination: { ...form.destination, port: e.target.value } })}
            />
          </div>
          <div className="field">
            <label>Default schema <Provenance source={src.destination?.schema} /></label>
            <input
              value={form.destination.schema}
              onChange={(e) => setForm({ ...form, destination: { ...form.destination, schema: e.target.value } })}
            />
          </div>
        </div>

        <div className="section-label" style={{ marginTop: 18 }}>LLM</div>
        <div className="form-grid">
          <div className="field wide">
            <label>Base URL <Provenance source={src.llm?.base_url} /></label>
            <input
              value={form.llm.base_url}
              onChange={(e) => setForm({ ...form, llm: { base_url: e.target.value } })}
              placeholder="https://api.your-provider.example/v1"
            />
          </div>
        </div>

        <div className="section-label" style={{ marginTop: 18 }}>Retry</div>
        <div className="form-grid">
          <div className="field">
            <label>Max attempts <Provenance source={src.retry?.max_attempts} /></label>
            <input
              value={form.retry.max_attempts}
              onChange={(e) => setForm({ ...form, retry: { ...form.retry, max_attempts: e.target.value } })}
            />
          </div>
          <div className="field">
            <label>Backoff <Provenance source={src.retry?.backoff} /></label>
            <select
              value={form.retry.backoff}
              onChange={(e) => setForm({ ...form, retry: { ...form.retry, backoff: e.target.value } })}
            >
              <option value="exponential">Exponential</option>
              <option value="linear">Linear</option>
            </select>
          </div>
          <div className="field">
            <label>Min backoff (s) <Provenance source={src.retry?.min_backoff_seconds} /></label>
            <input
              value={form.retry.min_backoff_seconds}
              onChange={(e) =>
                setForm({ ...form, retry: { ...form.retry, min_backoff_seconds: e.target.value } })
              }
            />
          </div>
          <div className="field">
            <label>Max backoff (s) <Provenance source={src.retry?.max_backoff_seconds} /></label>
            <input
              value={form.retry.max_backoff_seconds}
              onChange={(e) =>
                setForm({ ...form, retry: { ...form.retry, max_backoff_seconds: e.target.value } })
              }
            />
          </div>
        </div>

        <div className="section-label" style={{ marginTop: 18 }}>Extraction</div>
        <div className="form-grid">
          <div className="field">
            <label>Default batch size <Provenance source={src.extraction?.default_batch_size} /></label>
            <input
              value={form.extraction.default_batch_size}
              onChange={(e) =>
                setForm({ ...form, extraction: { ...form.extraction, default_batch_size: e.target.value } })
              }
            />
          </div>
          <div className="field">
            <label>Max batch size <Provenance source={src.extraction?.max_batch_size} /></label>
            <input
              value={form.extraction.max_batch_size}
              onChange={(e) =>
                setForm({ ...form, extraction: { ...form.extraction, max_batch_size: e.target.value } })
              }
            />
          </div>
        </div>

        {error && <div className="banner banner-error" style={{ marginTop: 14 }}>{error}</div>}
        {saved && !error && <div className="banner banner-success" style={{ marginTop: 14 }}>Saved.</div>}

        <div className="btn-row" style={{ marginTop: 16 }}>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save platform settings"}
          </button>
          <button className="btn btn-ghost" onClick={load} disabled={saving}>
            Reload
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <h3 className="card-title">Environment (.env & Session Overrides)</h3>
        <p className="card-hint">
          Values loaded from <code>.env</code> on launch act as default settings. You can enter session overrides below
          which immediately update active runtime environment variables in memory.
          <br />
          <strong style={{ color: "#94a3b8" }}>
            ⚡ Note: Session overrides remain active until process restart (Ctrl+C in terminal), which automatically restores .env defaults.
          </strong>
        </p>

        {envMsg && <div className="banner banner-info" style={{ marginBottom: 12 }}>{envMsg}</div>}

        <div className="form-grid" style={{ gap: 14 }}>
          {envEntries.map(([name, detail]) => {
            const isOverridden = detail.status === "overridden";
            const isSet = detail.is_set;
            const badgeClass = isOverridden
              ? "banner-info provenance-badge"
              : isSet
              ? "banner-success provenance-badge"
              : "banner-error provenance-badge";
            const badgeText = isOverridden
              ? "⚡ overridden (session)"
              : isSet
              ? "✓ set (.env default)"
              : "not set";

            return (
              <div key={name} className="field wide" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <label className="mono" style={{ fontWeight: 600 }}>{name}</label>
                  <span className={badgeClass}>{badgeText}</span>
                </div>
                <input
                  type="text"
                  placeholder={detail.masked_value ? `Active: ${detail.masked_value} — type new value to override` : "Enter new value to override..."}
                  value={envInputs[name] ?? ""}
                  onChange={(e) => setEnvInputs({ ...envInputs, [name]: e.target.value })}
                />
              </div>
            );
          })}
        </div>

        <div className="btn-row" style={{ marginTop: 16 }}>
          <button className="btn btn-primary" onClick={handleApplyEnv} disabled={envSaving}>
            {envSaving ? "Applying..." : "Apply Session Overrides"}
          </button>
          <button className="btn btn-ghost" onClick={handleResetEnv} disabled={envSaving}>
            Reset to .env Defaults
          </button>
        </div>
      </div>
    </div>
  );
}
