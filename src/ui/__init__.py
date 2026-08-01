"""UI support modules for merge-engine.

The user-facing UI is now a React single-page app (src/ui/frontend)
served by the FastAPI backend (src/api). This package holds the
framework-agnostic helpers that backend still uses: connection-history
persistence and database schema discovery.
"""

from __future__ import annotations

from src.ui import connections
from src.ui.components.db_discovery import test_and_discover_schemas

__all__ = ["connections", "test_and_discover_schemas"]
