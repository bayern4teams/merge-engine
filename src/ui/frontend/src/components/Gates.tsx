import { useEffect, useState } from "react";
import { InterruptData, MappingRow } from "../api";

interface GateProps {
  interrupt: InterruptData;
  skippedGates: Record<string, string>;
  busy: boolean;
  onApprove: (mappingEdits?: MappingRow[]) => void;
  onReject: () => void;
}

export default function Gate(props: GateProps) {
  const { interrupt } = props;
  const node = interrupt.node;
  let title = interrupt.title;
  let icon = "🔍";
  if (node === "human_review_deps") icon = "🗂";
  else if (node === "human_review_scope") icon = "🎯";
  else if (node === "human_review_matches") icon = "🔗";
  else if (node === "human_review_mappings") icon = "🧭";
  else if (node === "human_final_approval") icon = "✅";

  return (
    <div className="card gate-card">
      <h3 className="card-title">
        <span>{icon}</span> {title}
      </h3>
      {node === "human_review_deps" && <DependencyBody data={interrupt.data} />}
      {node === "human_review_scope" && <ScopeBody data={interrupt.data} />}
      {node === "human_review_matches" && <MatchBody data={interrupt.data} />}
      {node === "human_review_mappings" && (
        <MappingBody data={interrupt.data} node={node} {...props} />
      )}
      {node === "human_final_approval" && (
        <SummaryBody data={interrupt.data} skippedGates={props.skippedGates} />
      )}

      {node !== "human_review_mappings" && <GateButtons {...props} />}
    </div>
  );
}

function GateButtons({
  busy,
  onApprove,
  onReject,
  approveLabel = "Approve",
  mappingEdits,
}: Pick<GateProps, "busy" | "onApprove" | "onReject"> & {
  approveLabel?: string;
  mappingEdits?: MappingRow[];
}) {
  return (
    <div className="btn-row" style={{ marginTop: 18 }}>
      <button
        className="btn btn-success"
        disabled={busy}
        onClick={() => onApprove(mappingEdits)}
      >
        ✓ {approveLabel}
      </button>
      <button className="btn btn-danger" disabled={busy} onClick={onReject}>
        ✕ Reject
      </button>
    </div>
  );
}

// ── Dependency gate ─────────────────────────────────────────────────────────

function DependencyBody({ data }: { data: Record<string, any> }) {
  const order: string[] = data.extraction_order || [];
  const tree: Record<string, string[]> = data.dependency_tree || {};
  return (
    <div>
      <div className="gate-explain">
        Confirm the order in which entities are extracted. Each entity's
        prerequisites must be extracted before it.
      </div>
      {data.warning && <div className="banner banner-warn">⚠️ {data.warning}</div>}
      <div className="section-label">Extraction order</div>
      <ol className="ordered-list">
        {order.map((entity) => {
          const deps = tree[entity] || [];
          return (
            <li key={entity}>
              <strong>{entity}</strong>{" "}
              <span className="muted">
                {deps.length ? `→ depends on: ${deps.join(", ")}` : "→ no dependencies (extract first)"}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ── Scope gate ──────────────────────────────────────────────────────────────

function ScopeBody({ data }: { data: Record<string, any> }) {
  const subject: string = data.subject || "";
  const candidates: any[] = data.candidates || [];
  const plan: any[] = data.plan || [];
  return (
    <div>
      <div className="gate-explain">
        This merge is scoped to <strong>{subject}</strong>. Only its records —
        and the people and products linked to it — will be extracted. Confirm
        the match and the plan below before anything is read.
      </div>

      <div className="section-label">
        Matching {data.subject_entity || "company"} records ({candidates.length})
      </div>
      {candidates.length === 0 ? (
        <div className="muted">
          No preview rows (the origins may be unreachable for a probe) — the
          plan below still applies.
        </div>
      ) : (
        <ul className="ordered-list" style={{ listStyle: "none", paddingLeft: 0 }}>
          {candidates.slice(0, 20).map((c, i) => (
            <li key={i}>
              <span className="muted">{c.origin_id}:</span>{" "}
              {Object.entries(c.fields || {})
                .slice(0, 4)
                .map(([k, v]) => `${k}=${v}`)
                .join(", ")}
            </li>
          ))}
        </ul>
      )}

      <div className="section-label">How each entity is scoped</div>
      <table className="data-table">
        <thead>
          <tr><th>Entity</th><th>Origin</th><th>Table</th><th>How</th></tr>
        </thead>
        <tbody>
          {plan.map((p, i) => (
            <tr key={i}>
              <td>{p.entity_type}</td>
              <td>{p.origin_id}</td>
              <td>{p.table}</td>
              <td>
                {p.mode === "match"
                  ? `name matches "${subject}"`
                  : `linked via ${(p.columns || []).join(", ")}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Match gate ──────────────────────────────────────────────────────────────

function MatchBody({ data }: { data: Record<string, any> }) {
  const clusters: any[] = data.low_confidence_clusters || [];
  const stats = data.auto_match_stats || {};
  return (
    <div>
      <div className="gate-explain">
        The system already auto-merged high-confidence matches. Only the
        ambiguous clusters below need your judgment.
      </div>
      <div className="banner banner-info">
        System decisions so far: <strong>{stats.auto_matched_pairs || 0}</strong> pair(s)
        auto-matched (≥85%), <strong>{stats.auto_excluded_pairs || 0}</strong> auto-excluded
        (&lt;70%).
      </div>
      {clusters.length === 0 ? (
        <p className="muted">No ambiguous matches — all clusters were clear.</p>
      ) : (
        clusters.map((c, i) => (
          <div key={i} className="cluster">
            <div className="cluster-head">
              {c.entity_type} — {(c.records || c.record_ids || []).length} candidate records,
              confidence {Number(c.confidence || 0).toFixed(2)}
            </div>
            <div className="record-grid">
              {(c.records || []).map((rec: any, j: number) => (
                <div key={j} className="record-card">
                  {Object.keys(rec.fields || {}).length === 0 ? (
                    <em className="muted">record {rec.record_id} (fields unavailable)</em>
                  ) : (
                    Object.entries(rec.fields).map(([k, val]) => (
                      <div key={k} className="kv">
                        <strong>{k}:</strong> {String(val)}
                      </div>
                    ))
                  )}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ── Mapping gate (editable) ─────────────────────────────────────────────────

const ACTIONS = ["map", "new_column", "skip"];

function MappingBody({
  data,
  node,
  busy,
  onApprove,
  onReject,
}: { data: Record<string, any>; node: string } & GateProps) {
  const [rows, setRows] = useState<MappingRow[]>([]);

  // Reset editable rows only when we (re)enter this gate, so a background
  // status refresh never clobbers in-flight edits (mirrors the old
  // mapping_gate_key first-render guard).
  useEffect(() => {
    setRows((data.mapping_rows || []) as MappingRow[]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node]);

  function update(i: number, patch: Partial<MappingRow>) {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }

  return (
    <div>
      <div className="gate-explain">
        Review every source-field mapping below. Auto-mapped fields are shown
        too — edit any row before approving. "new_column" adds a column to the
        destination; "skip" drops the field.
      </div>
      {data.error && <div className="banner banner-error">✗ {data.error}</div>}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Source field</th>
              <th>Action</th>
              <th>Dest column</th>
              <th>New column</th>
              <th>Type</th>
              <th>Conf.</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.entity_type}.${r.source_field}.${i}`}>
                <td className="mono">{r.entity_type}</td>
                <td className="mono">{r.source_field}</td>
                <td>
                  <select
                    className="mini-input"
                    value={r.action}
                    onChange={(e) => update(i, { action: e.target.value })}
                  >
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    className="mini-input"
                    value={r.dest_column}
                    disabled={r.action !== "map"}
                    onChange={(e) => update(i, { dest_column: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    className="mini-input"
                    value={r.new_column_name}
                    disabled={r.action !== "new_column"}
                    onChange={(e) => update(i, { new_column_name: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    className="mini-input"
                    value={r.new_column_type}
                    disabled={r.action !== "new_column"}
                    onChange={(e) => update(i, { new_column_type: e.target.value })}
                  />
                </td>
                <td className="muted">
                  {r.confidence != null ? `${Math.round(Number(r.confidence) * 100)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="btn-row" style={{ marginTop: 18 }}>
        <button className="btn btn-success" disabled={busy} onClick={() => onApprove(rows)}>
          ✓ Approve mappings
        </button>
        <button className="btn btn-danger" disabled={busy} onClick={onReject}>
          ✕ Reject
        </button>
      </div>
    </div>
  );
}

// ── Final approval gate ─────────────────────────────────────────────────────

function SummaryBody({
  data,
  skippedGates,
}: { data: Record<string, any>; skippedGates: Record<string, string> }) {
  const summary = data.summary || {};
  const recon = (data.reconciliation || {}).entities || {};
  const stats = data.auto_match_stats || {};
  const mappings: MappingRow[] = data.field_mappings_rows || [];

  const qualityReport =
    data.quality_report ||
    (data.reconciliation || {}).quality_report ||
    (data.summary || {}).quality_report;
  const hasQualityFailure = Boolean(
    data.has_quality_failure || qualityReport?.severity === "fail"
  );
  const lineageData =
    data.lineage ||
    (data.reconciliation || {}).lineage ||
    (data.summary || {}).lineage;
  const [showLineage, setShowLineage] = useState(false);

  // Phase 5.4 (O.3): which survivorship rule actually decided each conflict,
  // read-only — summed across every entity so the reviewer can see at a
  // glance whether, say, most conflicts fell through to an arbitrary
  // first_seen pick rather than being decided by priority/recency/etc.
  const winningRules: Record<string, number> = {};
  Object.values(recon).forEach((e: any) => {
    Object.entries(e.conflict_winning_rules || {}).forEach(([rule, count]) => {
      winningRules[rule] = (winningRules[rule] || 0) + (count as number);
    });
  });
  const winningRuleEntries = Object.entries(winningRules).sort((a, b) => b[1] - a[1]);
  const gateLabels: Record<string, string> = {
    human_review_scope: "Scope Confirmation",
    human_review_matches: "Entity Match Review",
    human_review_mappings: "Field Mapping Review",
  };

  return (
    <div>
      <div className="gate-explain">
        Final check before loading into the destination. You're approving these
        numbers — review them below.
      </div>

      {qualityReport && (
        <div style={{ marginTop: 16, marginBottom: 16 }}>
          <div className="section-label">Data Quality Summary</div>
          {hasQualityFailure && (
            <div className="banner banner-danger" style={{ marginBottom: 12 }}>
              🚨 <strong>DATA QUALITY FAILURE:</strong> One or more critical expectations failed (row count or uniqueness). Review before approving.
            </div>
          )}
          {qualityReport.severity === "warn" && !hasQualityFailure && (
            <div className="banner banner-warning" style={{ marginBottom: 12 }}>
              ⚠️ <strong>Data Quality Warnings:</strong> Non-critical data quality warnings detected.
            </div>
          )}
          {qualityReport.severity === "pass" && (
            <div className="banner banner-info" style={{ marginBottom: 12 }}>
              ✅ <strong>Data Quality Passed:</strong> All expectations evaluated cleanly.
            </div>
          )}
          {qualityReport.entities && (
            <div className="table-scroll" style={{ marginBottom: 12 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Entity</th>
                    <th>Status</th>
                    <th>Row Count</th>
                    <th>Checks Run</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(qualityReport.entities || {}).map(([ent, res]: [string, any]) => (
                    <tr key={ent}>
                      <td className="mono">{ent}</td>
                      <td>
                        <span className={`badge badge-${res.severity === "fail" ? "danger" : res.severity === "warn" ? "warning" : "success"}`}>
                          {res.severity?.toUpperCase()}
                        </span>
                      </td>
                      <td>{res.row_count ?? 0}</td>
                      <td>
                        {(res.checks || []).map((c: any, idx: number) => (
                          <div key={idx} style={{ fontSize: 12, color: c.success ? "#2e7d32" : "#c62828" }}>
                            {c.success ? "✓" : "✗"} {c.details}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="summary-grid">
        <Stat label="Entities" value={summary.total_entities ?? 0} />
        <Stat label="Conflicts" value={summary.total_conflicts ?? 0} />
        <Stat label="Schema changes" value={summary.schema_changes ?? 0} />
        <Stat label="Errors" value={summary.errors ?? 0} />
      </div>

      {Object.keys(skippedGates).length > 0 && (
        <>
          <div className="section-label">Automatic decisions (gates skipped)</div>
          {Object.entries(skippedGates).map(([gate, reason]) => (
            <div key={gate} className="banner banner-info">
              ⏭ <strong>{gateLabels[gate] || gate}</strong> was skipped: {reason}
            </div>
          ))}
        </>
      )}

      {(stats.auto_matched_pairs || stats.auto_excluded_pairs || stats.llm_scored_pairs) ? (
        <p className="muted" style={{ fontSize: 13 }}>
          Entity matching: <strong>{stats.auto_matched_pairs || 0}</strong> auto-matched
          (≥85%), <strong>{stats.auto_excluded_pairs || 0}</strong> auto-excluded (&lt;70%),{" "}
          <strong>{stats.llm_scored_pairs || 0}</strong> judged by LLM.
        </p>
      ) : null}

      {Object.keys(recon).length > 0 && (
        <>
          <div className="section-label">Reconciliation (pre-load)</div>
          <div className="table-scroll" style={{ marginBottom: 16 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Extracted</th>
                  <th>Clusters</th>
                  <th>Reviewed</th>
                  <th>Unified</th>
                  <th>Conflicts</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(recon).map(([name, e]: [string, any]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{e.extracted_total ?? 0}</td>
                    <td>{e.clusters_formed ?? 0}</td>
                    <td>{e.human_reviewed_clusters ?? 0}</td>
                    <td>{e.unified_records ?? 0}</td>
                    <td>{e.conflicts ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {winningRuleEntries.length > 0 && (
        <>
          <div className="section-label">Conflicts resolved by</div>
          <div className="table-scroll" style={{ marginBottom: 16 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Conflicts decided</th>
                </tr>
              </thead>
              <tbody>
                {winningRuleEntries.map(([rule, count]) => (
                  <tr key={rule}>
                    <td className="mono">{rule}</td>
                    <td>{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {mappings.length > 0 && (
        <>
          <div className="section-label">Field mappings ({mappings.length})</div>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Source field</th>
                  <th>Action</th>
                  <th>Destination</th>
                  <th>Conf.</th>
                </tr>
              </thead>
              <tbody>
                {mappings.map((r, i) => {
                  let dest = r.dest_column;
                  if (r.action === "new_column")
                    dest = `${r.new_column_name} (new ${r.new_column_type || "TEXT"})`;
                  else if (r.action === "skip") dest = "— skipped";
                  return (
                    <tr key={i}>
                      <td className="mono">{r.entity_type}</td>
                      <td className="mono">{r.source_field}</td>
                      <td>{r.action}</td>
                      <td>{dest}</td>
                      <td className="muted">
                        {r.confidence != null ? `${Math.round(Number(r.confidence) * 100)}%` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div style={{ marginTop: 16, marginBottom: 16 }}>
        <button
          className="btn btn-secondary"
          style={{ marginBottom: 8 }}
          onClick={() => setShowLineage(!showLineage)}
        >
          {showLineage ? "▼ Hide Data Lineage" : "▶ View Column-Level Data Lineage"}
        </button>
        {showLineage && (
          <div className="card" style={{ padding: 12, background: "#f8f9fa" }}>
            <div className="section-label">Column-Level Lineage & Provenance</div>
            {lineageData?.entities && Object.keys(lineageData.entities).length > 0 ? (
              Object.entries(lineageData.entities).map(([ent, info]: [string, any]) => (
                <div key={ent} style={{ marginBottom: 12 }}>
                  <strong className="mono">{ent}</strong>
                  <div className="table-scroll" style={{ marginTop: 6 }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Dest Column</th>
                          <th>Source Field</th>
                          <th>Origins</th>
                          <th>Conflicts / Winning Rule</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(info.columns || {}).map(([col, details]: [string, any]) => (
                          <tr key={col}>
                            <td className="mono">{col}</td>
                            <td className="mono">{details.source_field}</td>
                            <td>
                              {(details.origins || []).map((o: any, i: number) => (
                                <div key={i} className="mono" style={{ fontSize: 12 }}>
                                  {o.origin_id} ({o.origin_table})
                                </div>
                              ))}
                            </td>
                            <td>
                              {(details.conflicts || []).length > 0 ? (
                                (details.conflicts || []).map((cf: any, i: number) => (
                                  <div key={i} style={{ fontSize: 12 }}>
                                    Rule: <strong>{cf.winning_rule || "priority"}</strong> (selected {cf.selected_origin})
                                  </div>
                                ))
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))
            ) : (
              <p className="muted" style={{ fontSize: 13 }}>
                Lineage data is derived from field mappings and spool origin metadata.
              </p>
            )}
            {lineageData?.limitations && (
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                ℹ️ <em>Limitations:</em> {lineageData.limitations.source_ids_granularity} | {lineageData.limitations.durable_entity_map}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
