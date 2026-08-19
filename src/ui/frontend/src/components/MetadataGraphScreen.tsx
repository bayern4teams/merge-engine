import { useMemo, useRef, useState } from "react";
import { getMetadataGraph, GraphEdge, GraphNode, GraphNodeType } from "../api";

// Part B P-8: interactive metadata graph -- source/database/schema/table/
// column containment (P-1), plus P-6 relationship and P-7 field_mapping
// edges. Strictly the metadata graph (tables/columns), never entity
// records -- the backend (metadata_graph_view.py) enforces this by only
// ever reading P-1's tables; there is no code path here that could render
// an entity node.

const NODE_TYPES: GraphNodeType[] = ["source", "database", "schema", "table", "column", "canonical_field"];

const NODE_COLOR: Record<GraphNodeType, string> = {
  source: "#4f46e5",
  database: "#0891b2",
  schema: "#059669",
  table: "#d97706",
  column: "#6b7280",
  canonical_field: "#be185d",
};

const EDGE_COLOR: Record<string, string> = {
  contains: "var(--border-strong)",
  relationship: "#2563eb",
  field_mapping: "#059669",
};

const LANE_WIDTH = 220;
const ROW_HEIGHT = 34;
const NODE_RADIUS = 7;

interface LayoutNode extends GraphNode {
  x: number;
  y: number;
}

/** Deterministic lane layout: one vertical lane per node type, in
 * containment order, nodes stacked top-to-bottom within their lane. Simple
 * and stable across re-renders (unlike a physics simulation, positions
 * never jump around when a filter changes), which matters more here than
 * visual elegance for a graph this size (~100 nodes today). */
function layoutNodes(nodes: GraphNode[]): Map<string, LayoutNode> {
  const byType = new Map<GraphNodeType, GraphNode[]>();
  for (const t of NODE_TYPES) byType.set(t, []);
  for (const n of nodes) {
    if (!byType.has(n.type)) byType.set(n.type, []);
    byType.get(n.type)!.push(n);
  }
  const positioned = new Map<string, LayoutNode>();
  let laneIndex = 0;
  for (const t of NODE_TYPES) {
    const laneNodes = (byType.get(t) || []).slice().sort((a, b) => a.label.localeCompare(b.label));
    laneNodes.forEach((n, i) => {
      positioned.set(n.id, { ...n, x: laneIndex * LANE_WIDTH + 100, y: i * ROW_HEIGHT + 40 });
    });
    laneIndex++;
  }
  return positioned;
}

export default function MetadataGraphScreen() {
  const [conn, setConn] = useState({
    host: "",
    port: "5432",
    user: "",
    password: "",
    database: "",
    schema_name: "public",
  });
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // Filters
  const [minConfidence, setMinConfidence] = useState(0);
  const [visibleTypes, setVisibleTypes] = useState<Set<GraphNodeType>>(new Set(NODE_TYPES));
  const [search, setSearch] = useState("");
  const [collapsedTables, setCollapsedTables] = useState<Set<string>>(new Set());
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [focusDepth, setFocusDepth] = useState(2);

  // Selection (click node -> metadata, click edge -> evidence)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);

  // Pan/zoom
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const dragRef = useRef<{ startX: number; startY: number; viewX: number; viewY: number } | null>(null);

  async function load(withFocus?: { focusNodeId: string; depth: number }) {
    if (!conn.host) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getMetadataGraph({
        host: conn.host,
        port: Number(conn.port) || 5432,
        user: conn.user,
        password: conn.password,
        database: conn.database,
        schema_name: conn.schema_name,
        focus_node_id: withFocus?.focusNodeId,
        max_depth: withFocus ? withFocus.depth : undefined,
      });
      if (!result.connected) {
        setError(result.error || "Could not connect");
        setNodes([]);
        setEdges([]);
      } else {
        setNodes(result.nodes);
        setEdges(result.edges);
        setLoaded(true);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  function focusOn(nodeId: string) {
    setFocusNodeId(nodeId);
    load({ focusNodeId: nodeId, depth: focusDepth });
  }

  function clearFocus() {
    setFocusNodeId(null);
    load();
  }

  function toggleCollapse(nodeId: string) {
    setCollapsedTables((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }

  // ── Visible subset (client-side filters applied on top of whatever the
  // backend already returned) ───────────────────────────────────────────

  const { visibleNodes, visibleEdges, layout } = useMemo(() => {
    const hiddenByCollapse = new Set<string>();
    for (const collapsedId of collapsedTables) {
      // Hide every node that is a `contains` descendant of a collapsed node.
      const stack = [collapsedId];
      const childrenOf = new Map<string, string[]>();
      for (const e of edges) {
        if (e.edge_type !== "contains") continue;
        if (!childrenOf.has(e.source)) childrenOf.set(e.source, []);
        childrenOf.get(e.source)!.push(e.target);
      }
      while (stack.length) {
        const cur = stack.pop()!;
        for (const child of childrenOf.get(cur) || []) {
          if (!hiddenByCollapse.has(child)) {
            hiddenByCollapse.add(child);
            stack.push(child);
          }
        }
      }
    }

    const vNodes = nodes.filter(
      (n) => visibleTypes.has(n.type) && !hiddenByCollapse.has(n.id),
    );
    const visibleIds = new Set(vNodes.map((n) => n.id));
    const vEdges = edges.filter(
      (e) =>
        visibleIds.has(e.source) &&
        visibleIds.has(e.target) &&
        (e.confidence === null || e.confidence >= minConfidence),
    );
    return { visibleNodes: vNodes, visibleEdges: vEdges, layout: layoutNodes(vNodes) };
  }, [nodes, edges, visibleTypes, minConfidence, collapsedTables]);

  const searchLower = search.trim().toLowerCase();
  const matchesSearch = (n: GraphNode) => !!searchLower && n.label.toLowerCase().includes(searchLower);

  // ── Pan / zoom handlers ─────────────────────────────────────────────

  function onBackgroundMouseDown(e: React.MouseEvent) {
    dragRef.current = { startX: e.clientX, startY: e.clientY, viewX: view.x, viewY: view.y };
  }
  function onBackgroundMouseMove(e: React.MouseEvent) {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setView((v) => ({ ...v, x: dragRef.current!.viewX + dx, y: dragRef.current!.viewY + dy }));
  }
  function onBackgroundMouseUp() {
    dragRef.current = null;
  }
  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    setView((v) => ({ ...v, scale: Math.min(4, Math.max(0.2, v.scale * factor)) }));
  }
  function zoomButton(factor: number) {
    setView((v) => ({ ...v, scale: Math.min(4, Math.max(0.2, v.scale * factor)) }));
  }
  function resetView() {
    setView({ x: 0, y: 0, scale: 1 });
  }

  return (
    <div>
      <div className="card">
        <h3 className="card-title">Metadata graph</h3>
        <p className="card-hint">
          The V3 discovery engine's own catalog: source/database/schema/table/column
          structure, plus relationship (P-6) and field-mapping (P-7) edges it has
          discovered. This is the <em>metadata</em> graph — tables and columns —
          never specific entity records.
        </p>
        <div className="form-grid">
          <div className="field">
            <label>Host</label>
            <input value={conn.host} onChange={(e) => setConn({ ...conn, host: e.target.value })} placeholder="localhost" />
          </div>
          <div className="field">
            <label>Port</label>
            <input value={conn.port} onChange={(e) => setConn({ ...conn, port: e.target.value })} />
          </div>
          <div className="field">
            <label>User</label>
            <input value={conn.user} onChange={(e) => setConn({ ...conn, user: e.target.value })} />
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={conn.password} onChange={(e) => setConn({ ...conn, password: e.target.value })} />
          </div>
          <div className="field">
            <label>Database</label>
            <input value={conn.database} onChange={(e) => setConn({ ...conn, database: e.target.value })} />
          </div>
          <div className="field">
            <label>Schema</label>
            <input value={conn.schema_name} onChange={(e) => setConn({ ...conn, schema_name: e.target.value })} />
          </div>
        </div>
        <div className="btn-row" style={{ marginTop: 12 }}>
          <button className="btn btn-primary" onClick={() => load()} disabled={loading || !conn.host}>
            {loading ? "Loading…" : "Load graph"}
          </button>
          {focusNodeId && (
            <button className="btn btn-ghost" onClick={clearFocus}>
              Clear focus
            </button>
          )}
        </div>
        {error && <div className="banner banner-error" style={{ marginTop: 12 }}>{error}</div>}
      </div>

      {loaded && (
        <div className="card">
          <div className="chip-row" style={{ marginBottom: 12, alignItems: "center", flexWrap: "wrap", gap: 12 }}>
            <input
              placeholder="Search node labels…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ minWidth: 200 }}
            />
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              Min confidence
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={minConfidence}
                onChange={(e) => setMinConfidence(Number(e.target.value))}
              />
              <span className="mono">{minConfidence.toFixed(2)}</span>
            </label>
            {NODE_TYPES.map((t) => (
              <label key={t} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="checkbox"
                  checked={visibleTypes.has(t)}
                  onChange={() =>
                    setVisibleTypes((prev) => {
                      const next = new Set(prev);
                      if (next.has(t)) next.delete(t);
                      else next.add(t);
                      return next;
                    })
                  }
                />
                <span className="chip" style={{ background: NODE_COLOR[t], color: "#fff" }}>
                  {t}
                </span>
              </label>
            ))}
          </div>

          {focusNodeId && (
            <div className="banner banner-info" style={{ marginBottom: 12 }}>
              Focused on <span className="mono">{focusNodeId}</span>, depth{" "}
              <input
                type="number"
                min={1}
                max={6}
                value={focusDepth}
                style={{ width: 50 }}
                onChange={(e) => {
                  const d = Number(e.target.value) || 1;
                  setFocusDepth(d);
                  load({ focusNodeId, depth: d });
                }}
              />
            </div>
          )}

          <div style={{ display: "flex", gap: 12 }}>
            <div
              style={{
                flex: 1,
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                overflow: "hidden",
                background: "var(--surface-2)",
                height: 520,
                position: "relative",
              }}
            >
              <div className="btn-row" style={{ position: "absolute", top: 8, right: 8, zIndex: 2 }}>
                <button className="btn btn-ghost" onClick={() => zoomButton(1.2)} title="Zoom in">+</button>
                <button className="btn btn-ghost" onClick={() => zoomButton(1 / 1.2)} title="Zoom out">−</button>
                <button className="btn btn-ghost" onClick={resetView} title="Reset view">⟲</button>
              </div>
              <svg
                width="100%"
                height="100%"
                onMouseDown={onBackgroundMouseDown}
                onMouseMove={onBackgroundMouseMove}
                onMouseUp={onBackgroundMouseUp}
                onMouseLeave={onBackgroundMouseUp}
                onWheel={onWheel}
                style={{ cursor: dragRef.current ? "grabbing" : "grab" }}
              >
                <g transform={`translate(${view.x},${view.y}) scale(${view.scale})`}>
                  {visibleEdges.map((e, i) => {
                    const a = layout.get(e.source);
                    const b = layout.get(e.target);
                    if (!a || !b) return null;
                    const isContains = e.edge_type === "contains";
                    return (
                      <line
                        key={i}
                        x1={a.x}
                        y1={a.y}
                        x2={b.x}
                        y2={b.y}
                        stroke={EDGE_COLOR[e.edge_type] || "#999"}
                        strokeWidth={isContains ? 1 : Math.max(1, (e.confidence ?? 0.5) * 3)}
                        strokeOpacity={selectedEdge === e ? 1 : isContains ? 0.35 : 0.7}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          setSelectedEdge(e);
                          setSelectedNode(null);
                        }}
                        style={{ cursor: "pointer" }}
                      />
                    );
                  })}
                  {visibleNodes.map((n) => {
                    const p = layout.get(n.id);
                    if (!p) return null;
                    const highlighted = matchesSearch(n);
                    const isSelected = selectedNode?.id === n.id;
                    const collapsible = n.type !== "column" && n.type !== "canonical_field";
                    const isCollapsed = collapsedTables.has(n.id);
                    return (
                      <g key={n.id} transform={`translate(${p.x},${p.y})`}>
                        <circle
                          r={NODE_RADIUS}
                          fill={NODE_COLOR[n.type]}
                          stroke={isSelected ? "var(--text)" : highlighted ? "#f59e0b" : "none"}
                          strokeWidth={isSelected || highlighted ? 3 : 0}
                          opacity={searchLower && !highlighted ? 0.25 : 1}
                          style={{ cursor: "pointer" }}
                          onClick={(ev) => {
                            ev.stopPropagation();
                            setSelectedNode(n);
                            setSelectedEdge(null);
                          }}
                          onDoubleClick={(ev) => {
                            ev.stopPropagation();
                            focusOn(n.id);
                          }}
                        />
                        <text
                          x={NODE_RADIUS + 6}
                          y={4}
                          fontSize={11}
                          fill="var(--text)"
                          opacity={searchLower && !highlighted ? 0.35 : 1}
                          style={{ userSelect: "none", pointerEvents: "none" }}
                        >
                          {n.label}
                          {isCollapsed ? " (collapsed)" : ""}
                        </text>
                        {collapsible && (
                          <text
                            x={-NODE_RADIUS - 4}
                            y={4}
                            fontSize={11}
                            textAnchor="end"
                            fill="var(--text-muted)"
                            style={{ cursor: "pointer", userSelect: "none" }}
                            onClick={(ev) => {
                              ev.stopPropagation();
                              toggleCollapse(n.id);
                            }}
                          >
                            {isCollapsed ? "▸" : "▾"}
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
              </svg>
            </div>

            <div style={{ width: 300, flexShrink: 0 }}>
              {selectedNode && (
                <div className="card" style={{ margin: 0 }}>
                  <h4 style={{ marginBottom: 8 }}>
                    <span className="chip" style={{ background: NODE_COLOR[selectedNode.type], color: "#fff" }}>
                      {selectedNode.type}
                    </span>{" "}
                    {selectedNode.label}
                  </h4>
                  <table className="data-table">
                    <tbody>
                      {Object.entries(selectedNode.data || {}).map(([k, v]) => (
                        <tr key={k}>
                          <td className="mono">{k}</td>
                          <td className="mono">{String(v)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <button className="btn btn-ghost" style={{ marginTop: 8 }} onClick={() => focusOn(selectedNode.id)}>
                    Focus here
                  </button>
                </div>
              )}
              {selectedEdge && (
                <div className="card" style={{ margin: 0 }}>
                  <h4 style={{ marginBottom: 8 }}>
                    <span className="chip" style={{ background: EDGE_COLOR[selectedEdge.edge_type], color: "#fff" }}>
                      {selectedEdge.edge_type}
                    </span>
                  </h4>
                  <table className="data-table">
                    <tbody>
                      <tr><td>from</td><td className="mono">{selectedEdge.source}</td></tr>
                      <tr><td>to</td><td className="mono">{selectedEdge.target}</td></tr>
                      {selectedEdge.relationship_type && (
                        <tr><td>type</td><td className="mono">{selectedEdge.relationship_type}</td></tr>
                      )}
                      <tr><td>confidence</td><td className="mono">{selectedEdge.confidence ?? "—"}</td></tr>
                      {selectedEdge.coverage !== null && (
                        <tr><td>coverage</td><td className="mono">{selectedEdge.coverage}</td></tr>
                      )}
                      {selectedEdge.origin && <tr><td>origin</td><td className="mono">{selectedEdge.origin}</td></tr>}
                      {selectedEdge.review_status && (
                        <tr><td>review_status</td><td className="mono">{selectedEdge.review_status}</td></tr>
                      )}
                    </tbody>
                  </table>
                  {selectedEdge.evidence && (
                    <pre style={{ fontSize: 11, overflow: "auto", maxHeight: 160, marginTop: 8 }}>
                      {JSON.stringify(selectedEdge.evidence, null, 2)}
                    </pre>
                  )}
                </div>
              )}
              {!selectedNode && !selectedEdge && (
                <p className="card-hint">
                  Click a node for its metadata, or an edge for its evidence. Double-click a
                  node to focus the graph on its neighborhood.
                </p>
              )}
            </div>
          </div>
          <p className="card-hint" style={{ marginTop: 8 }}>
            {visibleNodes.length} of {nodes.length} nodes, {visibleEdges.length} of {edges.length} edges shown.
            Drag to pan, scroll to zoom, ▾/▸ next to a source/database/schema/table label to
            collapse/expand its children.
          </p>
        </div>
      )}
    </div>
  );
}
