## 0. Project Structure

```text
merge-engine/
├── config/
│   ├── config.yaml                 # Main configuration file
│   └── schema/                     # Optional JSON schemas for config validation
├── src/
│   ├── __init__.py
│   ├── main.py                     # Entry point: start MCP server, Gradio, or both
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── state.py                # MergeState TypedDict definition
│   │   ├── graph.py                # LangGraph state graph builder and compilation
│   │   ├── nodes/                  # Individual LangGraph nodes
│   │   │   ├── __init__.py
│   │   │   ├── parse_request.py
│   │   │   ├── discover_schemas.py
│   │   │   ├── analyze_dependencies.py
│   │   │   ├── extract_data.py
│   │   │   ├── resolve_entities.py
│   │   │   ├── unify_entities.py
│   │   │   ├── map_fields.py
│   │   │   ├── load_data.py
│   │   │   ├── flag_origins.py
│   │   │   └── human_nodes.py      # Nodes that interrupt for human feedback
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── dependency_sort.py  # Topological sort
│   │       └── retry.py            # Retry logic wrapper
│   ├── mcp_server/
│   │   ├── __init__.py
│   │   ├── server.py               # MCP server definition and tool listing
│   │   ├── tools/                  # Tool implementations
│   │   │   ├── __init__.py
│   │   │   ├── discover_schema.py
│   │   │   ├── extract_data.py
│   │   │   ├── mark_imported.py
│   │   │   ├── resolve_entities.py
│   │   │   ├── unify_entity_group.py
│   │   │   ├── map_fields.py
│   │   │   └── load_data.py
│   │   └── connectors/             # Database connectors
│   │       ├── __init__.py
│   │       ├── postgres.py
│   │       ├── mongodb.py
│   │       ├── databricks.py
│   │       └── base.py             # Abstract base connector
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py               # LLM client setup (LangChain)
│   │   └── prompts/                # Prompt templates
│   │       ├── dependency_analysis.txt
│   │       ├── entity_resolution.txt
│   │       ├── field_mapping.txt
│   │       └── request_parsing.txt
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── gradio_app.py           # Gradio UI definition and integration
│   │   ├── components/             # Custom Gradio components/panels
│   │   │   ├── dependency_editor.py
│   │   │   ├── match_reviewer.py
│   │   │   ├── mapping_reviewer.py
│   │   │   └── summary_view.py
│   │   └── utils.py
│   └── utils/
│       ├── __init__.py
│       ├── config_loader.py        # Load and validate config.yaml
│       ├── logging_config.py
│       └── id_utils.py             # UUID generation and validation
├── tests/
│   ├── unit/
│   │   ├── test_tools.py
│   │   ├── test_nodes.py
│   │   ├── test_connectors.py
│   │   └── test_llm.py
│   ├── integration/
│   │   ├── test_mcp_server.py
│   │   ├── test_orchestrator.py
│   │   └── test_end_to_end.py
│   └── fixtures/                   # Test data and mock configs
├── Dockerfile                      # Optional containerisation
├── requirements.txt
├── pyproject.toml                  # Project metadata and dependencies
└── README.md
```

---

## 1. High-Level Architecture

**Goal:** Natural language → fully migrated, deduplicated, and unified entities in Databricks, with human oversight at critical points.

### Components

| Component | Technology | Role |
|-----------|------------|------|
| **User Interface** | Gradio (web) | Chat input, structured confirmation panels, progress monitoring |
| **Orchestrator** | LangGraph (state graph) | Manages the multi‑step workflow, pauses for human input, calls tools via MCP |
| **MCP Server** | Python (FastMCP) | Exposes all database and LLM tools as MCP resources/tools; handles credentials, batch processing |
| **LLM** | Any (via LangChain) | Used for schema understanding, dependency inference, entity resolution borderline cases, field mapping |
| **Origin Databases** | Various (SQL + NoSQL) | Read‑only (except for import flags), discovered dynamically |
| **Destination** | Databricks (Delta Lake) | Write‑only, pre‑defined star‑schema / data model, columns already exist, extendable |

### Data Flow Summary

```
User prompt → Orchestrator (LangGraph)
  → Schema Discovery (MCP tool)
  → Dependency Analyzer (LLM + MCP)
  → Human reviews dependency tree
  → Data Extraction (MCP tool, batched)
  → Entity Resolution & Unification (tool + LLM)
  → Human reviews ambiguous matches
  → Field Mapping (LLM + human review)
  → Human reviews final load plan
  → Data Loading (MCP tool, upsert)
  → Flag origins → Complete
```

---

## 2. MCP Server and Tools

### 2.1 Architecture: Internal Calls vs MCP Exposure

Tool implementations live as plain async functions in `src/mcp_server/tools/`. They are:
- **Called directly** by LangGraph orchestrator nodes (no MCP protocol overhead).
- **Registered** with the MCP server for external access (e.g., from other AI agents or automation scripts).

```python
# src/mcp_server/tools/extract_data.py  (plain function — shared implementation)
async def extract_data_impl(origin: OriginConfig, table_name: str,
                            columns: list[str], batch_size: int = 1000,
                            last_cursor: str | None = None,
                            filter: dict | None = None) -> dict:
    ...

# src/mcp_server/server.py (MCP registration — wraps same function)
@mcp.tool()
async def extract_data(args: dict) -> str:
    return json.dumps(await extract_data_impl(**args))

# src/orchestrator/nodes/extract_data.py (internal call — no MCP protocol)
from src.mcp_server.tools.extract_data import extract_data_impl
async def extract_data_node(state: MergeState):
    result = await extract_data_impl(...)  # direct call
```

### 2.2 Tools

| Tool | Description | Input | Output |
|------|-------------|-------|--------|
| `discover_schema` | Connect to an origin, return tables/collections, columns, types, one sample value, FKs | `origin_id` | `schema_json`: list of tables with fields |
| `extract_data` | Extract rows from a specific table/entity, filtered by `_imported = false` or custom filter. Batch‑oriented. Supports cursor‑based pagination (SQL: OFFSET/LIMIT; MongoDB: `_id`‑based skip). | `origin_id`, `table_name`, `columns[]`, `batch_size`, `last_cursor`, `filter` | `batch_json`: `{ rows, column_names, total_count, next_cursor, origin_ids, extracted_at }` |
| `mark_imported` | Flag extracted rows as imported (set `_imported = true` or insert into tracking table). For sources where schema changes aren't possible, writes to a separate `merge_engine_imported` tracking table. | `origin_id`, `table_name`, `ids[]` | success/fail |
| `resolve_entities` | Given records from multiple sources, cluster matching ones, assign UUIDs. Uses deterministic blocking + LLM scoring for borderline pairs. | `entity_type`, `records[]` (from extraction) | `clusters[]` (each cluster = list of record IDs + UUID) |
| `unify_entity_group` | Merge attributes within a cluster: complement nulls, flag conflicts, produce unified record. | `cluster_id`, `records[]` | `unified_record` (including `conflicts` JSON) |
| `map_fields` | Map unified fields to destination columns using LLM. Return mapping, confidence, and any `schema_changes` needed. | `unified_record_schema`, `destination_table_schema`, `entity_type` | `mapping_json` + `schema_changes[]` + `unmapped_fields[]` |
| `load_data` | Upsert unified records into destination Databricks table (Delta). Handles conflict column and provenance tracking. | `destination_table`, `records[]`, `mapping` | success/fail, row counts |

### 2.3 Resources (optional)
- `schema://{origin_id}` – cached schema
- `extraction://{origin_id}/{table}/status` – progress

All tools have retry logic internally (exponential backoff, max 3 attempts for transient errors, else return error to orchestrator).

**Credentials:** All origins/destination configured via `config.yaml` and `config.secrets.yaml` (or environment variables), never exposed to LLM. See Section 10.2 for credential management details.

---

## 3. LangGraph Orchestrator – State Machine

We’ll model the entire workflow as a LangGraph `StateGraph` with **human‑in‑the‑loop interrupts**.

### State Definition

```python
# src/orchestrator/state.py
from __future__ import annotations
from typing import TypedDict, NotRequired
from langgraph.graph import add_messages

class MergeState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────
    user_request: str
    target_entities: list[str]            # extracted from NL

    # ── Configuration (resolved at startup) ────────────────────────
    origins: list[OriginConfig]           # resolved origin configs
    destination: DestinationConfig        # resolved destination config

    # ── Phase outputs ─────────────────────────────────────────────
    schemas: dict[str, Any]               # {origin_id: schema}
    dependency_tree: dict[str, list[str]] # {entity: [prerequisites]}
    extraction_order: list[str]
    extracted_data: dict[str, list[dict]] # {entity_type: [records]}
    entity_clusters: dict[str, list[dict]]   # {entity_type: [clusters]}
    unified_entities: dict[str, list[dict]]  # {entity_type: [unified_records]}
    field_mappings: dict[str, dict]       # {entity_type: {src_field: (dest_col, conf)}}
    pending_schema_changes: list[dict]    # [{column, data_type, table, entity_type}]

    # ── Human interaction ─────────────────────────────────────────
    human_feedback: NotRequired[dict]     # collected user decisions
    load_approval: NotRequired[bool]      # final human approval

    # ── Error / retry tracking ────────────────────────────────────
    errors: NotRequired[list[str]]        # append-only error log
    retry_counts: NotRequired[dict[str, int]]  # {node_name: attempt_count}
    interrupt_data: NotRequired[dict]     # structured data for HITL interrupt
```

**Key changes from original:**
- `current_step` removed — LangGraph tracks current node execution via checkpoints.
- `retry_counts` added — tracks per-node retry attempts for escalation logic.
- `interrupt_data` added — holds structured data for the current HITL interrupt.
- `pending_schema_changes` added — accumulates proposed column additions during field mapping for batched application.
- `errors` uses append semantics (via `add_messages` reducer in LangGraph).

### Nodes (Agents/Tools)

1. **Parse Request** – uses LLM to extract target entities from natural language.
2. **Discover Schemas** – calls `discover_schema` for all origins.
3. **Analyze Dependencies** – uses LLM on schemas to build dependency tree.
4. **Human: Review Dependencies** – interrupt, show tree, let user modify.
5. **Extract Data** – calls `extract_data` for each entity in dependency order, in parallel where possible, with batching.
6. **Resolve Entities** – for each entity type, cluster across sources.
7. **Human: Review Matches** – interrupt on low‑confidence clusters, user confirms/rejects.
8. **Unify Entity Groups** – call `unify_entity_group` on each cluster.
9. **Map Fields** – for each entity, call `map_fields` to get mapping.
10. **Human: Review Mappings** – interrupt to resolve unmapped fields, possibly extend destination schema.
11. **Human: Final Approval** – show summary (counts, conflicts, unmapped), await OK.
12. **Load Data** – call `load_data` for each entity in order.
13. **Flag Origins** – call `mark_imported` for all extracted records.
14. **Done** – notify user.

### Edges and Conditions

- After dependency analysis, always go to Human Review Dependencies.
- After entity resolution, check if any low‑confidence clusters exist; if yes, interrupt; else go to Unify.
- After field mapping, if any unmapped fields exist, interrupt; else skip to Final Approval.
- Error node: retry if transient, else escalate.

### LangGraph Implementation Sketch

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

builder = StateGraph(MergeState)

# Add nodes
builder.add_node("parse_request", parse_request)
builder.add_node("discover_schemas", discover_schemas)
builder.add_node("analyze_deps", analyze_dependencies)
builder.add_node("human_review_deps", human_review_deps)  # interrupt
builder.add_node("extract_data", extract_data)
builder.add_node("resolve_entities", resolve_entities)
builder.add_node("human_review_matches", human_review_matches)  # interrupt
builder.add_node("unify_entities", unify_entity_groups)
builder.add_node("map_fields", map_fields)
builder.add_node("human_review_mappings", human_review_mappings)  # interrupt
builder.add_node("human_final_approval", human_final_approval)  # interrupt
builder.add_node("load_data", load_data)
builder.add_node("flag_origins", flag_origins)
builder.add_node("handle_error", handle_error)

# Edges
builder.set_entry_point("parse_request")
builder.add_edge("parse_request", "discover_schemas")
builder.add_edge("discover_schemas", "analyze_deps")
builder.add_edge("analyze_deps", "human_review_deps")
builder.add_edge("human_review_deps", "extract_data")
builder.add_edge("extract_data", "resolve_entities")
builder.add_conditional_edges("resolve_entities", has_low_confidence_clusters, {
    True: "human_review_matches",
    False: "unify_entities"
})
builder.add_edge("human_review_matches", "unify_entities")  # after human resolves
builder.add_edge("unify_entities", "map_fields")
builder.add_conditional_edges("map_fields", has_unmapped_fields, {
    True: "human_review_mappings",
    False: "human_final_approval"
})
builder.add_edge("human_review_mappings", "human_final_approval")
builder.add_edge("human_final_approval", "load_data")
builder.add_edge("load_data", "flag_origins")
builder.add_edge("flag_origins", END)

# Error handling (in each node, on exception go to handle_error, then retry from current node)
for node in [...]:
    builder.add_conditional_edges(node, error_handler, {"error": "handle_error", "continue": ...})
```

### Human‑in‑the‑Loop (Interrupts)

We use LangGraph’s `interrupt` mechanism. For each HITL node, we:

1. Save state, generate a message for the user (e.g., dependency tree JSON).
2. Pause execution with `interrupt(prompt_data)`.
3. The Gradio UI receives the interrupt data, renders a form, collects input.
4. UI sends the user response back to the graph via `graph.update_state(resume=True)`.
5. The node resumes with `human_feedback` populated.

---

## 4. Detailed Phase Design

### Phase 1: Request Parsing

- LLM prompt: “Extract list of entity types from: {user_request}.” Returns JSON `["person", "company", ...]`.
- Sets `target_entities` in state.

### Phase 2: Schema Discovery

- For each origin, call `discover_schema`.
  - For SQL: use `information_schema` for tables, columns, data types, foreign keys. Get one sample row per column.
  - For NoSQL (MongoDB, etc.): sample 100 documents, infer keys and types (string/number/array/object). Flatten nested keys with dots.
- Store in `state.schemas`.

### Phase 3: Dependency Analysis

- LLM prompt with schemas of all origins (or a unified view) + target entities: “For each target entity, determine all other entities that are prerequisites for a complete import (based on foreign keys and column semantics). Output a dependency tree as JSON.”
- Example output:
  ```json
  {
    "product": ["company", "person"],
    "company": ["country"],
    "person": []
  }
  ```
- Also builds the `extraction_order` using topological sort.

### Phase 4: Human Review Dependencies

- Display tree as interactive graph (Gradio component). User can add/remove dependencies or approve.
- The modified tree is stored in `state.dependency_tree`.

### Phase 5: Data Extraction

- For each entity in `extraction_order` (starting from leaf nodes):
  - Determine which tables/collections from which origins contain this entity. (LLM matches table names/columns to entity, using schema).
  - Call `extract_data` tool with `table_name`, column list, and filter `_imported = false` (if flag column exists; else we create it or use a tracking table).
  - The tool returns batches; loop until all rows fetched.
  - Store raw records with origin ID and origin‑table‑row IDs.
- This step is inherently parallelizable across independent leaves; we can use LangGraph’s `Send` API to fan out.

### Phase 6: Entity Resolution

- For each entity type (e.g., "person"), gather all records from all origins.
- Use a **three‑step matching** pipeline:

**Step 1 — Deterministic Blocking:**
- For known entity types (defined in config under `blocking_keys`), use configured blocking keys.
- For unknown entity types, use LLM-assisted discovery: prompt with sample records to identify 2–3 good blocking keys.
- Blocking algorithms:
  - **Exact match**: group records by identical values on blocking keys (e.g., same email).
  - **Normalized strings**: lowercase, strip whitespace, handle "St." vs "Street".
  - **Phonetic matching**: Soundex/Metaphone for name-based blocking.
  - **Date tolerances**: allow ±1 day for birth_date to handle typos.
- For each block with >1 record, generate all candidate pairs. For large blocks (>1000 records), use sampling or keyhole blocking.

**Step 2 — LLM Scoring (borderline only):**
- Pairs with deterministic confidence ≥ 0.95 → auto-match.
- Pairs with deterministic confidence ≤ 0.70 → auto-no-match.
- Pairs with confidence 0.70–0.95 → send to LLM.
- LLM prompt includes all non-key fields; returns yes/no + confidence.

**Step 3 — Cluster Assembly:**
- Use Union-Find / connected components on confirmed match pairs.
- Each connected component = one entity cluster.
- Generate a UUID per cluster, store in `state.entity_clusters`.
- Clusters with any LLM-scored pairs where confidence < 0.85 are flagged as "low‑confidence" to trigger human review.

### Phase 7: Human Review Matches (if any low‑confidence)

- Display side‑by‑side records within a questionable cluster. User selects “Match” / “Not a match” / “Split manually”.
- Update clusters accordingly.

### Phase 8: Unification

- For each cluster, call `unify_entity_group` tool.
  - Iterate over all fields present in any source record.
  - If all non‑null values agree → take value.
  - If some are null and others non‑null → fill with non‑null.
  - If conflicting non‑null values → create a `ConflictEntry` list with `{source, value}`.
  - Construct final unified record: main fields get the “best” value (configurable: first source, most frequent, or designated authoritative origin). All conflict details stored in a `conflicts` JSON column for that entity.
- The “best” value selection is rule‑based (config). Default: pick from the first origin in priority list (configurable).

### Phase 9: Field Mapping

- For each entity type, call `map_fields` tool:
  - Input: unified record schema (field names, sample values), destination table schema (from Databricks `DESCRIBE TABLE`).
  - LLM returns: for each source field, mapped destination column and confidence (0‑1).
  - If a source field maps to no destination column (or confidence < threshold), it's "unmapped".
  - The tool also returns `schema_changes`: list of `{column, data_type, table}` for any new columns needed.
- Unmapped fields are accumulated in `state.field_mappings` with `confidence: 0` for human review.
- Schema changes are accumulated in `state.pending_schema_changes` (not applied yet — see Phase 10).
- Human reviews unmapped fields; options:
  - Discard field
  - Map to an existing column (user selects from dropdown)
  - Confirm new column addition (adds to `pending_schema_changes`)
- Field mapping stored in `state.field_mappings`.

### Phase 10: Human Final Approval

- Summarize: total rows per entity, number of conflicts, unmapped fields, schema changes.
- **Batched schema extension:** If `pending_schema_changes` has entries, execute all `ALTER TABLE` statements in a single transaction **before** any data loading:
  ```sql
  BEGIN TRANSACTION;
  ALTER TABLE unified.dim_person ADD COLUMN department STRING;
  ALTER TABLE unified.dim_company ADD COLUMN tax_id STRING;
  COMMIT;
  ```
- This ensures all entities see the updated destination schema during loading.
- User clicks "Approve" to proceed (schema changes + data load), or "Abort".

### Phase 11: Data Loading

- For each entity (in dependency order to respect FK constraints, though Databricks may not enforce, but for correctness):
  - Apply field mapping to transform unified records into destination columns.
  - For conflict data, transform the conflict list into a JSON string.
  - Use Databricks `MERGE` (UPSERT) using the universal UUID as key. If the UUID already exists (previous run), update non‑key fields; else insert.
  - Insert in batches.
- On success, record provenance: add a `source_ids` JSON column containing `[{origin_id, original_table, original_id}]`.
- Also update `updated_at` or load timestamp.

### Phase 12: Flag Origins

- For every origin table that contributed rows, call `mark_imported` with the list of original IDs.
  - Implementation: if origin allows it, create a boolean column `_imported` and set to True. For NoSQL, set a field. For sources where schema changes aren’t possible, create a separate tracking table `merge_engine_imported` (origin_id, table, id, imported_at). This tracking can be in a small SQLite DB attached to the engine or in Databricks.

### Phase 13: Completion

- Notify user via Gradio. Display final counts and any warnings.

---

## 5. MCP Server Design (Python)

### 5.1 Architecture: Dual-Path Tool Calls

Tool implementations are plain async functions in `src/mcp_server/tools/`. They serve dual purposes:
- **Internal path:** Called directly by LangGraph orchestrator nodes (no protocol overhead).
- **External path:** Wrapped by MCP server for external clients (other AI agents, automation scripts).

### 5.2 Server Setup

We use the `mcp` (FastMCP) package. Credentials are loaded from `config.yaml` + `config.secrets.yaml` on startup, never passed by the LLM.

```python
import mcp
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationCapabilities
from src.utils.config_loader import load_config

server = Server("merge-engine")

@server.list_tools()
async def list_tools() -> list[mcp.types.Tool]:
    return [
        mcp.types.Tool(name="discover_schema", inputSchema={...}),
        mcp.types.Tool(name="extract_data", inputSchema={...}),
        # ... other tools
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[mcp.types.TextContent]:
    config = load_config()  # resolved config with secrets
    if name == "discover_schema":
        result = await discover_schema_impl(config, arguments)
        return [mcp.types.TextContent(type="text", text=json.dumps(result))]
    # ... other tools
```

### 5.3 Database Connectors

Tool implementations use `sqlalchemy` for SQL sources, `pymongo` for MongoDB, and `databricks-sql-connector` for the Databricks destination. Connections are pooled per-call (or reused via connection pool).

```python
# src/mcp_server/connectors/base.py
from abc import ABC, abstractmethod

class BaseConnector(ABC):
    @abstractmethod
    async def discover_schema(self) -> dict: ...
    @abstractmethod
    async def extract(self, table_name: str, columns: list[str],
                      filter: dict | None, batch_size: int,
                      cursor: str | None) -> dict: ...
    @abstractmethod
    async def mark_imported(self, table_name: str, ids: list[str]) -> bool: ...

# src/mcp_server/connectors/postgres.py
# src/mcp_server/connectors/mongodb.py
# src/mcp_server/connectors/databricks.py
```

---

## 6. Python Code Schematics (Key Classes & Functions)

> **Note:** These are simplified schematics. The canonical definitions are in Sections 3 (State), 10 (Config), and 8 (Error Handling).

```python
# config.py  (canonical definition: Section 10.2)
from pydantic import BaseModel, Field
from typing import Optional

class OriginConnection(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    uri: Optional[str] = None

class OriginConfig(BaseModel):
    id: str
    type: str  # "postgres" | "mongodb" | "databricks"
    connection: OriginConnection
    priority: int = Field(default=0, ge=0)

class DestinationConfig(BaseModel):
    type: str = "databricks"
    host: str
    http_path: str
    token: str
    catalog: str
    schema: str
    tables: dict[str, str]  # entity_type -> table_name

class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str
    api_key: str
    base_url: Optional[str] = None

class Config(BaseModel):
    origins: list[OriginConfig] = Field(min_length=1)
    destination: DestinationConfig
    llm: LLMConfig
    blocking_keys: dict[str, list[str]] = {}
```

```python
# tools/schema_discovery.py
async def discover_schema(origin: OriginConfig) -> dict:
    if origin.type == "postgres":
        return await _discover_postgres(origin)
    elif origin.type == "mongodb":
        return await _discover_mongo(origin)
    ...

# tools/extraction.py
async def extract_data(origin: OriginConfig, table_name: str, columns: list[str],
                       batch_size: int = 1000, last_cursor: str = None) -> dict:
    # returns { "rows": [...], "total_count": int, "next_cursor": str, "ids": [...] }
    # Uses SQL OFFSET/LIMIT or NoSQL cursor-based pagination
```

```python
# orchestration/state.py  (canonical definition: Section 3)
from typing import TypedDict, NotRequired

class MergeState(TypedDict):
    user_request: str
    target_entities: list[str]
    origins: list[OriginConfig]
    destination: DestinationConfig
    schemas: dict[str, Any]
    dependency_tree: dict[str, list[str]]
    extraction_order: list[str]
    extracted_data: dict[str, list[dict]]
    entity_clusters: dict[str, list[dict]]
    unified_entities: dict[str, list[dict]]
    field_mappings: dict[str, dict]
    pending_schema_changes: list[dict]
    human_feedback: NotRequired[dict]
    load_approval: NotRequired[bool]
    errors: NotRequired[list[str]]
    retry_counts: NotRequired[dict[str, int]]
    interrupt_data: NotRequired[dict]
```

```python
# orchestration/graph.py
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def build_graph():
    builder = StateGraph(MergeState)

    # Add all nodes
    for node_name, node_fn in [
        ("parse_request", parse_request),
        ("discover_schemas", discover_schemas),
        ("analyze_deps", analyze_dependencies),
        ("human_review_deps", human_review_deps),
        ("extract_data", extract_data),
        ("resolve_entities", resolve_entities),
        ("human_review_matches", human_review_matches),
        ("unify_entities", unify_entity_groups),
        ("map_fields", map_fields),
        ("human_review_mappings", human_review_mappings),
        ("human_final_approval", human_final_approval),
        ("load_data", load_data),
        ("flag_origins", flag_origins),
        ("handle_error", handle_error),
    ]:
        builder.add_node(node_name, node_fn)

    # Error handling: wrap each node with try/except that routes errors to handle_error
    for node_name in [n for n in ["parse_request", "discover_schemas", "analyze_deps",
                                   "extract_data", "resolve_entities", "unify_entities",
                                   "map_fields", "load_data", "flag_origins"]]:
        builder.add_conditional_edges(
            node_name,
            error_router,
            {"error": "handle_error", "next": node_name}  # retry or escalate
        )

    # Main flow edges
    builder.set_entry_point("parse_request")
    builder.add_edge("parse_request", "discover_schemas")
    builder.add_edge("discover_schemas", "analyze_deps")
    builder.add_edge("analyze_deps", "human_review_deps")
    builder.add_edge("human_review_deps", "extract_data")
    builder.add_edge("extract_data", "resolve_entities")
    builder.add_conditional_edges("resolve_entities", has_low_confidence_clusters, {
        True: "human_review_matches",
        False: "unify_entities"
    })
    builder.add_edge("human_review_matches", "unify_entities")
    builder.add_edge("unify_entities", "map_fields")
    builder.add_conditional_edges("map_fields", has_unmapped_fields, {
        True: "human_review_mappings",
        False: "human_final_approval"
    })
    builder.add_edge("human_review_mappings", "human_final_approval")
    builder.add_edge("human_final_approval", "load_data")
    builder.add_edge("load_data", "flag_origins")
    builder.add_edge("flag_origins", END)

    # Compile with checkpointing (enables resume after interrupts)
    memory = MemorySaver()
    return builder.compile(
        checkpointer=memory,
        interrupt_before=["human_review_deps", "human_review_matches",
                          "human_review_mappings", "human_final_approval"]
    )
```

```python
# gradio_ui.py
import gradio as gr
from src.ui.workflow_manager import WorkflowManager

workflow_manager = WorkflowManager()

def handle_user_request(user_request: str):
    thread_id = workflow_manager.start_workflow(user_request)
    return f"Workflow started: `{thread_id}`", gr.update(visible=True)

def poll_status(thread_id: str):
    status = workflow_manager.get_workflow_status(thread_id)
    if status["interrupt_data"]:
        return render_interrupt_panel(status)
    return gr.update(visible=False)

def submit_feedback(thread_id: str, feedback: dict):
    workflow_manager.resume_workflow(thread_id, feedback)
```

---

## 7. Mermaid Diagrams

### 7.1 System Architecture Diagram

```mermaid
graph TD
    User -->|Natural Language| GradioUI[Gradio UI]
    GradioUI <-->|State Updates, Interrupts| LangGraph[LangGraph Orchestrator]
    LangGraph -->|Tool Calls| MCP[MCP Server]
    MCP -->|DB Access| Origins[(Origin DBs: SQL/NoSQL)]
    MCP -->|DB Access| Destination[(Databricks)]
    LangGraph -->|LLM Calls| LLM[LLM Service]
    MCP -->|File I/O| Config[config.yaml]
    subgraph LangGraph Internals
        State[(State)]
        Nodes[Agents/Nodes]
    end
```

### 7.2 Workflow Sequence Diagram (Simplified)

```mermaid
sequenceDiagram
    participant U as User
    participant G as Gradio
    participant LG as LangGraph
    participant M as MCP Server
    participant O as Origins
    participant D as Databricks

    U->>G: "Import all products and related entities"
    G->>LG: Start workflow
    LG->>LG: Parse request → ["product"]
    LG->>M: discover_schema(all origins)
    M->>O: Query metadata
    O-->>M: Schemas
    M-->>LG: Schemas + samples
    LG->>LG: Analyze dependencies → tree
    LG-->>G: Interrupt: review deps
    G-->>U: Display tree
    U->>G: Approve
    G->>LG: Resume
    LG->>M: extract_data for each entity in order
    M->>O: SELECT ... WHERE _imported=false
    O-->>M: Batches
    M-->>LG: Records
    LG->>M: resolve_entities(person)
    M-->>LG: Clusters (some low-confidence)
    LG-->>G: Interrupt: review matches
    G-->>U: Show pairs
    U->>G: Confirm/Reject
    G->>LG: Resume
    LG->>M: unify_entity_group for all clusters
    M-->>LG: Unified records
    LG->>M: map_fields(product)
    M-->>LG: Mapping
    LG-->>G: Interrupt: review mappings/unmapped
    G-->>U: Show mapping
    U->>G: Approve/Add column
    G->>LG: Resume
    LG-->>G: Final summary, ask approval
    U->>G: Approve
    G->>LG: Resume
    LG->>M: load_data(product)
    M->>D: MERGE INTO ...
    D-->>M: OK
    M-->>LG: Success
    LG->>M: mark_imported(origin, ids)
    M->>O: SET _imported=true
    LG-->>G: Done
    G-->>U: Success
```

### 7.3 LangGraph State Machine Diagram

```mermaid
stateDiagram-v2
    [*] --> ParseRequest
    ParseRequest --> DiscoverSchemas
    DiscoverSchemas --> AnalyzeDependencies
    AnalyzeDependencies --> HumanReviewDeps
    HumanReviewDeps --> ExtractData
    ExtractData --> ResolveEntities
    ResolveEntities --> HasLowConfidence
    HasLowConfidence --> HumanReviewMatches: Yes
    HasLowConfidence --> UnifyEntities: No
    HumanReviewMatches --> UnifyEntities
    UnifyEntities --> MapFields
    MapFields --> HasUnmapped
    HasUnmapped --> HumanReviewMappings: Yes
    HasUnmapped --> HumanFinalApproval: No
    HumanReviewMappings --> HumanFinalApproval
    HumanFinalApproval --> LoadData
    LoadData --> FlagOrigins
    FlagOrigins --> [*]
```

---

## 8. Error Handling & Retries

### 8.1 Per-Node Retry Tracking

Each node wraps its logic in a retry decorator. On failure, the node increments its own counter in `state.retry_counts`. The `handle_error` node inspects this counter:

```python
# src/orchestrator/utils/retry.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

def node_retry(max_attempts: int = 3):
    def decorator(func):
        return retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception_type(TransientError),
            reraise=True,
        )(func)
    return decorator
```

### 8.2 Error Classification

| Error Type | Examples | Action |
|------------|----------|--------|
| `TransientError` | Connection timeout, network glitch, rate limit | Retry with exponential backoff (max 3 attempts) |
| `PermanentError` | Schema mismatch, auth failure, invalid config | Escalate to `handle_error` → interrupt to user |

### 8.3 Error Flow

1. Node executes → raises `TransientError` → retry (up to 3x).
2. After 3 failures → raise `ToolException` → LangGraph node catches → append to `state.errors`, increment `state.retry_counts[node_name]`.
3. Transition to `handle_error` node.
4. `handle_error` checks: if `retry_counts[node_name] >= max_attempts` and error is `PermanentError` → interrupt to user with details. If transient but exhausted retries → attempt reroute to failed node (for temporary issues that may resolve).
5. On user approval after interrupt → resume from the failed node (or skip, if user chooses).

### 8.4 Partial Extraction Recovery

- State tracks progress per entity via `extracted_data`. On failure mid-extraction, resume from the last successfully extracted entity (not from scratch).
- The `last_cursor` passed to `extract_data` encodes the position within a table, enabling resume mid-table.

---

## 9. Human‑in‑the‑Loop Details (Gradio Integration)

### 9.1 Gradio UI Components

Gradio UI will have:
- A persistent chat area showing system progress.
- Dynamic panels that appear when the graph interrupts:
  - **Dependency Tree Editor**: checkbox tree or graph visualization; user can add/remove edges.
  - **Match Reviewer**: two‑panel record comparison, buttons "Match" / "Not match" / "Split".
  - **Mapping Reviewer**: table with unmapped fields and dropdowns of destination columns, plus "Add Column" option.
  - **Final Summary**: counts, schemas, approve/abort.

### 9.2 WorkflowManager Pattern

Gradio's state management for long-running workflows with pauses uses a dedicated `WorkflowManager` class:

```python
# src/ui/workflow_manager.py
import threading
import uuid
from src.orchestrator.graph import build_graph
from src.utils.config_loader import load_config

class WorkflowManager:
    def __init__(self):
        self.graph = build_graph()
        self.active_workflows: dict[str, dict] = {}

    def start_workflow(self, user_request: str, thread_id: str | None = None) -> str:
        """Start a new merge workflow. Returns thread_id."""
        thread_id = thread_id or f"wf_{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        resolved = load_config()
        initial_state = {
            "user_request": user_request,
            "origins": resolved.origins,
            "destination": resolved.destination,
        }

        thread = threading.Thread(
            target=self._run_workflow,
            args=(thread_id, initial_state, config),
            daemon=True,
        )
        self.active_workflows[thread_id] = {
            "thread": thread,
            "config": config,
            "status": "running",
            "current_interrupt_node": None,
        }
        thread.start()
        return thread_id

    def _run_workflow(self, thread_id: str, initial_state: dict, config: dict):
        """Run the graph in a background thread."""
        try:
            for event in self.graph.stream(initial_state, config=config):
                # Store state updates for UI polling
                self._update_thread_state(thread_id, event)
        except Exception as e:
            self._update_thread_state(thread_id, {"error": str(e)})

    def get_workflow_status(self, thread_id: str) -> dict:
        """Get current workflow status, progress, and any interrupt data."""
        wf = self.active_workflows.get(thread_id)
        if not wf:
            return {"status": "not_found"}
        return {
            "status": wf["status"],
            "interrupt_data": wf.get("interrupt_data"),
            "current_node": wf.get("current_interrupt_node"),
        }

    def resume_workflow(self, thread_id: str, feedback: dict) -> None:
        """Send human feedback to resume a paused workflow."""
        wf = self.active_workflows[thread_id]
        self.graph.update_state(
            wf["config"],
            {"human_feedback": feedback},
            as_node=wf["current_interrupt_node"],
        )
```

### 9.3 UI Integration

- UI polls `get_workflow_status()` every 2 seconds (via `gr.Ticker`).
- When an interrupt is detected, the interrupt data is rendered in a dynamic panel.
- User submits feedback → calls `resume_workflow()`.
- The background thread continues, and the next status update reflects progress.

---

## 10. Configuration

### 10.1 Credential Management

Separate non-sensitive configuration from secrets:

- **`config/config.yaml`** — non-sensitive config (origin types, connection params without secrets, LLM model selection, blocking rules, retry settings).
- **`config/config.secrets.yaml`** — sensitive values only (passwords, API tokens). This file is in `.gitignore`.
- Environment variable resolution: `${ENV_VAR_NAME}` placeholders in `config.yaml` are resolved at runtime by the config loader.

```yaml
# config/config.yaml (non-sensitive)
origins:
  - id: crm
    type: postgres
    connection:
      host: "{{ CRM_DB_HOST }}"
      port: 5432
      user: "{{ CRM_DB_USER }}"
      password: "{{ CRM_DB_PASSWORD }}"
  - id: hr_system
    type: mongodb
    connection:
      uri: "{{ HR_MONGO_URI }}"
destination:
  type: databricks
  host: xxx.cloud.databricks.com
  http_path: /sql/1.0/warehouses/xxx
  token: "{{ DATABRICKS_TOKEN }}"
  catalog: main
  schema: unified
  tables:
    person: dim_person
    company: dim_company
    product: dim_product
    country: dim_country
llm:
  provider: openai
  model: gpt-4o
  api_key: "{{ OPENAI_API_KEY }}"
blocking_keys:
  person: [email, phone, birth_date, national_id]
  company: [registration_number, domain, name_country]
retry:
  max_attempts: 3
  backoff: exponential
```

```yaml
# config/config.secrets.yaml (gitignored)
# Override or supplement env vars with file-based secrets
# CRM_DB_PASSWORD: actual_password_here
# HR_MONGO_URI: mongodb://user:pass@host/db
# DATABRICKS_TOKEN: dapiXXXXX
# OPENAI_API_KEY: sk-proj-XXXXX
```

### 10.2 Config Validation at Startup

Configuration is validated at startup using Pydantic models. Failures produce clear, actionable errors:

```python
# src/utils/config_loader.py
from pydantic import BaseModel, Field, field_validator
from typing import Optional
import yaml
import os

class OriginConnection(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    uri: Optional[str] = None

class OriginConfig(BaseModel):
    id: str
    type: str  # "postgres" | "mongodb" | "databricks"
    connection: OriginConnection
    priority: int = Field(default=0, ge=0)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        valid = {"postgres", "mongodb", "databricks"}
        if v not in valid:
            raise ValueError(f"Origin type must be one of {valid}, got '{v}'")
        return v

class DestinationConfig(BaseModel):
    type: str = "databricks"
    host: str
    http_path: str
    token: str
    catalog: str
    schema: str
    tables: dict[str, str]  # entity_type -> table_name

class LLMConfig(BaseModel):
    provider: str = "openai"  # "openai" | "azure" | "anthropic"
    model: str
    api_key: str
    base_url: Optional[str] = None

class Config(BaseModel):
    origins: list[OriginConfig] = Field(min_length=1)
    destination: DestinationConfig
    llm: LLMConfig
    blocking_keys: dict[str, list[str]] = {}
    retry: dict = Field(default_factory=lambda: {"max_attempts": 3, "backoff": "exponential"})

    @field_validator("origins")
    @classmethod
    def check_unique_ids(cls, v):
        ids = [o.id for o in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Origin IDs must be unique")
        return v

def load_config(config_path: str = "config/config.yaml",
                secrets_path: str | None = "config/config.secrets.yaml") -> Config:
    """Load and validate configuration, resolving environment variables."""
    import re
    config_data = yaml.safe_load(open(config_path))
    secrets_data = yaml.safe_load(open(secrets_path)) if secrets_path and os.path.exists(secrets_path) else {}

    # Resolve ${ENV_VAR} placeholders
    def resolve_env_vars(obj):
        if isinstance(obj, str):
            return re.sub(r"\{\{(\w+)\}\}",
                          lambda m: os.environ.get(m.group(1), m.group(0)),
                          obj)
        elif isinstance(obj, dict):
            return {k: resolve_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_env_vars(item) for item in obj]
        return obj

    config_data = resolve_env_vars(config_data)
    if secrets_data:
        config_data = _deep_merge(secrets_data, config_data)
    return Config(**config_data)
```

**Validation errors at startup:**
```
ValidationError: 3 validation errors for Config
  origins.0.connection.password
    Field required for postgres origin type [type=missing, input_type=str]
  destination.http_path
    String should match pattern '^/sql/\d\.\d/warehouses/\w+$' [type=pattern_mismatch]
  origins
    Unique constraint failed on 'id' [type=value_error]
```

---

## 11. Example Run Walkthrough

**User:** *“Import all companies and their contacts.”*

1. **Parse:** target_entities = ["company", "person"]
2. **Schema Discovery:** from CRM (Postgres) and HR (Mongo).
3. **Dependency Analysis:** LLM infers person depends on company (through FK `works_for`). Tree: `company: []`, `person: [company]`.
4. **Human:** Approves.
5. **Extract:** Load companies (leaf), then persons (with `_imported=false`).
6. **Resolve:** For persons, CRM has 5000, HR has 2000; LLM matches on email/phone, creates clusters. A few low‑confidence pairs flagged.
7. **Human:** Confirms or splits.
8. **Unify:** Merge attributes; note conflicting phone numbers get added to `conflicts`.
9. **Map:** “first_name” → `first_name`, “dept” unmapped → human decides to map to `department`.
10. **Approve:** Summary shows 1200 companies, 8000 persons, 42 conflicts, 1 unmapped mapped.
11. **Load:** MERGE into Databricks, store conflicts.
12. **Flag:** Set `_imported=true` on all source rows.
13. **Done.**
