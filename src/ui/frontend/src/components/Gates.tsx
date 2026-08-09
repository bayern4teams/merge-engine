import { useEffect, useState } from "react";
import { ColumnDependency, InterruptData, MappingRow } from "../api";
import Tooltip from "./Tooltip";

interface GateProps {
  interrupt: InterruptData;
  skippedGates: Record<string, string>;
  busy: boolean;
  onApprove: (mappingEdits?: MappingRow[], columnDependencies?: ColumnDependency[]) => void;
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
      {node === "human_review_deps" && <DependencyBody data={interrupt.data} {...props} />}
      {node === "human_review_scope" && <ScopeBody data={interrupt.data} />}
      {node === "human_review_matches" && <MatchBody data={interrupt.data} />}
      {node === "human_review_mappings" && (
        <MappingBody
          data={interrupt.data}
          node={node}
          payloadVersion={interrupt.payload_version}
          {...props}
        />
      )}
      {node === "human_final_approval" && (
        <SummaryBody data={interrupt.data} skippedGates={props.skippedGates} />
      )}

      {/* human_review_mappings and human_review_deps render their own
          approve/reject row — both need to hand onApprove a local edit
          (mapping rows, custom dependency links respectively) that only
          their own component state holds. */}
      {node !== "human_review_mappings" && node !== "human_review_deps" && (
        <GateButtons {...props} />
      )}
    </div>
  );
}

function GateButtons({
  busy,
  onApprove,
  onReject,
  approveLabel = "Approve",
  mappingEdits,
  columnDependencies,
}: Pick<GateProps, "busy" | "onApprove" | "onReject"> & {
  approveLabel?: string;
  mappingEdits?: MappingRow[];
  columnDependencies?: ColumnDependency[];
}) {
  return (
    <div className="btn-row" style={{ marginTop: 18 }}>
      <button
        className="btn btn-success"
        disabled={busy}
        onClick={() => onApprove(mappingEdits, columnDependencies)}
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

// Bug #3: group the extraction order into tiers — tables with no dependencies
// are Tier 1, tables depending only on Tier 1 are Tier 2, and so on.
function computeTiers(
  order: string[],
  tree: Record<string, string[]>,
): Array<{ tier: number; items: Array<{ entity: string; deps: string[] }> }> {
  const memo: Record<string, number> = {};
  function tierOf(entity: string, seen: Set<string>): number {
    if (memo[entity] != null) return memo[entity];
    if (seen.has(entity)) return 1; // cycle guard — treat as a root
    seen.add(entity);
    const deps = tree[entity] || [];
    const t =
      deps.length === 0 ? 1 : 1 + Math.max(...deps.map((d) => tierOf(d, seen)));
    seen.delete(entity);
    memo[entity] = t;
    return t;
  }
  const byTier: Record<number, Array<{ entity: string; deps: string[] }>> = {};
  for (const entity of order) {
    const t = tierOf(entity, new Set());
    (byTier[t] = byTier[t] || []).push({ entity, deps: tree[entity] || [] });
  }
  return Object.keys(byTier)
    .map(Number)
    .sort((a, b) => a - b)
    .map((t) => ({ tier: t, items: byTier[t] }));
}

// Bug #2: one side (child or parent) of the "Add Custom Dependency Link"
// form — three cascading selects driven by the discovered origin schemas.
function SchemaCascade({
  label,
  originSchemas,
  origin,
  table,
  column,
  onOrigin,
  onTable,
  onColumn,
}: {
  label: string;
  originSchemas: Record<string, Record<string, string[]>>;
  origin: string;
  table: string;
  column: string;
  onOrigin: (v: string) => void;
  onTable: (v: string) => void;
  onColumn: (v: string) => void;
}) {
  const originIds = Object.keys(originSchemas);
  const tables = origin ? Object.keys(originSchemas[origin] || {}) : [];
  const columns = origin && table ? originSchemas[origin]?.[table] || [] : [];
  return (
    <div>
      <label style={{ fontSize: 11, fontWeight: 600, display: "block" }}>{label}</label>
      <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
        <select
          className="mini-input"
          value={origin}
          onChange={(e) => onOrigin(e.target.value)}
        >
          <option value="">Origin…</option>
          {originIds.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <select
          className="mini-input"
          value={table}
          disabled={!origin}
          onChange={(e) => onTable(e.target.value)}
        >
          <option value="">Table…</option>
          {tables.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          className="mini-input"
          value={column}
          disabled={!origin || !table}
          onChange={(e) => onColumn(e.target.value)}
        >
          <option value="">Column…</option>
          {columns.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function DependencyBody({
  data,
  busy,
  onApprove,
  onReject,
}: { data: Record<string, any> } & GateProps) {
  const order: string[] = data.extraction_order || [];
  const tree: Record<string, string[]> = data.dependency_tree || {};
  // Bug #2: {origin_id: {table_name: [column, ...]}} built by the backend
  // from the schema discovery step. Empty on runs resumed from a checkpoint
  // created before this field existed.
  const originSchemas: Record<string, Record<string, string[]>> =
    data.origin_schemas || {};
  const hasSchemas = Object.keys(originSchemas).length > 0;
  const [colDeps, setColDeps] = useState<any[]>(data.column_dependencies || []);

  const [newChildOrigin, setNewChildOrigin] = useState("");
  const [newChildTable, setNewChildTable] = useState("");
  const [newChildCol, setNewChildCol] = useState("");
  const [newParentOrigin, setNewParentOrigin] = useState("");
  const [newParentTable, setNewParentTable] = useState("");
  const [newParentCol, setNewParentCol] = useState("");
  const [newRationale, setNewRationale] = useState("");

  function addDependency() {
    if (!newChildOrigin || !newChildTable || !newChildCol) return;
    if (!newParentOrigin || !newParentTable || !newParentCol) return;
    const entry = {
      child_origin: newChildOrigin,
      child_table: newChildTable,
      child_column: newChildCol,
      parent_origin: newParentOrigin,
      parent_table: newParentTable,
      parent_column: newParentCol,
      rationale: newRationale || "Custom user-defined dependency link",
    };
    setColDeps((prev) => [...prev, entry]);
    setNewChildOrigin("");
    setNewChildTable("");
    setNewChildCol("");
    setNewParentOrigin("");
    setNewParentTable("");
    setNewParentCol("");
    setNewRationale("");
  }

  function removeDependency(index: number) {
    setColDeps((prev) => prev.filter((_, i) => i !== index));
  }

  // Bug #3: structured table grouped by extraction tier.
  const tiers = computeTiers(order, tree);
  let rowNumber = 0;

  return (
    <div>
      <div className="gate-explain">
        Confirm entity extraction order and cross-table column dependencies below. Prerequisites must be extracted before dependent tables.
      </div>
      {data.warning && <div className="banner banner-warn">⚠️ {data.warning}</div>}

      <div className="section-label">
        Extraction Order
        <Tooltip text="The topological sequence in which tables must be extracted so prerequisite entities exist first." />
      </div>
      <div className="table-scroll" style={{ marginBottom: 16 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Table Name</th>
              <th>Dependencies</th>
              <th>Status</th>
            </tr>
          </thead>
          {tiers.map(({ tier, items }) => (
            <tbody key={tier}>
              <tr>
                <td
                  colSpan={4}
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    textTransform: "uppercase",
                    background: "var(--card-bg, #f8f9fa)",
                    color: "var(--muted-color, #6c757d)",
                    borderBottom: "1px solid var(--border-color, #e0e0e0)",
                    padding: "4px 8px",
                  }}
                >
                  Tier {tier} —{" "}
                  {tier === 1
                    ? "no prerequisites (extract first)"
                    : `depends on Tier ${tier - 1}`}
                </td>
              </tr>
              {items.map(({ entity, deps }) => {
                rowNumber += 1;
                return (
                  <tr key={entity}>
                    <td className="mono muted">{rowNumber}</td>
                    <td className="mono">
                      <strong>{entity}</strong>
                    </td>
                    <td className="mono muted" style={{ fontSize: 12 }}>
                      {deps.length ? deps.join(", ") : "—"}
                    </td>
                    <td>
                      {deps.length === 0 ? (
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "#d4edda",
                            color: "#155724",
                            border: "1px solid #c3e6cb",
                            fontWeight: 600,
                          }}
                        >
                          root
                        </span>
                      ) : (
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "#e3f2fd",
                            color: "#1565c0",
                            border: "1px solid #bbdefb",
                            fontWeight: 600,
                          }}
                        >
                          has deps
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          ))}
        </table>
      </div>

      <div className="section-label">Column-Level Prerequisites ({colDeps.length})</div>
      {colDeps.length === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          No explicit column-level dependencies declared.
        </p>
      ) : (
        <div className="table-scroll" style={{ marginBottom: 16 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Child (Dependent)</th>
                <th>Parent (Prerequisite)</th>
                <th>Rationale / Link</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {colDeps.map((d, i) => (
                <tr key={i}>
                  <td className="mono">
                    <strong>{d.child_origin || "origin"}</strong>.{d.child_table}.{d.child_column || "*"}
                  </td>
                  <td className="mono">
                    <strong>{d.parent_origin || "origin"}</strong>.{d.parent_table}.{d.parent_column || "*"}
                  </td>
                  <td className="mono muted" style={{ fontSize: 12 }}>
                    {d.rationale || "Required relationship"}
                  </td>
                  <td>
                    <button
                      className="btn btn-danger"
                      style={{ padding: "2px 6px", fontSize: 11 }}
                      onClick={() => removeDependency(i)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="section-label">Add Custom Dependency Link</div>
      {!hasSchemas && (
        <div className="banner banner-info" style={{ marginBottom: 8 }}>
          Origin schemas are unavailable for this run, so the dropdowns cannot
          be populated. Approve the tree as-is, or reject and restart the
          workflow to re-discover schemas.
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          background: "var(--card-bg, #f8f9fa)",
          padding: 12,
          borderRadius: 6,
          border: "1px solid var(--border-color, #e0e0e0)",
        }}
      >
        <SchemaCascade
          label="Child (Dependent) Origin, Table & Column"
          originSchemas={originSchemas}
          origin={newChildOrigin}
          table={newChildTable}
          column={newChildCol}
          onOrigin={(v) => {
            setNewChildOrigin(v);
            setNewChildTable("");
            setNewChildCol("");
          }}
          onTable={(v) => {
            setNewChildTable(v);
            setNewChildCol("");
          }}
          onColumn={setNewChildCol}
        />

        <SchemaCascade
          label="Parent (Prerequisite) Origin, Table & Column"
          originSchemas={originSchemas}
          origin={newParentOrigin}
          table={newParentTable}
          column={newParentCol}
          onOrigin={(v) => {
            setNewParentOrigin(v);
            setNewParentTable("");
            setNewParentCol("");
          }}
          onTable={(v) => {
            setNewParentTable(v);
            setNewParentCol("");
          }}
          onColumn={setNewParentCol}
        />

        <div style={{ gridColumn: "1 / -1" }}>
          <input
            className="mini-input"
            placeholder="Rationale / Explanation (e.g. Requires Person record first)"
            value={newRationale}
            onChange={(e) => setNewRationale(e.target.value)}
            style={{ width: "100%" }}
          />
        </div>

        <div style={{ gridColumn: "1 / -1", textAlign: "right" }}>
          <button
            className="btn btn-secondary"
            style={{ fontSize: 12 }}
            onClick={addDependency}
            disabled={!hasSchemas}
          >
            + Add Dependency Link
          </button>
        </div>
      </div>

      {/* Rendered here rather than by the parent Gate, so onApprove gets
          this component's own colDeps — a custom link added above and never
          sent anywhere until this button carries it (Phase 2.4). */}
      <GateButtons busy={busy} onApprove={onApprove} onReject={onReject} columnDependencies={colDeps} />
    </div>
  );
}

// ── Scope gate ──────────────────────────────────────────────────────────────

// Bug #5: one collapsible card per matching record. Collapsed shows the first
// 3 fields inline; expanded shows the full field/value table.
function CandidateCard({ candidate, index }: { candidate: any; index: number }) {
  const [open, setOpen] = useState(false);
  const fields: Array<[string, any]> = Object.entries(candidate.fields || {});
  const preview = fields.slice(0, 3);
  const hasMore = fields.length > preview.length;

  return (
    <div
      style={{
        border: "1px solid var(--border-color, #e0e0e0)",
        borderRadius: 6,
        marginBottom: 8,
        overflow: "hidden",
        background: "var(--card-bg, #fff)",
      }}
    >
      <div
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 12px",
          cursor: "pointer",
          userSelect: "none",
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 12, color: "var(--muted-color, #6c757d)" }}>
          {open ? "▾" : "▸"} Record {index + 1}
        </span>
        <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>
          {candidate.origin_id}
        </span>
        <span className="mono muted" style={{ fontSize: 12 }}>
          {candidate.table || "Company"}
        </span>
        <span className="mono" style={{ fontSize: 12, color: "var(--muted-color, #6c757d)" }}>
          {preview.map(([k, v]) => `${k}=${String(v)}`).join(",  ")}
          {hasMore ? `,  +${fields.length - preview.length} more` : ""}
        </span>
      </div>
      {open && (
        <div style={{ padding: "4px 12px 10px" }}>
          <table className="data-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th style={{ width: "40%" }}>Field Name</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {fields.map(([k, v]) => (
                <tr key={k}>
                  <td className="mono">{k}</td>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {v == null ? <em className="muted">null</em> : String(v)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ScopeBody({ data }: { data: Record<string, any> }) {
  const subject: string = data.subject || "";
  const candidates: any[] = data.candidates || [];
  const plan: any[] = data.plan || [];
  return (
    <div>
      <div className="gate-explain">
        This merge is scoped to <strong>{subject}</strong>. Only its records — and the people, products, and associated entities linked to it — will be extracted. Confirm the match and the scope plan below before proceeding.
      </div>

      <div className="section-label">
        Matching {data.subject_entity || "company"} records ({candidates.length})
      </div>
      {candidates.length === 0 ? (
        <div className="muted">
          No preview rows (the origins may be unreachable for a probe) — the plan below still applies.
        </div>
      ) : (
        <div style={{ marginBottom: 16 }}>
          {candidates.slice(0, 20).map((c, i) => (
            <CandidateCard key={i} candidate={c} index={i} />
          ))}
        </div>
      )}

      <div className="section-label">How each entity is scoped</div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Origin</th>
              <th>Table</th>
              <th>Scoping Mode / Link Path</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {plan.map((p, i) => {
              let how = "";
              if (p.mode === "match") {
                how = `name matches "${subject}"`;
              } else if (p.mode === "link") {
                how = `linked via ${(p.columns || []).join(", ")}`;
              } else if (p.mode === "link_via") {
                how = `linked via ${p.via_from_column || ""} -> ${p.via_table || ""} -> ${p.via_to_column || ""}`;
              } else if (p.mode === "not_found") {
                how = p.reason || `No matching or linked table found in ${p.origin_id}`;
              }

              const isNotFound = p.mode === "not_found";

              return (
                <tr key={i} style={isNotFound ? { background: "rgba(255, 0, 0, 0.03)" } : {}}>
                  <td className="mono">{p.entity_type}</td>
                  <td className="mono muted">{p.origin_id}</td>
                  <td className="mono">{p.table}</td>
                  <td className="mono">{how}</td>
                  <td>
                    {isNotFound ? (
                      <span
                        style={{
                          fontSize: 11,
                          padding: "2px 6px",
                          borderRadius: 4,
                          background: "#fff3cd",
                          color: "#856404",
                          border: "1px solid #ffeeba",
                          fontWeight: 600,
                        }}
                      >
                        ⚠️ Not Found in {p.origin_id}
                      </span>
                    ) : (
                      <span
                        style={{
                          fontSize: 11,
                          padding: "2px 6px",
                          borderRadius: 4,
                          background: "#d4edda",
                          color: "#155724",
                          border: "1px solid #c3e6cb",
                          fontWeight: 600,
                        }}
                      >
                        ✓ Active
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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

// Phase 2.3: "new_column" was removed. The destination schema is a fixed
// contract (CLAUDE.md's "No new destination columns, ever" — the backend's
// apply_mapping_feedback has always hard-rejected this action), so offering
// it here was a dead end: a reviewer could pick it, fill in a name and
// type, click Approve, and be told no. An unmappable field is now always
// "skip", explained by the Reason column below.
const ACTIONS = ["map", "skip"];

function MappingBody({
  data,
  node,
  payloadVersion,
  busy,
  onApprove,
  onReject,
}: { data: Record<string, any>; node: string; payloadVersion?: string } & GateProps) {
  const [rows, setRows] = useState<MappingRow[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<string>("ALL");

  // Keyed on payloadVersion, not `data` itself: every 2s poll parses a
  // fresh JSON response, so `data` is a new object reference every tick
  // even when nothing changed. Depending on the whole object meant this
  // effect re-fired on every poll and wiped whatever the reviewer had just
  // edited. payloadVersion is a string that only changes when the gate's
  // actual content does (see WorkflowManager._build_interrupt_data).
  useEffect(() => {
    setRows((data.mapping_rows || []) as MappingRow[]);
  }, [node, payloadVersion]);

  function update(i: number, patch: Partial<MappingRow>) {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }

  const entities = Array.from(new Set(rows.map((r) => r.entity_type))).filter(Boolean);
  const filteredRows =
    selectedEntity === "ALL" ? rows : rows.filter((r) => r.entity_type === selectedEntity);

  return (
    <div>
      <div className="gate-explain">
        Review every origin field mapping below. Auto-mapped fields are shown upfront. You can filter by Destination Table using the dropdown below.
      </div>
      {data.error && <div className="banner banner-error">✗ {data.error}</div>}

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <label style={{ fontWeight: 600, fontSize: 13 }}>Filter by Destination Table / Entity:</label>
        <select
          className="mini-input"
          style={{ width: "auto", padding: "4px 8px" }}
          value={selectedEntity}
          onChange={(e) => setSelectedEntity(e.target.value)}
        >
          <option value="ALL">All Destination Tables ({rows.length} fields)</option>
          {entities.map((e) => (
            <option key={e} value={e}>
              {e} ({rows.filter((r) => r.entity_type === e).length} fields)
            </option>
          ))}
        </select>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Dest Table</th>
              <th>Origin</th>
              <th>Source Table</th>
              <th>Source Field</th>
              <th>Action</th>
              <th>Dest Column</th>
              <th>Reason</th>
              <th>Conf.</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((r, i) => {
              const globalIndex = rows.indexOf(r);
              return (
                <tr key={`${r.entity_type}.${r.origin_id || ""}.${r.table || ""}.${r.source_field}.${i}`}>
                  <td className="mono">{r.entity_type}</td>
                  <td className="mono muted">{r.origin_id || "—"}</td>
                  <td className="mono muted">{r.table || "—"}</td>
                  <td className="mono">
                    {r.source_field}
                    {r.is_coalesced && (
                      <span
                        style={{
                          marginLeft: 6,
                          fontSize: 10,
                          padding: "2px 5px",
                          borderRadius: 4,
                          background: "#fff3cd",
                          color: "#856404",
                          border: "1px solid #ffeeba",
                          fontWeight: 600,
                        }}
                        title="Multiple origin columns map to this same destination column"
                      >
                        ⚡ Coalesced
                      </span>
                    )}
                  </td>
                  <td>
                    <select
                      className="mini-input"
                      value={r.action}
                      onChange={(e) => update(globalIndex, { action: e.target.value })}
                    >
                      {ACTIONS.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      className="mini-input"
                      value={r.dest_column}
                      disabled={r.action !== "map"}
                      title={r.action !== "map" ? "Only used when Action is 'map'" : undefined}
                      onChange={(e) => update(globalIndex, { dest_column: e.target.value })}
                    />
                  </td>
                  <td className="muted">{r.reason || "—"}</td>
                  <td className="muted">
                    {r.confidence != null ? `${Math.round(Number(r.confidence) * 100)}%` : "—"}
                  </td>
                </tr>
              );
            })}
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
          <div className="section-label">
            Reconciliation (pre-load)
            <Tooltip text="Shows per-entity counts: how many records were extracted from origins, grouped into clusters, reviewed by a human, unified into canonical records, and how many field-level conflicts arose during unification." />
          </div>
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
          <div className="section-label">
            Conflicts resolved by
            <Tooltip text="Shows which survivorship rules (priority, recency, completeness, first_seen, etc.) decided each field-level conflict when merging duplicate records." />
          </div>
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
          <div className="section-label">
            Field mappings ({mappings.length})
            <Tooltip text="The final column-to-column mapping from source fields to destination columns. 'map' copies a value into an existing destination column; 'skip' drops the field (the destination schema is a fixed contract, so an unmappable field is always skipped, never given a new column)." />
          </div>
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
                  if (r.action === "skip") dest = "— skipped";
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
