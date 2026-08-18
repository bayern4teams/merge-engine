import { useState } from "react";
import {
  getMatches,
  getTimeline,
  sampleRows,
  getGoldenDiff,
  getLineage,
  MatchInspection,
  RunTimeline,
  SampleResult,
  GoldenDiff,
  LineageResult,
} from "../api";

// Part 1.6.B: four read-only investigation tools. None of these can mutate a
// run or write to any database -- every call here is a GET or a bounded,
// explicitly-limited sample.

export default function InvestigateScreen() {
  return (
    <div>
      <TimelineCard />
      <MatchInspectorCard />
      <LineageCard />
      <DataExplorerCard />
      <GoldenDiffCard />
    </div>
  );
}

// ── B.2: run trace & cost timeline ──────────────────────────────────────────

function TimelineCard() {
  const [threadId, setThreadId] = useState("");
  const [data, setData] = useState<RunTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    if (!threadId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setData(await getTimeline(threadId.trim()));
    } catch (e) {
      setError(String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  const maxSeconds = data ? Math.max(1, ...Object.values(data.timings)) : 1;

  return (
    <div className="card">
      <h3 className="card-title">Run trace & cost timeline</h3>
      <p className="card-hint">
        Per-node wall time from the run's own report, plus what it spent on
        LLM tokens. Enter a thread id (shown when a merge starts, e.g.{" "}
        <span className="mono">wf_a93e5822</span>).
      </p>
      <div className="preset-row">
        <input
          value={threadId}
          onChange={(e) => setThreadId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          placeholder="wf_..."
        />
        <button className="btn btn-primary" onClick={load} disabled={loading || !threadId.trim()}>
          {loading ? "Loading…" : "Load"}
        </button>
      </div>
      {error && <div className="banner banner-error" style={{ marginTop: 12 }}>{error}</div>}
      {data && !data.found && (
        <div className="banner banner-warn" style={{ marginTop: 12 }}>
          No run report found for that thread id yet — it may still be running (the
          report is written once the run finishes) or the id doesn't exist.
        </div>
      )}
      {data && data.found && (
        <div style={{ marginTop: 14 }}>
          <div className="section-label">Per-node duration</div>
          <div className="table-list">
            {Object.entries(data.timings).map(([node, seconds]) => (
              <div key={node} className="table-list-row timeline-row">
                <span className="mono">{node}</span>
                <div className="timeline-bar-track">
                  <div
                    className="timeline-bar"
                    style={{ width: `${Math.max(2, (seconds / maxSeconds) * 100)}%` }}
                  />
                </div>
                <span className="count">{seconds.toFixed(1)}s</span>
              </div>
            ))}
          </div>
          <div className="section-label" style={{ marginTop: 14 }}>Cost</div>
          <pre className="code-block">{JSON.stringify(data.cost.run, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

// ── B.1: match & cluster inspector ─────────────────────────────────────────

function MatchInspectorCard() {
  const [threadId, setThreadId] = useState("");
  const [data, setData] = useState<MatchInspection | null>(null);
  const [entityType, setEntityType] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load(withEntity?: string) {
    if (!threadId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const d = await getMatches(threadId.trim(), withEntity);
      setData(d);
      if (withEntity) setEntityType(withEntity);
    } catch (e) {
      setError(String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Match & cluster inspector</h3>
      <p className="card-hint">
        Per-entity blocking/scoring counts, and — pick an entity type — the
        actual clusters that were formed.
      </p>
      <div className="preset-row">
        <input
          value={threadId}
          onChange={(e) => setThreadId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          placeholder="wf_..."
        />
        <button className="btn btn-primary" onClick={() => load()} disabled={loading || !threadId.trim()}>
          {loading ? "Loading…" : "Load"}
        </button>
      </div>
      {error && <div className="banner banner-error" style={{ marginTop: 12 }}>{error}</div>}
      {data && !data.found && (
        <div className="banner banner-warn" style={{ marginTop: 12 }}>
          No checkpoint found for that thread id.
        </div>
      )}
      {data && data.found && (
        <div style={{ marginTop: 14 }}>
          <div className="section-label">Entity types</div>
          <div className="chip-row">
            {data.entity_types.map((et) => (
              <span
                key={et}
                className={`chip chip-clickable ${et === entityType ? "chip-active" : ""}`}
                onClick={() => load(et)}
              >
                {et}
              </span>
            ))}
            {data.entity_types.length === 0 && (
              <span className="card-hint" style={{ margin: 0 }}>
                No resolution_stats yet — this entity's resolve step hasn't finished.
              </span>
            )}
          </div>

          {data.resolution_stats[entityType] && (
            <>
              <div className="section-label" style={{ marginTop: 14 }}>
                {entityType} — resolution stats
              </div>
              <pre className="code-block">
                {JSON.stringify(data.resolution_stats[entityType], null, 2)}
              </pre>
            </>
          )}

          {entityType && (
            <>
              <div className="section-label" style={{ marginTop: 14 }}>
                {entityType} — clusters ({data.clusters.length})
              </div>
              {data.clusters.length === 0 ? (
                <p className="card-hint">
                  No materialised clusters (the run's scratch spool may have been
                  cleaned up since it finished — resolution stats above still survive
                  in the checkpoint either way).
                </p>
              ) : (
                <div className="table-list">
                  {data.clusters.slice(0, 100).map((c) => (
                    <div key={c.cluster_id} className="table-list-row">
                      <span className="mono">{c.cluster_id}</span>
                      <span className="count">
                        {(c.record_ids || []).length} record(s) · confidence{" "}
                        {typeof c.match_confidence === "number" ? c.match_confidence.toFixed(2) : "?"}
                        {c.required_human_review ? " · reviewed" : ""}
                      </span>
                    </div>
                  ))}
                  {data.clusters.length > 100 && (
                    <p className="card-hint">…and {data.clusters.length - 100} more.</p>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Phase 7.3: merge lineage lookup ──────────────────────────────────────────
// Unlike the match inspector above (checkpoint-backed, gone once the run's
// scratch spool is cleaned up), this queries the destination's own
// merge_engine_merge_audit table (Phase 7.2) directly -- durable, and
// searchable by either a canonical (merged) record id or a raw source
// record id, since both appear as record_a/record_b/cluster_id.

function LineageCard() {
  const [v, setV] = useState({
    host: "",
    port: "5432",
    user: "",
    password: "",
    database: "",
    schema_name: "public",
    entity_type: "",
    limit: "50",
  });
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<LineageResult | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!v.host || !search.trim()) return;
    setLoading(true);
    try {
      setResult(
        await getLineage({
          host: v.host,
          port: Number(v.port) || 5432,
          user: v.user,
          password: v.password,
          database: v.database,
          schema_name: v.schema_name,
          search: search.trim(),
          entity_type: v.entity_type || undefined,
          limit: Number(v.limit) || 50,
        }),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Merge lineage lookup</h3>
      <p className="card-hint">
        Search merge_engine_merge_audit (PostgreSQL destinations only) by a
        canonical record id or a raw source record id — every pair it ever
        appeared in, with the evidence that justified the merge. Survives the
        run's own spool being cleaned up.
      </p>
      <div className="form-grid">
        <div className="field">
          <label>Host</label>
          <input value={v.host} onChange={(e) => setV({ ...v, host: e.target.value })} placeholder="localhost" />
        </div>
        <div className="field">
          <label>Port</label>
          <input value={v.port} onChange={(e) => setV({ ...v, port: e.target.value })} />
        </div>
        <div className="field">
          <label>Database</label>
          <input value={v.database} onChange={(e) => setV({ ...v, database: e.target.value })} />
        </div>
        <div className="field">
          <label>Username</label>
          <input value={v.user} onChange={(e) => setV({ ...v, user: e.target.value })} />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            type="password"
            value={v.password}
            onChange={(e) => setV({ ...v, password: e.target.value })}
          />
        </div>
        <div className="field">
          <label>Schema</label>
          <input
            value={v.schema_name}
            onChange={(e) => setV({ ...v, schema_name: e.target.value })}
          />
        </div>
        <div className="field">
          <label>Entity type (optional)</label>
          <input
            value={v.entity_type}
            onChange={(e) => setV({ ...v, entity_type: e.target.value })}
            placeholder="company"
          />
        </div>
        <div className="field">
          <label>Record id or cluster id</label>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="o1:t:123 or a cluster id"
          />
        </div>
      </div>
      <div className="btn-row" style={{ marginTop: 14 }}>
        <button className="btn btn-primary" onClick={run} disabled={loading || !v.host || !search.trim()}>
          {loading ? "Searching…" : "Search"}
        </button>
      </div>
      {result && !result.connected && (
        <div className="banner banner-error" style={{ marginTop: 12 }}>{result.error}</div>
      )}
      {result && result.connected && result.rows.length === 0 && (
        <div className="banner banner-warn" style={{ marginTop: 12 }}>
          No merge-audit rows found for that id.
        </div>
      )}
      {result && result.connected && result.rows.length > 0 && (
        <div style={{ marginTop: 14 }}>
          {result.rows.map((row, i) => (
            <div key={i} className="cluster" style={{ marginBottom: 10 }}>
              <div className="cluster-head">
                {row.record_a} ↔ {row.record_b} — {row.method || "?"} (
                {typeof row.confidence === "number" ? row.confidence.toFixed(2) : "?"})
              </div>
              <div className="muted mono" style={{ fontSize: 12 }}>
                entity: {row.entity_type} · cluster: {row.cluster_id} · run: {row.run_id} · decided
                by: {row.decided_by || "?"}
                {row.human_override ? ` (human: ${row.human_override})` : ""}
              </div>
              {row.evidence && (
                <pre className="code-block" style={{ marginTop: 6, fontSize: 11 }}>
                  {JSON.stringify(row.evidence, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── B.3: schema & data explorer ─────────────────────────────────────────────

function DataExplorerCard() {
  const [v, setV] = useState({
    conn_type: "postgres",
    host: "",
    port: "5432",
    user: "",
    password: "",
    database: "",
    schema_name: "public",
    table: "",
    limit: "20",
  });
  const [result, setResult] = useState<SampleResult | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!v.host || !v.table) return;
    setLoading(true);
    try {
      setResult(
        await sampleRows({
          conn_type: v.conn_type,
          host: v.host,
          port: Number(v.port) || 5432,
          user: v.user,
          password: v.password,
          database: v.database,
          table: v.table,
          schema_name: v.schema_name,
          limit: Number(v.limit) || 20,
        }),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Schema & data explorer</h3>
      <p className="card-hint">
        A bounded (max 200 rows), read-only sample from any origin or destination
        you can already connect to. Never touches the merge pipeline.
      </p>
      <div className="form-grid">
        <div className="field">
          <label>Type</label>
          <select value={v.conn_type} onChange={(e) => setV({ ...v, conn_type: e.target.value })}>
            <option value="postgres">PostgreSQL</option>
            <option value="clickhouse">ClickHouse</option>
            <option value="mongodb">MongoDB</option>
          </select>
        </div>
        <div className="field">
          <label>Host</label>
          <input value={v.host} onChange={(e) => setV({ ...v, host: e.target.value })} placeholder="localhost" />
        </div>
        <div className="field">
          <label>Port</label>
          <input value={v.port} onChange={(e) => setV({ ...v, port: e.target.value })} />
        </div>
        <div className="field">
          <label>Database</label>
          <input value={v.database} onChange={(e) => setV({ ...v, database: e.target.value })} />
        </div>
        <div className="field">
          <label>Username</label>
          <input value={v.user} onChange={(e) => setV({ ...v, user: e.target.value })} />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            type="password"
            value={v.password}
            onChange={(e) => setV({ ...v, password: e.target.value })}
          />
        </div>
        {v.conn_type === "postgres" && (
          <div className="field">
            <label>Schema</label>
            <input
              value={v.schema_name}
              onChange={(e) => setV({ ...v, schema_name: e.target.value })}
            />
          </div>
        )}
        <div className="field">
          <label>Table / collection</label>
          <input value={v.table} onChange={(e) => setV({ ...v, table: e.target.value })} />
        </div>
        <div className="field">
          <label>Row limit (max 200)</label>
          <input value={v.limit} onChange={(e) => setV({ ...v, limit: e.target.value })} />
        </div>
      </div>
      <div className="btn-row" style={{ marginTop: 14 }}>
        <button className="btn btn-primary" onClick={run} disabled={loading || !v.host || !v.table}>
          {loading ? "Sampling…" : "Sample rows"}
        </button>
      </div>
      {result && !result.connected && (
        <div className="banner banner-error" style={{ marginTop: 12 }}>{result.error}</div>
      )}
      {result && result.connected && (
        <div style={{ marginTop: 14, overflowX: "auto" }}>
          <table className="sample-table">
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, i) => (
                <tr key={i}>
                  {result.columns.map((c) => (
                    <td key={c}>{String(row[c] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="card-hint">{result.rows.length} row(s)</p>
        </div>
      )}
    </div>
  );
}

// ── B.4: run comparison / reconciliation ────────────────────────────────────

function GoldenDiffCard() {
  const [subject, setSubject] = useState("boeing");
  const [data, setData] = useState<GoldenDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await getGoldenDiff(subject));
    } catch (e) {
      setError(String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Run comparison vs. the hand-merge golden</h3>
      <p className="card-hint">
        Wraps <span className="mono">compare_to_golden.py</span> — per-table
        parity between the current destination and the hand-built golden
        record for one of the three verified subjects.
      </p>
      <div className="preset-row">
        <select value={subject} onChange={(e) => setSubject(e.target.value)}>
          <option value="boeing">Boeing</option>
          <option value="airbus">Airbus</option>
          <option value="lockheed">Lockheed Martin</option>
        </select>
        <button className="btn btn-primary" onClick={load} disabled={loading}>
          {loading ? "Comparing…" : "Compare"}
        </button>
      </div>
      {error && <div className="banner banner-error" style={{ marginTop: 12 }}>{error}</div>}
      {data && (
        <div style={{ marginTop: 14 }}>
          <div className="summary-grid">
            <div className="stat">
              <div className="value">{data.totals.overall_parity_pct}%</div>
              <div className="label">Overall parity</div>
            </div>
            <div className="stat">
              <div className="value">{data.totals.matched_rows}</div>
              <div className="label">Matched rows</div>
            </div>
            <div className="stat">
              <div className="value">{data.totals.golden_rows}</div>
              <div className="label">Golden rows</div>
            </div>
            <div className="stat">
              <div className="value">{data.totals.app_rows}</div>
              <div className="label">App rows</div>
            </div>
          </div>
          <div className="section-label" style={{ marginTop: 14 }}>Per table</div>
          <div className="table-list">
            {data.tables.map((t: any) => (
              <div key={t.table} className="table-list-row">
                <span className="mono">{t.table}</span>
                <span className="count">
                  {t.matched}/{t.golden_rows} matched ({t.parity_pct}%) · app has {t.app_rows}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
