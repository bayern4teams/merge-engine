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
  new_column_name: string;
  new_column_type: string;
  confidence: number;
}

export interface InterruptData {
  node: string;
  title: string;
  data: Record<string, any>;
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
}): Promise<{ thread_id: string; profile_name?: string }> {
  return post("/api/workflow/start", body);
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
  mappingEdits?: MappingRow[]
): Promise<WorkflowStatus> {
  return post(`/api/workflow/${threadId}/feedback`, {
    decision,
    mapping_edits: mappingEdits ?? null,
  });
}

export function abortWorkflow(threadId: string): Promise<{ ok: boolean }> {
  return post(`/api/workflow/${threadId}/abort`, {});
}

export function getActive(): Promise<{ active: Array<{ thread_id: string; status: string }> }> {
  return get("/api/workflow/active");
}
