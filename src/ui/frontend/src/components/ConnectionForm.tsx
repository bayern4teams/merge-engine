import { useState } from "react";
import { testConnection, DiscoverResult, RecentItem } from "../api";

const DEFAULT_PORTS: Record<string, number> = {
  postgres: 5432,
  postgresql: 5432,
  clickhouse: 8123,
  mongodb: 27017,
};

export interface ConnValues {
  type: string;
  host: string;
  port: string;
  user: string;
  password: string;
  database: string;
  schema: string;
}

export const emptyConn = (type = "postgres"): ConnValues => ({
  type,
  host: "",
  port: String(DEFAULT_PORTS[type] ?? 5432),
  user: "",
  password: "",
  database: "",
  schema: "public",
});

interface Props {
  title: string;
  hint: string;
  showSchema: boolean;
  recent: RecentItem[];
  onSubmit: (values: ConnValues, discovery: DiscoverResult | null) => void;
  submitLabel: string;
  submitDisabled?: boolean;
}

export default function ConnectionForm({
  title,
  hint,
  showSchema,
  recent,
  onSubmit,
  submitLabel,
  submitDisabled,
}: Props) {
  const [v, setV] = useState<ConnValues>(emptyConn());
  const [discovery, setDiscovery] = useState<DiscoverResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<keyof ConnValues, string>>>({});

  const REQUIRED: (keyof ConnValues)[] = ["host", "user", "database"];

  function set<K extends keyof ConnValues>(k: K, val: ConnValues[K]) {
    setV((prev) => ({ ...prev, [k]: val }));
    if (errors[k] && typeof val === "string" && val.trim()) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[k];
        return next;
      });
    }
  }

  function validate(k: keyof ConnValues) {
    if (!REQUIRED.includes(k)) return;
    setErrors((prev) => {
      const next = { ...prev };
      if (!String(v[k] ?? "").trim()) next[k] = "Required";
      else delete next[k];
      return next;
    });
  }

  function onTypeChange(type: string) {
    setV((prev) => ({ ...prev, type, port: String(DEFAULT_PORTS[type] ?? 5432) }));
    setDiscovery(null);
  }

  function loadRecent(label: string) {
    const item = recent.find((r) => r.label === label);
    if (!item) return;
    const c = item.connection;
    setV({
      type: item.type,
      host: c.host ?? "",
      port: String(c.port ?? DEFAULT_PORTS[item.type] ?? 5432),
      user: c.user ?? "",
      password: c.password ?? "",
      database: c.database ?? "",
      schema: c.schema ?? "public",
    });
    setDiscovery(null);
    setErrors({});
  }

  async function runTest() {
    if (!v.host || !v.user) return;
    setTesting(true);
    try {
      const result = await testConnection({
        conn_type: v.type,
        host: v.host,
        port: Number(v.port) || DEFAULT_PORTS[v.type] || 5432,
        user: v.user,
        password: v.password,
        database: v.database,
        schema_name: v.schema,
      });
      setDiscovery(result);
    } catch (e) {
      setDiscovery({
        connected: false,
        error: String(e),
        databases: [],
        schemas: [],
        tables: [],
        table_columns: {},
        table_row_counts: {},
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">{title}</h3>
      <p className="card-hint">{hint}</p>

      {recent.length > 0 && (
        <div className="field wide" style={{ marginBottom: 14 }}>
          <label>Recent connections</label>
          <select
            defaultValue=""
            onChange={(e) => e.target.value && loadRecent(e.target.value)}
          >
            <option value="">— select a saved connection —</option>
            {recent.map((r) => (
              <option key={r.label} value={r.label}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="form-grid">
        <div className="field">
          <label>Type</label>
          <select value={v.type} onChange={(e) => onTypeChange(e.target.value)}>
            <option value="postgres">PostgreSQL</option>
            <option value="clickhouse">ClickHouse</option>
            <option value="mongodb">MongoDB</option>
          </select>
        </div>
        <div className={`field ${errors.host ? "invalid" : ""}`}>
          <label>Host</label>
          <input
            value={v.host}
            onChange={(e) => set("host", e.target.value)}
            onBlur={() => { validate("host"); runTest(); }}
            placeholder="localhost"
          />
          {errors.host && <span className="field-error">{errors.host}</span>}
        </div>
        <div className="field">
          <label>Port</label>
          <input value={v.port} onChange={(e) => set("port", e.target.value)} onBlur={runTest} />
        </div>
        <div className={`field ${errors.database ? "invalid" : ""}`}>
          <label>Database</label>
          <input
            value={v.database}
            onChange={(e) => set("database", e.target.value)}
            onBlur={() => { validate("database"); runTest(); }}
            placeholder="crm_origin"
          />
          {errors.database && <span className="field-error">{errors.database}</span>}
        </div>
        <div className={`field ${errors.user ? "invalid" : ""}`}>
          <label>Username</label>
          <input
            value={v.user}
            onChange={(e) => set("user", e.target.value)}
            onBlur={() => { validate("user"); runTest(); }}
          />
          {errors.user && <span className="field-error">{errors.user}</span>}
        </div>
        <div className="field">
          <label>Password</label>
          <input type="password" value={v.password} onChange={(e) => set("password", e.target.value)} onBlur={runTest} />
        </div>
        {showSchema && v.type !== "clickhouse" && (
          <div className="field">
            <label>Schema</label>
            <input value={v.schema} onChange={(e) => set("schema", e.target.value)} placeholder="public" />
          </div>
        )}
      </div>

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button className="btn btn-ghost" onClick={runTest} disabled={testing || !v.host}>
          {testing ? "Testing…" : "Test connection"}
        </button>
        <button
          className="btn btn-primary"
          onClick={() => onSubmit(v, discovery)}
          disabled={submitDisabled || !v.host || !v.user || !v.database}
        >
          {submitLabel}
        </button>
      </div>

      {discovery && <DiscoveryView d={discovery} />}
    </div>
  );
}

function DiscoveryView({ d }: { d: DiscoverResult }) {
  if (d.error && !d.connected) {
    return <div className="banner banner-error">✗ {d.error}</div>;
  }
  return (
    <div className="discovery">
      <div className="banner banner-success" style={{ margin: "0 0 12px" }}>
        ✓ Connected
      </div>
      {d.databases.length > 0 && (
        <>
          <div className="section-label">Databases</div>
          <div className="chip-row">
            {d.databases.map((db) => (
              <span key={db} className="chip">{db}</span>
            ))}
          </div>
        </>
      )}
      {d.tables.length > 0 && (
        <>
          <div className="section-label">Tables ({d.tables.length})</div>
          <div className="table-list">
            {d.tables.map((t) => (
              <div key={t} className="table-list-row">
                <span className="mono">{t}</span>
                <span className="count">
                  {d.table_row_counts[t] >= 0 ? `${d.table_row_counts[t]} rows` : ""}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
