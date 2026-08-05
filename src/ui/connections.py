"""
Recent-connection persistence for the merge-engine UI.

Framework-agnostic: plain file I/O over config/recent_connections.json,
used by the FastAPI layer (src/api). Kept separate from any UI framework
so it survives frontend swaps.
"""

from __future__ import annotations

import json
import os

# Overridable so the test suite can redirect history writes to a temp file
# instead of clobbering the user's real saved connections.
CONFIG_PATH = os.environ.get("MERGE_ENGINE_HISTORY_PATH", "config/recent_connections.json")

_CONN_TYPE_ICONS = {
    "postgres": "🐘",
    "postgresql": "🐘",
    "clickhouse": "⚡",
    "mongodb": "🍃",
}


def load_persistent_history() -> dict[str, list]:
    """Read the saved recent-connections file, or an empty structure."""
    if not os.path.exists(CONFIG_PATH):
        return {"recent_origins": [], "recent_destinations": []}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"recent_origins": [], "recent_destinations": []}


def save_persistent_history(history: dict[str, list]) -> None:
    """Persist the recent-connections file (best-effort)."""
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass


def add_to_recent(new_item: dict, max_size: int = 5, kind: str = "origin") -> list[dict]:
    """Append an item to the recent list for `kind`, de-duplicating on
    (host, database, type), and persist. Returns the trimmed list."""
    history = load_persistent_history()
    key = "recent_origins" if kind == "origin" else "recent_destinations"
    existing = history.get(key, []) or []

    new_conn = new_item.get("connection", {})
    new_host = new_conn.get("host")
    new_db = new_conn.get("database")
    new_type = new_item.get("type")

    cleaned: list[dict] = []
    for item in existing:
        if isinstance(item, str):
            continue
        conn = item.get("connection", {})
        if (
            conn.get("host") == new_host
            and conn.get("database") == new_db
            and item.get("type") == new_type
        ):
            continue
        cleaned.append(item)
    cleaned.append(new_item)
    trimmed = cleaned[-max_size:]

    history[key] = trimmed
    save_persistent_history(history)
    return trimmed


def conn_label(item: dict) -> str:
    """Human-readable label for a recent connection (with a type icon)."""
    conn = item.get("connection", {})
    conn_type = item.get("type", "unknown")
    icon = _CONN_TYPE_ICONS.get(conn_type, "🔌")
    host = conn.get("host", "?")
    port = conn.get("port", "?")
    database = conn.get("database", "?")
    return f"{icon} {conn_type} → {host}:{port}/{database}"
