// Thin fetch wrappers around the FastAPI backend. All calls are relative
// ("/api/...") so the same code works served same-origin from FastAPI or
// via the Vite dev proxy.

export interface DiscoverResult {
  connected: boolean;
  error: string | null;
  databases: string[];
  schemas: string[];
  tables: string[];
  table_columns: Record<string, Array<{ name: string; type: string; nullable: boolean }>>;
  table_row_counts: Record<string, number>;
}

export interface OriginConfig {
  id: string;
  type: string;
  connection: {
    host: string;
    port: number;
    user: string;
    password: string;
    database: string;
  };
  priority: number;
}

export interface DestinationConfig {
  type: string;
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  schema?: string;
}

export interface RecentItem {
  type: string;
  connection: Record<string, any>;
  label: string;
  priority?: number;
}

export interface MappingRow {
  entity_type: string;
  source_field: string;
  action: string;
  dest_column: string;
  confidence: number;
  origin_id?: string;
  table?: string;
  is_coalesced?: boolean;
  reason?: string;
}

export interface ColumnDependency {
  child_origin: string;
  child_table: string;
  child_column: string;
  parent_origin: string;
  parent_table: string;
  parent_column: string;
  rationale?: string;
}

export interface InterruptData {
  node: string;
  title: string;
  data: Record<string, any>;
  // Content hash of `data`, set by WorkflowManager._build_interrupt_data.
  // Changes only when the gate's actual content does, unlike `data` itself
  // (a fresh object on every poll's JSON round-trip) — see MappingBody's
  // sync effect for why that distinction matters.
  payload_version?: string;
}

export interface WorkflowStatus {
  status: string;
  interrupt_data?: InterruptData | null;
  current_interrupt_node?: string | null;
  skipped_gates?: Record<string, string>;
  summary?: Record<string, any>;
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

async function put<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function testConnection(body: {
  conn_type: string;
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  schema_name?: string;
}): Promise<DiscoverResult> {
  return post("/api/connections/test", body);
}

export function getRecent(): Promise<{
  recent_origins: RecentItem[];
  recent_destinations: RecentItem[];
}> {
  return get("/api/connections/recent");
}

export function addRecent(
  kind: "origin" | "destination",
  item: Record<string, any>
): Promise<{ kind: string; items: RecentItem[] }> {
  return post("/api/connections/recent", { kind, item });
}

export interface ProfileSummary {
  name: string;
  description: string;
  user_request: string;
  entities: string[];
  pins: string[];
  updated_at: string;
}

// Part 1.6.A: run-by-run overrides submitted from the Pipeline screen's
// Advanced panel. Every field is optional -- omitting one leaves the
// file-loaded (or platform-overridden) default untouched for this run.
export interface RunConfig {
  model?: string;
  fallback_models?: string[];
  temperature?: number;
  entity_resolution_enabled?: boolean;
  field_mapping_enabled?: boolean;
  batch_size?: number;
  /** Gate names (a subset of REVIEW_GATES) to resume through automatically
   * instead of pausing for human review. Empty/omitted pauses at every gate,
   * same as before this existed. */
  auto_approve_gates?: string[];
}

// The gate names REVIEW_GATES actually contains (graph.py), kept in sync by
// hand -- "handle_error" is deliberately excluded, its resume semantics
// aren't a plain approve.
export const REVIEW_GATES = [
  "human_review_deps",
  "human_review_scope",
  "human_review_matches",
  "human_review_mappings",
  "human_final_approval",
] as const;

export const GATE_LABELS: Record<string, string> = {
  human_review_deps: "Dependency review",
  human_review_scope: "Scope confirmation",
  human_review_matches: "Entity match review",
  human_review_mappings: "Field mapping review",
  human_final_approval: "Final approval",
};

export function startWorkflow(body: {
  request: string;
  origins: OriginConfig[];
  destination: DestinationConfig;
  /** Replay a saved profile's pinned decisions instead of re-deriving them. */
  profile_name?: string;
  /** Write this run's decisions back to a profile of this name when it ends. */
  save_as_profile?: string;
  /** Ignore tracked watermarks and re-extract every table in full. */
  full_remerge?: boolean;
  /** Per-field survivorship override for this run (e.g. {_default: [...]}). */
  conflict_policy?: Record<string, any>;
  /** Per-entity matching override for this run, keyed by entity type with
   * an optional "_default" entry (target_entities isn't known yet). */
  matching_rules?: Record<string, any>;
  /** Advanced-panel run-by-run overrides. */
  run_config?: RunConfig;
}): Promise<{ thread_id: string; profile_name?: string }> {
  return post("/api/workflow/start", body);
}

// ── Config (Part 1.6.A Settings page) ──────────────────────────────────────

export interface EnvDetail {
  status: "set" | "overridden" | "not_set";
  source: "env" | "session_override" | "not_set";
  masked_value: string;
  is_set: boolean;
}

export interface EffectiveConfig {
  effective: Record<string, any>;
  platform_overrides: Record<string, any>;
  sources: Record<string, Record<string, "override" | "env" | "file">>;
  env_present: Record<string, boolean>;
  env_details?: Record<string, EnvDetail>;
}

export function getConfig(): Promise<EffectiveConfig> {
  return get("/api/config");
}

export function updatePlatformConfig(
  body: Record<string, any>
): Promise<{ platform_overrides: Record<string, any> }> {
  return put("/api/config/platform", body);
}

export function updateEnvOverrides(
  overrides: Record<string, string>
): Promise<{ env_details: Record<string, EnvDetail>; env_present: Record<string, boolean> }> {
  return post("/api/config/env", { overrides });
}

export function resetEnvOverrides(): Promise<{
  env_details: Record<string, EnvDetail>;
  env_present: Record<string, boolean>;
}> {
  return post("/api/config/env/reset", {});
}

// ── Investigation tools (Part 1.6.B) ────────────────────────────────────────
// All four are read-only. None can mutate a run or write to any database.

export interface MatchInspection {
  found: boolean;
  entity_types: string[];
  resolution_stats: Record<string, any>;
  requested_entity_type: string | null;
  clusters: Array<Record<string, any>>;
}

export function getMatches(threadId: string, entityType?: string): Promise<MatchInspection> {
  const q = entityType ? `?entity_type=${encodeURIComponent(entityType)}` : "";
  return get(`/api/workflow/${threadId}/matches${q}`);
}

export interface RunTimeline {
  found: boolean;
  timings: Record<string, number>;
  entities: Record<string, any>;
  generated_at: string | null;
  cost: { run: Record<string, any>; cache: Record<string, any> };
}

export function getTimeline(threadId: string): Promise<RunTimeline> {
  return get(`/api/workflow/${threadId}/timeline`);
}

export interface SampleResult {
  connected: boolean;
  error: string | null;
  columns: string[];
  rows: Array<Record<string, any>>;
}

export function sampleRows(body: {
  conn_type: string;
  host: string;
  port: number;
  user?: string;
  password?: string;
  database?: string;
  table: string;
  schema_name?: string;
  limit?: number;
}): Promise<SampleResult> {
  return post("/api/explore/sample", body);
}

export interface GoldenDiff {
  subject: string;
  totals: { golden_rows: number; app_rows: number; matched_rows: number; overall_parity_pct: number };
  families: {
    golden_family_count: number;
    app_family_count: number;
    golden_families: string[];
    app_families: string[];
    missing_families: string[];
  };
  tables: Array<{ table: string; golden_rows: number; app_rows: number; matched: number; parity_pct: number }>;
}

export function getGoldenDiff(subject: string): Promise<GoldenDiff> {
  return get(`/api/runs/diff/golden/${encodeURIComponent(subject)}`);
}

export function listProfiles(): Promise<{ profiles: ProfileSummary[] }> {
  return get("/api/profiles");
}

export async function deleteProfile(name: string): Promise<void> {
  const resp = await fetch(`/api/profiles/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!resp.ok) throw new Error(await resp.text());
}

export function getStatus(threadId: string): Promise<WorkflowStatus> {
  return get(`/api/workflow/${threadId}/status`);
}

export function sendFeedback(
  threadId: string,
  decision: "approve" | "reject",
  mappingEdits?: MappingRow[],
  columnDependencies?: ColumnDependency[]
): Promise<WorkflowStatus> {
  return post(`/api/workflow/${threadId}/feedback`, {
    decision,
    mapping_edits: mappingEdits ?? null,
    column_dependencies: columnDependencies ?? null,
  });
}

export function abortWorkflow(threadId: string): Promise<{ ok: boolean }> {
  return post(`/api/workflow/${threadId}/abort`, {});
}

export function getActive(): Promise<{ active: Array<{ thread_id: string; status: string }> }> {
  return get("/api/workflow/active");
}
