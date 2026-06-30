"""
Gradio UI definition and integration for merge-engine.

Provides a web-based chat interface with dynamic panels for each
human-in-the-loop review stage, plus a setup wizard for loading
origin and destination database connections with real-time schema
discovery, auto-complete suggestions, and connection history.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import gradio as gr

from src.ui.components.db_discovery import test_and_discover_schemas
from src.ui.components.dependency_editor import render_dependency_editor
from src.ui.components.match_reviewer import render_match_reviewer
from src.ui.components.mapping_reviewer import render_mapping_reviewer
from src.ui.components.summary_view import render_final_summary
from src.orchestrator.workflow_manager import WorkflowManager
from src.orchestrator.server_manager import LangGraphServerManager

logger = logging.getLogger("merge-engine.ui.gradio_app")

# Custom CSS for better visual styling
CUSTOM_CSS = """
/* Main container */
.gradio-container {
    max-width: 100% !important;
    padding: 0 20px;
}

/* Tab styling */
.tabs .tabitem {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 10px;
    background: #fafafa;
}

/* Accordion styling */
.accordion {
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    margin: 5px 0;
}

/* Button styling */
button.primary {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
}

button.stop {
    background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%) !important;
}

/* Dropdown styling */
select, .gr-dropdown {
    border-radius: 4px;
    border: 1px solid #ccc;
}

/* Textbox styling */
.gr-textbox {
    border-radius: 4px;
}

/* Status messages */
.status-success {
    color: #28a745;
    font-weight: bold;
}

.status-error {
    color: #dc3545;
    font-weight: bold;
}

.status-warning {
    color: #ffc107;
    font-weight: bold;
}

/* Code block */
.code-block {
    border-radius: 4px;
    font-size: 12px;
}

/* Row/Column spacing */
.gr-row { margin: 5px 0; }
.gr-column { padding: 5px; }

/* Header */
h1, h2, h3 { color: #333; }

/* Pipeline chat */
.textbox textarea {
    font-family: 'Courier New', monospace;
    font-size: 13px;
}

/* Progress steps */
.progress-step {
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 4px;
    border-left: 4px solid #ccc;
    background: #f9f9f9;
}
.progress-step.complete {
    border-left-color: #28a745;
    background: #e8f5e9;
}
.progress-step.current {
    border-left-color: #ffc107;
    background: #fff8e1;
}
.progress-step.pending {
    border-left-color: #ccc;
    background: #f5f5f5;
}

/* Event log */
.event-log {
    font-family: 'Courier New', monospace;
    font-size: 12px;
    max-height: 300px;
    overflow-y: auto;
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 10px;
    border-radius: 4px;
}
.event-log .info { color: #4fc3f7; }
.event-log .warn { color: #ffb74d; }
.event-log .error { color: #ef5350; }
.event-log .success { color: #66bb6a; }

/* Connection history card */
.conn-card {
    padding: 8px;
    margin: 4px 0;
    border-radius: 4px;
    background: white;
    border: 1px solid #e0e0e0;
    cursor: pointer;
}
.conn-card:hover {
    background: #f0f0f0;
}

/* Loading spinner overlay */
.loading-overlay {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
}
"""


class ChatState:
    """Per-session chat state for the Gradio UI."""

    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.status: str = "idle"
        self.last_log: str = ""
        # Setup phase data
        self.origins: list[dict[str, Any]] = []
        self.destination: dict[str, Any] = {}
        self.setup_step: str = "origins"
        # Recent connections history (last 5 each)
        self.recent_origins: list[dict[str, Any]] = []
        self.recent_destinations: list[dict[str, Any]] = []
        # Connection profiles for saving
        self.saved_profiles: list[dict[str, Any]] = []
        # Discovered schemas cache
        self.discovered_cache: dict[str, dict] = {}


# Global workflow manager (initialized on app start)
# Can be either WorkflowManager (in-process) or LangGraphServerManager (server mode)
workflow_manager: Any = None
_server_mode: bool = False

# Global system messages log
_system_messages: list[str] = []

# Pipeline progress tracking
_pipeline_steps: list[dict[str, str]] = []

# Server mode flag
_server_mode = False
_server_url = ""


def _log_message(msg: str, level: str = "info") -> str:
    """Append a system message to the chat log with level."""
    _system_messages.append(f"[{level.upper()}] {msg}")
    return "\n".join(_system_messages[-50:])


def _add_to_recent(history: list, new_item: dict, max_size: int = 5) -> list:
    """Add an item to recent history, keeping only the last max_size unique items."""
    cleaned = []
    new_conn = new_item.get("connection", {})
    new_host = new_conn.get("host")
    new_db = new_conn.get("database")
    new_type = new_item.get("type")
    for item in history:
        if isinstance(item, str):
            continue
        item_conn = item.get("connection", {})
        if (item_conn.get("host") == new_host
                and item_conn.get("database") == new_db
                and item.get("type") == new_type):
            continue
        cleaned.append(item)
    cleaned.append(new_item)
    return cleaned[-max_size:]


def _get_recent_display(recent_list: list) -> list[str]:
    """Get display labels for recent connections."""
    if not recent_list:
        return []
    labels = []
    for item in recent_list:
        if isinstance(item, str):
            continue
        conn = item.get("connection", {})
        conn_type = item.get("type", "unknown")
        host = conn.get("host", "?")
        port = conn.get("port", "?")
        database = conn.get("database", "?")
        labels.append(f"{conn_type} → {host}:{port}/{database}")
    return labels


def _find_recent_item(recent_list: list, display_label: str) -> dict | None:
    """Find a recent connection item matching the display label."""
    for item in recent_list:
        if isinstance(item, str):
            continue
        conn = item.get("connection", {})
        conn_type = item.get("type", "unknown")
        host = conn.get("host", "?")
        port = conn.get("port", "?")
        database = conn.get("database", "?")
        expected = f"{conn_type} → {host}:{port}/{database}"
        if expected == display_label:
            return item
    return None


def _format_config_json(origins: list[dict], destination: dict) -> str:
    """Format the complete config as a pretty JSON string."""
    config = {"origins": origins, "destination": destination}
    return json.dumps(config, indent=2)


def _get_pipeline_progress_html() -> str:
    """Generate HTML for pipeline progress visualization."""
    steps = [
        ("Parse Request", "parse_request"),
        ("Analyze Dependencies", "analyze_dependencies"),
        ("Discover Schemas", "discover_schemas"),
        ("Extract Data", "extract_data"),
        ("Resolve Entities", "resolve_entities"),
        ("Map Fields", "map_fields"),
        ("Load Data", "load_data"),
    ]
    html = '<div style="display:flex;flex-wrap:wrap;gap:5px;margin:10px 0;">'
    for label, key in steps:
        found = None
        for s in _pipeline_steps:
            if s["step"] == key:
                found = s
                break
        if found:
            cls = "complete" if found["status"] == "done" else "current"
            icon = "✅" if found["status"] == "done" else "⏳"
        else:
            cls = "pending"
            icon = "⚪"
        html += f'<div class="progress-step {cls}" style="flex:1;min-width:120px;text-align:center;">{icon} {label}</div>'
    html += '</div>'
    return html


def _get_event_log_html() -> str:
    """Generate HTML for event log panel."""
    html = '<div class="event-log">'
    for msg in _system_messages[-100:]:
        msg_lower = msg.lower()
        if "[info]" in msg_lower:
            level_cls = "info"
        elif "[warn]" in msg_lower:
            level_cls = "warn"
        elif "[error]" in msg_lower:
            level_cls = "error"
        elif "[success]" in msg_lower:
            level_cls = "success"
        else:
            level_cls = "info"
        html += f'<div class="{level_cls}">{msg}</div>'
    html += '</div>'
    return html


def add_origin_to_json(
    json_data: list[dict],
    origin_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    priority: int,
    recent_origins: list[dict],
) -> tuple[list[dict], list[str], str, str]:
    """Add a new origin entry to the JSON data list."""
    if not all([host, user, password, database]):
        return json_data, [], "⚠️ Please fill in Host, Username, Password, and Database.", _format_config_json(json_data, {})

    new_origin = {
        "id": f"origin_{len(json_data) + 1}",
        "type": origin_type,
        "connection": {
            "host": host,
            "port": int(port) if port else 5432,
            "user": user,
            "password": password,
            "database": database,
        },
        "priority": int(priority) if priority else 0,
    }
    json_data.append(new_origin)
    recent_origins = _add_to_recent(recent_origins, new_origin)
    msg = f"✅ Added origin `{new_origin['id']}` ({origin_type} → {host}:{port}/{database})"
    return json_data, _get_recent_display(recent_origins), msg, _format_config_json(json_data, {})


def remove_origin_from_json(
    json_data: list[dict],
    index: int,
    recent_origins: list[dict],
) -> tuple[list[dict], list[str], str, str]:
    """Remove an origin entry from the JSON data list by index."""
    if not json_data or index < 0 or index >= len(json_data):
        return json_data, [], "⚠️ Invalid origin index.", _format_config_json(json_data, {})
    removed = json_data.pop(index)
    return json_data, _get_recent_display(recent_origins), f"🗑 Removed origin `{removed.get('id', 'unknown')}`", _format_config_json(json_data, {})


def load_origin_from_recent(
    selected_display: str,
    recent_origins: list[dict],
) -> tuple[str, str, str, str, str, int, str, list[str]]:
    """Load origin fields from a recent connection selection."""
    if not selected_display:
        return "", "", "", "", "", 0, "{}", []

    item = _find_recent_item(recent_origins, selected_display)
    if not item:
        return "", "", "", "", "", 0, "{}", []

    conn = item.get("connection", {})
    origin_type = item.get("type", "postgres")
    host = conn.get("host", "")
    port = conn.get("port", 5432)
    user = conn.get("user", "")
    password = conn.get("password", "")
    database = conn.get("database", "")
    return host, str(port), user, password, database, item.get("priority", 0), _format_config_json([], {}), _get_recent_display(recent_origins)


def get_origin_port_for_type(origin_type: str) -> int:
    """Return default port for origin type."""
    if origin_type == "clickhouse":
        return 8123
    elif origin_type == "mongodb":
        return 27017
    return 5432


# ── Connection Testing with Real-Time Discovery ──────────────────────────────

def test_and_auto_discover_origin(
    origin_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> tuple[str, list[str], list[str], list[str], list[dict]]:
    """Test connection AND discover schemas/tables in one call. Returns status, databases, schemas, tables, table_info."""
    if not all([host, user, password]):
        return "⚠️ Please fill in Host, Username, and Password.", [], [], [], []

    _log_message(f"Testing {origin_type} connection to {host}:{port}...", "info")

    result = test_and_discover_schemas(
        conn_type=origin_type,
        host=host,
        port=int(port) if port else 5432,
        user=user,
        password=password,
        database=database if database else "",
    )

    if result["connected"]:
        msg = f"✅ Connected to {origin_type} at {host}:{port}"
        dbs = result.get("databases", [])
        schemas = result.get("schemas", [])
        tables = result.get("tables", [])
        table_info = result.get("table_columns", {})

        if database and database in dbs:
            msg += f"\n📁 Database '{database}' selected"
        elif dbs:
            msg += f"\n📁 Found {len(dbs)} database(s): {', '.join(dbs[:10])}"

        if schemas:
            msg += f"\n📂 Schemas: {', '.join(schemas[:10])}"
        if tables:
            msg += f"\n📋 Tables: {', '.join(tables[:10])}"
            if len(tables) > 10:
                msg += f"\n  ...and {len(tables) - 10} more"

        # Cache discovery results for auto-fill
        cache_key = f"{origin_type}_{host}_{database}"
        _discovery_cache[cache_key] = result

        _log_message(f"Connection successful: {len(tables)} tables discovered", "success")
        return msg, dbs, schemas, tables, table_info
    else:
        err_msg = f"❌ Connection failed: {result.get('error', 'Unknown error')}"
        _log_message(err_msg, "error")
        return err_msg, [], [], [], []


def test_and_auto_discover_destination(
    dest_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    schema: str,
) -> tuple[str, list[str], list[str], list[str], list[dict]]:
    """Test destination connection AND discover schemas/tables."""
    if not all([host, user, password]):
        return "⚠️ Please fill in Host, Username, and Password.", [], [], [], []

    _log_message(f"Testing {dest_type} destination connection to {host}:{port}...", "info")

    result = test_and_discover_schemas(
        conn_type=dest_type,
        host=host,
        port=int(port) if port else 5432,
        user=user,
        password=password,
        database=database if database else "",
        schema=schema if schema else "",
    )

    if result["connected"]:
        msg = f"✅ Connected to {dest_type} at {host}:{port}"
        dbs = result.get("databases", [])
        schemas = result.get("schemas", [])
        tables = result.get("tables", [])
        table_info = result.get("table_columns", {})

        if database:
            msg += f"\n📁 Database '{database}' selected"
        elif dbs:
            msg += f"\n📁 Found {len(dbs)} database(s): {', '.join(dbs[:10])}"
        if schemas:
            msg += f"\n📂 Schemas: {', '.join(schemas[:10])}"
        if tables:
            msg += f"\n📋 Tables: {', '.join(tables[:10])}"

        cache_key = f"{dest_type}_{host}_dest_{database}"
        _discovery_cache[cache_key] = result

        _log_message("Destination connection successful", "success")
        return msg, dbs, schemas, tables, table_info
    else:
        err_msg = f"❌ Connection failed: {result.get('error', 'Unknown error')}"
        _log_message(err_msg, "error")
        return err_msg, [], [], [], []


# Global discovery cache
_discovery_cache: dict[str, dict] = {}


# ── Connection History & Profiles ────────────────────────────────────────────

def add_connection_profile(
    conn_type: str,
    host: str,
    port: int,
    user: str,
    database: str,
    saved_profiles: list[dict],
) -> tuple[list[dict], str]:
    """Save a connection as a reusable profile."""
    if not all([conn_type, host, user, database]):
        return saved_profiles, "⚠️ Fill in Type, Host, User, and Database."

    profile = {
        "type": conn_type,
        "host": host,
        "port": int(port) if port else 5432,
        "user": user,
        "database": database,
    }
    # Avoid duplicates
    for p in saved_profiles:
        if p.get("host") == host and p.get("database") == database:
            return saved_profiles, f"ℹ️ Profile '{conn_type} → {host}/{database}' already exists."

    saved_profiles.append(profile)
    return saved_profiles, f"✅ Saved connection profile: {conn_type} → {host}:{port}/{database}"


def load_connection_profile(profile: dict) -> tuple[str, int, str, str]:
    """Load fields from a saved connection profile."""
    if not profile:
        return "", 5432, "", ""
    return profile.get("host", ""), profile.get("port", 5432), profile.get("user", ""), profile.get("database", "")


def get_saved_profiles_display(profiles: list[dict]) -> list[str]:
    """Get display labels for saved profiles."""
    if not profiles:
        return []
    return [f"{p.get('type','?')} → {p.get('host')}:{p.get('port')}/{p.get('database')}" for p in profiles]


# ── Origin Handlers ──────────────────────────────────────────────────────────

def test_origin_connection(
    origin_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> tuple[str, list[str], list[str], list[str]]:
    """Test connection and discover databases/schemas/tables.
    
    Returns 4 values to match Gradio output components:
    status_msg, databases, schemas, tables.
    (table_info is cached internally via _discovery_cache)
    """
    result = test_and_auto_discover_origin(origin_type, host, port, user, password, database)
    msg, dbs, schemas, tables, _ = result
    return msg, dbs, schemas, tables


def submit_origins_json(
    json_data: list[dict],
    chat_state: dict,
) -> tuple[str, dict, str]:
    """Submit origin configurations from JSON data."""
    if not json_data:
        return "⚠️ Please add at least one origin database using the form below.", chat_state, _format_config_json([], {})

    chat_state["origins"] = json_data
    chat_state["setup_step"] = "destination"

    summary = f"✅ **{len(json_data)} origin(s) configured:**\n"
    for o in json_data:
        c = o.get("connection", {})
        summary += f"  - `{o.get('id', '?')}` ({o.get('type', '?')}) → {c.get('host')}:{c.get('port')}/{c.get('database')}\n"
    summary += "\nNow configure the destination database."

    _system_messages.append(summary)

    return summary, chat_state, _format_config_json(json_data, chat_state.get("destination", {}))


# ── Destination Handlers ────────────────────────────────────────────────────

def add_destination_to_recent(
    dest_config: dict,
    recent_destinations: list[dict],
) -> list[dict]:
    """Add destination to recent history."""
    return _add_to_recent(recent_destinations, dest_config)


def load_destination_from_recent(
    selected_display: str,
    recent_destinations: list[dict],
) -> tuple[str, str, str, str, str, str, list[str], str]:
    """Load destination fields from a recent connection selection."""
    if not selected_display:
        return "postgresql", "", "", "", "", [], _get_recent_display(recent_destinations), _format_config_json([], {})

    item = _find_recent_item(recent_destinations, selected_display)
    if not item:
        return "postgresql", "", "", "", "", [], _get_recent_display(recent_destinations), _format_config_json([], {})

    conn = item.get("connection", {})
    dest_type = item.get("type", "postgresql")
    host = conn.get("host", "")
    port = conn.get("port", 5432)
    user = conn.get("user", "")
    password = conn.get("password", "")
    database = conn.get("database", "")
    schema = conn.get("schema", "public")
    return dest_type, host, str(port), user, password, database, _get_recent_display(recent_destinations), _format_config_json([], {})


def get_dest_port_for_type(dest_type: str) -> int:
    """Return default port for destination type."""
    if dest_type == "clickhouse":
        return 8123
    return 5432


def test_destination_connection(
    dest_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    schema: str,
) -> tuple[str, list[str], list[str], list[str]]:
    """Test destination connection and discover databases/schemas.
    
    Returns 4 values to match Gradio output components:
    status_msg, databases, schemas, tables.
    (table_info is cached internally via _discovery_cache)
    """
    result = test_and_auto_discover_destination(dest_type, host, port, user, password, database, schema)
    msg, dbs, schemas, tables, _ = result
    return msg, dbs, schemas, tables


def submit_destination(
    dest_type: str,
    dest_host: str,
    dest_port: int,
    dest_user: str,
    dest_password: str,
    dest_database: str,
    dest_schema: str,
    chat_state: dict,
    recent_destinations: list[dict],
    current_origins: list[dict],
) -> tuple[str, dict, Any, list[str], str]:
    """Submit destination configuration and move to pipeline step."""
    if not all([dest_host, dest_user, dest_password, dest_database]):
        return "⚠️ Please fill in all destination database fields.", chat_state, gr.update(), _get_recent_display(recent_destinations), _format_config_json(current_origins, {})

    if dest_type == "clickhouse":
        flat_dest_config = {
            "type": "clickhouse",
            "host": dest_host,
            "port": int(dest_port) if dest_port else 8123,
            "user": dest_user,
            "password": dest_password,
            "database": dest_database,
        }
    else:
        flat_dest_config = {
            "type": "postgresql",
            "host": dest_host,
            "port": int(dest_port) if dest_port else 5432,
            "user": dest_user,
            "password": dest_password,
            "database": dest_database,
            "schema": dest_schema or "public",
        }

    dest_config_for_recent = {
        "type": dest_type,
        "connection": {
            "host": dest_host,
            "port": int(dest_port) if dest_port else 5432,
            "user": dest_user,
            "password": dest_password,
            "database": dest_database,
        },
    }
    if dest_type == "postgresql":
        dest_config_for_recent["connection"]["schema"] = dest_schema or "public"

    recent_destinations = add_destination_to_recent(dest_config_for_recent, recent_destinations)
    chat_state["destination"] = flat_dest_config
    chat_state["recent_destinations"] = recent_destinations
    chat_state["setup_step"] = "pipeline"

    summary = f"✅ **Destination configured:**\n"
    summary += f"  - {dest_type} → {dest_host}:{flat_dest_config.get('port', '?')}/{dest_database}\n"
    if dest_type == "postgresql":
        summary += f"  - Schema: {flat_dest_config.get('schema', 'public')}\n"
    summary += f"  - Origins: {len(chat_state.get('origins', []))}\n"
    summary += "\nYou can now start the merge pipeline."

    _system_messages.append(summary)

    return (
        summary,
        chat_state,
        gr.update(visible=True),
        _get_recent_display(recent_destinations),
        _format_config_json(current_origins, flat_dest_config),
    )


# ── Workflow Handlers ───────────────────────────────────────────────────────

def start_workflow(user_request: str, chat_state: dict) -> tuple:
    """Handle user starting a new merge workflow."""
    global workflow_manager
    logger.debug("ENTER start_workflow")
    logger.debug("user_request (truncated): %.200s", user_request)
    logger.debug("chat_state keys: %s", list(chat_state.keys()))
    if not user_request or not user_request.strip():
        return [[], [["", "Please enter a request."]]], "", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "disabled"

    if not chat_state.get("origins"):
        return [[], [["", "⚠️ Please configure origin databases first in the Setup tab."]]], "", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "disabled"

    if not chat_state.get("destination"):
        return [[], [["", "⚠️ Please configure the destination database first in the Setup tab."]]], "", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "disabled"

    if workflow_manager is None:
        logger.warning("Workflow manager is None")
        return [[], [["", "Workflow manager not initialized."]]], "", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "disabled"

    origins = chat_state["origins"]
    destination = chat_state["destination"]
    logger.debug("origins count: %d", len(origins))
    logger.debug("destination: %s", json.dumps(destination, default=str)[:300])

    # Reset pipeline steps
    global _pipeline_steps
    _pipeline_steps = []

    logger.debug("Calling workflow_manager.start_workflow()")
    thread_id = workflow_manager.start_workflow(user_request, _config_path, origins=origins, destination=destination)
    chat_state["thread_id"] = thread_id
    chat_state["status"] = "running"
    logger.debug("Thread ID: %s", thread_id)

    _pipeline_steps = [
        {"step": "parse_request", "status": "running", "message": "Parsing request..."},
    ]

    response = [
        [user_request, None],
        [None, f"Workflow started (`{thread_id}`). Analyzing request..."],
    ]

    _system_messages.append(f"Workflow {thread_id} started with {len(origins)} origin(s).")
    logger.debug("EXIT start_workflow (thread_id=%s)", thread_id)
    return response, "", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "disabled"


def poll_status(chat_state: dict) -> tuple:
    """Poll workflow status and update panels if interrupted."""
    global workflow_manager
    logger.debug("ENTER poll_status")
    logger.debug("chat_state status: %s, thread_id: %s", chat_state.get("status"), chat_state.get("thread_id"))
    if not workflow_manager or chat_state.get("status") in ("idle", "completed", "failed", "aborted"):
        logger.debug("poll_status: early return (status=%s)", chat_state.get("status"))
        return _get_event_log_html(), _get_pipeline_progress_html(), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", "disabled"

    thread_id = chat_state.get("thread_id")
    if not thread_id:
        return _get_event_log_html(), _get_pipeline_progress_html(), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", "disabled"

    status = workflow_manager.get_workflow_status(thread_id)
    logger.debug("Workflow status response: %s", json.dumps({k: v for k, v in status.items() if k != "thread_state"}, default=str)[:500])
    wf_status = status.get("status", "running")
    interrupt_data = status.get("interrupt_data")
    current_node = status.get("current_interrupt_node", "")
    errors = status.get("errors", [])

    chat_state["status"] = wf_status
    logger.debug("Updated chat_state status to: %s", wf_status)

    # Log any new errors from state
    for err in errors:
        if err and err not in _system_messages:
            _log_message(f"ERROR: {err}", "error")

    # Update pipeline progress with proper node tracking
    step_order = ["parse_request", "analyze_dependencies", "discover_schemas", "extract_data", "resolve_entities", "map_fields", "load_data"]
    
    # Determine which step is currently active based on wf_status
    current_idx = -1
    if current_node and current_node in step_order:
        current_idx = step_order.index(current_node)
    elif wf_status == "completed":
        current_idx = len(step_order) - 1
    elif wf_status == "running" and current_idx < 0:
        # Try to figure out from the global _pipeline_steps which is running
        # Use a local reference to avoid UnboundLocalError from later reassignment
        _running_steps = list(_pipeline_steps) if _pipeline_steps else []
        for i, s in enumerate(step_order):
            for ps in _running_steps:
                if ps["step"] == s and ps.get("status") == "running":
                    current_idx = i
                    break
            if current_idx >= 0:
                break

    # Build a fresh pipeline steps list based on current progress
    _pipeline_steps = []
    for i, s in enumerate(step_order):
        if i < current_idx:
            _pipeline_steps.append({"step": s, "status": "done", "message": ""})
        elif i == current_idx:
            _pipeline_steps.append({
                "step": s,
                "status": "running",
                "message": f"Processing: {current_node}" if current_node else "Processing...",
            })
        else:
            _pipeline_steps.append({"step": s, "status": "pending", "message": ""})

    # Status messages for the event log
    status_messages = {
        "running": f"Running node: {current_node or '...'}..." if current_node else "Running workflow...",
        "interrupted": "Review required at checkpoint.",
        "completed": "Workflow completed successfully!",
        "failed": "Workflow failed.",
        "aborted": "Workflow aborted.",
    }
    status_msg = status_messages.get(wf_status, "Running...")
    _log_message(status_msg, "info" if wf_status == "running" else ("success" if wf_status == "completed" else "error"))
    logger.debug("EXIT poll_status (status=%s, interrupt=%s)", wf_status, "yes" if interrupt_data else "no")

    dep_panel = gr.update(visible=False)
    match_panel = gr.update(visible=False)
    mapping_panel = gr.update(visible=False)
    summary_panel = gr.update(visible=False)
    resume_btn_state = "disabled"

    if interrupt_data:
        # Handle both dict and other formats
        if isinstance(interrupt_data, dict):
            node = interrupt_data.get("node", current_interrupt_node or "")
            data = interrupt_data.get("data", {})
        else:
            # Fallback for non-dict interrupt_data
            node = current_interrupt_node or ""
            data = {}

        if node == "human_review_deps":
            dep_panel = gr.update(
                value=render_dependency_editor(
                    data.get("dependency_tree", {}),
                    data.get("extraction_order", []),
                ),
                visible=True,
            )
            resume_btn_state = "enabled"
            _log_message("Checkpoint: Dependency review required", "warn")

        elif node == "human_review_matches":
            clusters = data.get("low_confidence_clusters", [])
            match_panel = gr.update(
                value=render_match_reviewer(clusters),
                visible=True,
            )
            resume_btn_state = "enabled"
            _log_message("Checkpoint: Entity match review required", "warn")

        elif node == "human_review_mappings":
            unmapped = data.get("unmapped_fields", [])
            schema_changes = data.get("pending_schema_changes", [])
            mapping_panel = gr.update(
                value=render_mapping_reviewer(unmapped, schema_changes),
                visible=True,
            )
            resume_btn_state = "enabled"
            _log_message("Checkpoint: Field mapping review required", "warn")

        elif node == "human_final_approval":
            summary = data.get("summary", {})
            schema_changes = data.get("pending_schema_changes", [])
            summary_panel = gr.update(
                value=render_final_summary(summary, schema_changes),
                visible=True,
            )
            resume_btn_state = "enabled"
            _log_message("Checkpoint: Final approval required", "warn")

        elif node == "handle_error":
            error = data.get("error", "Unknown error")
            _log_message(f"ERROR: {error}", "error")
            chat_state["status"] = "failed"

    return _get_event_log_html(), _get_pipeline_progress_html(), dep_panel, match_panel, mapping_panel, summary_panel, chat_state.get("thread_id", ""), resume_btn_state


def submit_feedback(
    decision: str,
    chat_state: dict,
) -> tuple:
    """Handle human feedback from interrupt review panels."""
    global workflow_manager
    logger.debug("ENTER submit_feedback (decision=%s)", decision)
    thread_id = chat_state.get("thread_id")

    if not workflow_manager or not thread_id:
        logger.warning("submit_feedback: no workflow_manager or thread_id")
        return _get_event_log_html(), _get_pipeline_progress_html(), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", "disabled"

    feedback: dict[str, Any] = {"approved": True, "decision": decision}
    logger.debug("feedback: %s", json.dumps(feedback, default=str)[:300])

    if chat_state.get("status") == "interrupted":
        logger.debug("Calling resume_workflow for thread_id=%s", thread_id)
        workflow_manager.resume_workflow(thread_id, feedback)
        _log_message(f"Feedback submitted for workflow {thread_id}: approved", "success")
        logger.debug("EXIT submit_feedback (resumed)")

    return _get_event_log_html(), _get_pipeline_progress_html(), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", "disabled"


def abort_workflow(chat_state: dict) -> tuple:
    """Abort the current workflow."""
    global workflow_manager
    logger.debug("ENTER abort_workflow")
    thread_id = chat_state.get("thread_id")

    if workflow_manager and thread_id:
        logger.debug("Calling abort_workflow for thread_id=%s", thread_id)
        workflow_manager.abort_workflow(thread_id)
        chat_state["status"] = "aborted"
        _log_message(f"Workflow {thread_id} aborted.", "warn")
        logger.debug("EXIT abort_workflow")

    return _get_event_log_html(), _get_pipeline_progress_html(), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", "disabled"


# Module-level config path
_config_path: str = "config/config.yaml"


def create_app(
    config_path: str = "config/config.yaml",
    server_name: str = "0.0.0.0",
    server_port: int = 7860,
    langgraph_server_url: str = "",
) -> gr.Blocks:
    """Create the Gradio application with full UI features.
    
    Parameters
    ----------
    config_path : str
        Path to config.yaml (legacy, used for in-process mode).
    server_name : str
        Gradio server bind address.
    server_port : int
        Gradio server port.
    langgraph_server_url : str
        URL of running LangGraph CLI server. If provided, uses server mode
        with LangGraphServerManager instead of in-process WorkflowManager.
    """
    global workflow_manager, _system_messages, _config_path, _server_mode, _server_url

    _system_messages = []
    _config_path = config_path
    _server_url = langgraph_server_url
    
    if langgraph_server_url:
        # Server mode: use LangGraphServerManager
        _server_mode = True
        workflow_manager = LangGraphServerManager(server_url=langgraph_server_url)
        logger.info("Initialized LangGraphServerManager (server mode: %s)", langgraph_server_url)
    else:
        # In-process mode: use WorkflowManager
        workflow_manager = WorkflowManager()
        _server_mode = False
        logger.info("Initialized WorkflowManager (in-process mode)")

    with gr.Blocks(title="Merge Engine") as app:
        chat_state = gr.State({})
        recent_origins_state = gr.State([])
        recent_destinations_state = gr.State([])
        saved_profiles_state = gr.State([])

        gr.Markdown("# 🔀 Merge Engine")
        gr.Markdown("Natural language → fully migrated, deduplicated, and unified entities into PostgreSQL.")

        with gr.Tabs():
            # ===== SETUP TAB =====
            with gr.Tab("⚙️ Setup"):
                gr.Markdown("Configure origin databases and destination before running the pipeline.")

                with gr.Row():
                    # ===== LEFT COLUMN: Origins =====
                    with gr.Column(scale=1):
                        with gr.Accordion("📤 Origin Databases", open=True):
                            # Connection profile section
                            with gr.Accordion("Connection Profiles", open=False):
                                profile_name = gr.Dropdown(
                                    label="Saved Profiles",
                                    choices=[],
                                    value="",
                                    allow_custom_value=True,
                                )
                                load_profile_btn = gr.Button("📥 Load Profile", variant="secondary", size="sm")
                                save_profile_btn = gr.Button("💾 Save as Profile", variant="secondary", size="sm")

                            # Recent origins
                            gr.Markdown("### Recent Connections")
                            recent_origins_dropdown = gr.Dropdown(
                                label="Recent Origins — click to load",
                                choices=[],
                                value="",
                                allow_custom_value=True,
                            )
                            load_recent_origin_btn = gr.Button("📥 Load Selected", variant="secondary", size="sm")

                            # Add new origin form
                            with gr.Accordion("Add New Origin", open=True):
                                with gr.Row():
                                    with gr.Column(scale=1):
                                        origin_type = gr.Dropdown(
                                            label="Type",
                                            choices=["postgres", "mongodb", "clickhouse"],
                                            value="postgres",
                                        )
                                    with gr.Column(scale=2):
                                        origin_host = gr.Textbox(label="Host", placeholder="e.g., localhost")
                                    with gr.Column(scale=1):
                                        origin_port = gr.Number(label="Port", value=5432)

                                with gr.Row():
                                    with gr.Column(scale=2):
                                        origin_user = gr.Textbox(label="Username", placeholder="db_user")
                                    with gr.Column(scale=2):
                                        origin_password = gr.Textbox(label="Password", type="password", placeholder="••••••••")
                                    with gr.Column(scale=1):
                                        origin_database = gr.Dropdown(
                                            label="Database (type or select from discovered)",
                                            choices=[],
                                            value="",
                                            allow_custom_value=True,
                                        )

                                # Auto-discover button
                                origin_discover_btn = gr.Button("🔗 Connect & Discover", variant="primary", size="sm")
                                origin_discover_status = gr.Textbox(label="Connection Status", interactive=False, lines=2, placeholder="Click 'Connect & Discover' to test...")

                                with gr.Row():
                                    origin_priority = gr.Number(label="Priority (0-100)", value=0, minimum=0, maximum=100, scale=1)
                                    add_origin_btn = gr.Button("➕ Add Origin", variant="primary", scale=1)

                            # Origins JSON display
                            origin_json = gr.JSON(label="Configured Origins", value=[])
                            origin_msg = gr.Textbox(label="", interactive=False, lines=1)
                            submit_origins_btn = gr.Button("✅ Save Origins & Continue", variant="stop")
                            origins_output = gr.Textbox(label="Status", interactive=False, lines=3, placeholder="Origins status will appear here...")

                    # ===== MIDDLE COLUMN: Destination =====
                    with gr.Column(scale=1):
                        with gr.Accordion("📥 Destination Database", open=True):
                            # Recent destinations
                            recent_destinations_dropdown = gr.Dropdown(
                                label="Recent Destinations — click to load",
                                choices=[],
                                value="",
                                allow_custom_value=True,
                            )
                            load_recent_dest_btn = gr.Button("📥 Load Selected", variant="secondary", size="sm")

                            dest_type = gr.Dropdown(label="Type", choices=["postgresql", "clickhouse"], value="postgresql")

                            with gr.Row():
                                with gr.Column(scale=2):
                                    dest_host = gr.Textbox(label="Host", placeholder="e.g., localhost")
                                    dest_port = gr.Number(label="Port", value=5432)
                                with gr.Column(scale=1):
                                    dest_schema = gr.Dropdown(
                                        label="Schema (or select from discovered)",
                                        choices=[],
                                        value="",
                                        allow_custom_value=True,
                                    )

                            with gr.Row():
                                with gr.Column(scale=2):
                                    dest_user = gr.Textbox(label="Username", placeholder="db_admin")
                                    dest_password = gr.Textbox(label="Password", type="password", placeholder="••••••••")
                                with gr.Column(scale=1):
                                    dest_database = gr.Dropdown(
                                        label="Database (or select from discovered)",
                                        choices=[],
                                        value="",
                                        allow_custom_value=True,
                                    )

                            # Auto-discover button
                            dest_discover_btn = gr.Button("🔗 Connect & Discover", variant="primary", size="sm")
                            dest_discover_status = gr.Textbox(label="Connection Status", interactive=False, lines=2, placeholder="Click 'Connect & Discover' to test...")

                            submit_dest_btn = gr.Button("✅ Save Destination & Continue", variant="primary")
                            dest_output = gr.Textbox(label="Status", interactive=False, lines=3, placeholder="Destination status will appear here...")

                    # ===== RIGHT COLUMN: Config Preview =====
                    with gr.Column(scale=1):
                        with gr.Accordion("📋 Configuration", open=False):
                            config_preview = gr.Code(
                                label="Configuration JSON",
                                language="json",
                                value="{}  # Add origins and destination to see config",
                            )

                        with gr.Accordion("💾 Saved Profiles", open=False):
                            saved_profiles_dropdown = gr.Dropdown(
                                label="Saved Connection Profiles",
                                choices=[],
                                value="",
                                allow_custom_value=True,
                            )
                            load_saved_profile_btn = gr.Button("📥 Load Profile", variant="secondary", size="sm")
                            profiles_display = gr.Dropdown(
                                label="Profile List",
                                choices=[],
                                value="",
                                interactive=False,
                            )
                            profile_status = gr.Textbox(label="", interactive=False, lines=2)

                        with gr.Accordion("📊 Pipeline Status", open=False, visible=False) as pipeline_status_accord:
                            pipeline_progress_html = gr.HTML(label="Progress")
                            event_log_html = gr.HTML(label="Event Log")

            # ===== PIPELINE TAB =====
            with gr.Tab("🚀 Pipeline"):
                gr.Markdown("Enter your natural language request and start the merge pipeline.")

                with gr.Row():
                    with gr.Column(scale=4):
                        request_input = gr.Textbox(
                            label="Your Request",
                            placeholder="e.g., merging boeing company - import all companies, contacts, and orders from all origins",
                            lines=3,
                        )
                        with gr.Row():
                            submit_btn = gr.Button("🚀 Start Merge", variant="primary")
                            abort_btn = gr.Button("⏹ Abort", variant="secondary")

                    with gr.Column(scale=1):
                        status_output = gr.Textbox(label="Status", interactive=False, lines=1)

                # Pipeline progress & event log
                with gr.Row():
                    with gr.Column(scale=1):
                        pipeline_progress_html = gr.HTML(label="Pipeline Progress")
                    with gr.Column(scale=1):
                        event_log_html = gr.HTML(label="Event Log")

                chat_output = gr.Textbox(
                    label="Activity Log",
                    interactive=False,
                    lines=10,
                    autoscroll=True,
                )

                # Interrupt panels
                with gr.Accordion("Dependency Review", open=False) as dep_accordion:
                    dep_panel = gr.HTML(visible=False)
                    with gr.Row():
                        dep_approve = gr.Button("✅ Approve Dependencies", variant="stop")
                        dep_reject = gr.Button("❌ Reject", variant="secondary")

                with gr.Accordion("Entity Match Review", open=False) as match_accordion:
                    match_panel = gr.HTML(visible=False)
                    with gr.Row():
                        match_approve = gr.Button("✅ Approve Matches", variant="stop")
                        match_reject = gr.Button("❌ Reject", variant="secondary")

                with gr.Accordion("Field Mapping Review", open=False) as mapping_accordion:
                    mapping_panel = gr.HTML(visible=False)
                    with gr.Row():
                        mapping_approve = gr.Button("✅ Approve Mappings", variant="stop")
                        mapping_reject = gr.Button("❌ Reject", variant="secondary")

                with gr.Accordion("Final Approval", open=False) as summary_accordion:
                    summary_panel = gr.HTML(visible=False)
                    with gr.Row():
                        summary_approve = gr.Button("✅ Approve & Load", variant="stop")
                        summary_reject = gr.Button("❌ Abort", variant="secondary")

            # ===== DATA SOURCES TAB =====
            with gr.Tab("📁 Data Sources"):
                gr.Markdown("View and manage discovered data sources across all origins.")

                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### Discovered Sources")
                        source_status = gr.Textbox(label="Status", interactive=False, lines=4, placeholder="Connect to origins to see discovered sources...")

                with gr.Accordion("Origin Details", open=True):
                    origin_sources_json = gr.JSON(label="Source Schemas", value={})

                with gr.Accordion("Discovery Cache", open=False):
                    cache_status = gr.Textbox(label="Cache Info", interactive=False, lines=2, placeholder="Discovery results cached after connection...")

        # ── Setup Event Handlers ──────────────────────────────────────────

        # Origin: Type change updates default port
        origin_type.change(
            fn=lambda t: get_origin_port_for_type(t),
            inputs=[origin_type],
            outputs=[origin_port],
        )

        # Origin: Connect & Discover button - status, databases, schemas, tables, table_info
        origin_discover_btn.click(
            fn=test_origin_connection,
            inputs=[origin_type, origin_host, origin_port, origin_user, origin_password, origin_database],
            outputs=[origin_discover_status, origin_database, dest_schema, origin_database],
        )

        # Origin: Add origin button
        add_origin_btn.click(
            fn=lambda otype, ohost, op, ou, opass, odb, opri, jdata, recent: add_origin_to_json(
                jdata, otype, ohost, op, ou, opass, odb, opri, recent
            ),
            inputs=[origin_type, origin_host, origin_port, origin_user, origin_password, origin_database, origin_priority, origin_json, recent_origins_state],
            outputs=[origin_json, origin_msg, config_preview, recent_origins_state],
        )

        # Origin: Load from recent
        load_recent_origin_btn.click(
            fn=lambda sel, recent: load_origin_from_recent(sel, recent),
            inputs=[recent_origins_dropdown, recent_origins_state],
            outputs=[origin_host, origin_port, origin_user, origin_password, origin_database, origin_priority, config_preview, recent_origins_state],
        )

        # Origin: Submit origins
        submit_origins_btn.click(
            fn=submit_origins_json,
            inputs=[origin_json, chat_state],
            outputs=[origins_output, chat_state, config_preview],
        )

        # Destination: Type change updates default port
        dest_type.change(
            fn=lambda t: get_dest_port_for_type(t),
            inputs=[dest_type],
            outputs=[dest_port],
        )

        # Destination: Connect & Discover button
        dest_discover_btn.click(
            fn=test_destination_connection,
            inputs=[dest_type, dest_host, dest_port, dest_user, dest_password, dest_database, dest_schema],
            outputs=[dest_discover_status, dest_database, dest_schema, dest_database],
        )

        # Destination: Load from recent
        load_recent_dest_btn.click(
            fn=lambda sel, dest_type_val, recent: load_destination_from_recent(sel, recent),
            inputs=[recent_destinations_dropdown, dest_type, recent_destinations_state],
            outputs=[dest_type, dest_host, dest_port, dest_user, dest_password, dest_database, config_preview, recent_destinations_state],
        )

        # Destination: Submit destination
        submit_dest_btn.click(
            fn=lambda dtype, dhost, dport, duser, dpass, ddb, dschema, state, recent_d, origins: submit_destination(
                dtype, dhost, dport, duser, dpass, ddb, dschema, state, recent_d, origins
            ),
            inputs=[dest_type, dest_host, dest_port, dest_user, dest_password, dest_database, dest_schema, chat_state, recent_destinations_state, origin_json],
            outputs=[dest_output, chat_state, dest_discover_status, recent_destinations_state, config_preview],
        )

        # Pipeline event handlers
        submit_btn.click(
            fn=start_workflow,
            inputs=[request_input, chat_state],
            outputs=[chat_output, request_input, dep_panel, match_panel, mapping_panel, summary_panel, submit_btn],
        ).then(
            fn=poll_status,
            inputs=[chat_state],
            outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel, status_output, submit_btn],
        )

        # Poll ticker (every 2 seconds)
        timer = gr.Timer(2)
        timer.tick(
            fn=poll_status,
            inputs=[chat_state],
            outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel, status_output, submit_btn],
        )

        # Approve buttons
        for btn, panel in [
            (dep_approve, dep_panel),
            (match_approve, match_panel),
            (mapping_approve, mapping_panel),
            (summary_approve, summary_panel),
        ]:
            btn.click(
                fn=lambda s=chat_state: submit_feedback("approve", s),
                inputs=[chat_state],
                outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel],
            ).then(
                fn=poll_status,
                inputs=[chat_state],
                outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel, status_output, submit_btn],
            )

        # Reject buttons
        for btn, panel in [
            (dep_reject, dep_panel),
            (match_reject, match_panel),
            (mapping_reject, mapping_panel),
            (summary_reject, summary_panel),
        ]:
            btn.click(
                fn=lambda s=chat_state: submit_feedback("reject", s),
                inputs=[chat_state],
                outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel],
            ).then(
                fn=abort_workflow,
                inputs=[chat_state],
                outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel],
            )

        # Abort button
        abort_btn.click(
            fn=abort_workflow,
            inputs=[chat_state],
            outputs=[event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel],
        )

        # Connection profile handlers
        save_profile_btn.click(
            fn=lambda otype, ohost, op, ou, odb, profiles: add_connection_profile(otype, ohost, op, ou, odb, profiles),
            inputs=[origin_type, origin_host, origin_port, origin_user, origin_database, saved_profiles_state],
            outputs=[saved_profiles_state, profile_status],
        )

        load_saved_profile_btn.click(
            fn=lambda profiles, sel: load_connection_profile(_find_recent_item(profiles, sel) if profiles else {}),
            inputs=[saved_profiles_state, saved_profiles_dropdown],
            outputs=[origin_host, origin_port, origin_user, origin_database],
        )

    return app