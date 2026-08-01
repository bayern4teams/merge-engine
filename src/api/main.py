"""
FastAPI backend for merge-engine.

Thin JSON wrapper around the existing, framework-agnostic backend:
`WorkflowManager` (merge execution + HITL interrupts), `db_discovery`
(connection testing / schema discovery), and `connections` (recent-
connection persistence). Also serves the built React frontend so the
whole app runs as one process on one port.

No merge/business logic lives here — every endpoint delegates.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.ui import connections
from src.ui.components.db_discovery import test_and_discover_schemas
from src.orchestrator.workflow_manager import WorkflowManager
from src.llm.cost import COST_LEDGER, SEMANTIC_CACHE
from src.orchestrator.profiles import (
    build_profile,
    delete_profile,
    list_profiles,
    load_profile,
    save_profile,
)

logger = logging.getLogger("merge-engine.api")

# Single long-lived manager shared across requests (mirrors the one the
# Gradio app held). Built lazily so importing this module is cheap.
_manager: WorkflowManager | None = None
_config_path = "config/config.yaml"

# Where the built React bundle lands (Vite `outDir`). Absolute so it
# resolves regardless of the process CWD.
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "ui" / "frontend" / "dist"


def get_manager() -> WorkflowManager:
    global _manager
    if _manager is None:
        _manager = WorkflowManager()
    return _manager


# ── Request models ───────────────────────────────────────────────────────────


class ConnectionTest(BaseModel):
    conn_type: str
    host: str
    port: int
    user: str = ""
    password: str = ""
    database: str = ""
    schema_name: str = ""
    table: str = ""


class RecentAdd(BaseModel):
    kind: str  # "origin" | "destination"
    item: dict[str, Any]


class WorkflowStart(BaseModel):
    request: str
    origins: list[dict[str, Any]]
    destination: dict[str, Any]
    # Replay a saved profile's pinned decisions instead of re-deriving them.
    profile_name: str | None = None
    # Write this run's decisions back to that profile when it completes.
    save_as_profile: str | None = None
    # Ignore any tracked watermark and re-extract every table in full. Needed
    # when the destination was edited (rows deleted) and the user wants them
    # back — an incremental run would see nothing new and load 0.
    full_remerge: bool = False
    # Per-field survivorship override for this run (Phase 5, O.3), e.g.
    # {"revenue": ["numeric_max"], "_default": ["priority", "recency"]}.
    # Takes effect ahead of a saved profile's pinned conflict_policy.
    conflict_policy: dict[str, Any] | None = None


class ProfileSave(BaseModel):
    """Save a configured run so it can be repeated without the wizard."""

    name: str
    request: str
    origins: list[dict[str, Any]]
    destination: dict[str, Any]
    description: str = ""


class Feedback(BaseModel):
    """A decision at one HITL gate.

    ``decision`` is one of:

    ``approve``  continue with what the pipeline produced
    ``edit``     continue, applying the edits carried in this payload
    ``retry``    send the work back to the node that produced it, with
                 ``reason`` added to that node's prompt
    ``reject``   stop the run; nothing is written to the destination
    ``abort``    synonym for ``reject``; the natural word at the error gate
    ``skip``     error gate only: continue past a step that failed, leaving
                 the merge knowingly incomplete by that step's output
    """

    decision: str
    reason: str | None = None
    mapping_edits: list[dict[str, Any]] | None = None
    cluster_decisions: list[dict[str, Any]] | None = None
    entity_tables: dict[str, list[dict[str, Any]]] | None = None


_DECISIONS = {"approve", "edit", "retry", "reject", "abort", "skip"}


# ── App ──────────────────────────────────────────────────────────────────────


def create_api(config_path: str = "config/config.yaml") -> FastAPI:
    global _config_path
    _config_path = config_path

    app = FastAPI(title="merge-engine", version="1.0.0")

    # Dev convenience: the Vite dev server (5173) calls this API cross-origin.
    # In production the bundle is served same-origin from here, so this is
    # harmless.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Connections ──────────────────────────────────────────────────────────

    @app.post("/api/connections/test")
    def test_connection(body: ConnectionTest) -> dict[str, Any]:
        """Test a DB connection and discover databases/schemas/tables/columns."""
        return test_and_discover_schemas(
            conn_type=body.conn_type,
            host=body.host,
            port=body.port,
            user=body.user,
            password=body.password,
            database=body.database,
            schema=body.schema_name,
            table=body.table,
        )

    @app.get("/api/connections/recent")
    def recent_connections() -> dict[str, Any]:
        """Return saved recent origins/destinations, each with a display label."""
        history = connections.load_persistent_history()

        def _decorate(items: list) -> list[dict[str, Any]]:
            out = []
            for it in items or []:
                if isinstance(it, str):
                    continue
                out.append({**it, "label": connections.conn_label(it)})
            return out

        return {
            "recent_origins": _decorate(history.get("recent_origins", [])),
            "recent_destinations": _decorate(history.get("recent_destinations", [])),
        }

    @app.post("/api/connections/recent")
    def add_recent(body: RecentAdd) -> dict[str, Any]:
        """Append an item to the recent list for its kind and persist."""
        kind = "origin" if body.kind == "origin" else "destination"
        trimmed = connections.add_to_recent(body.item, kind=kind)
        return {
            "kind": kind,
            "items": [
                {**it, "label": connections.conn_label(it)} for it in trimmed
            ],
        }

    # ── Workflow ─────────────────────────────────────────────────────────────

    @app.post("/api/workflow/start")
    def start_workflow(body: WorkflowStart) -> dict[str, Any]:
        if not body.request.strip():
            raise HTTPException(400, "Request text is required.")
        if not body.origins:
            raise HTTPException(400, "Configure at least one origin database.")
        if not body.destination:
            raise HTTPException(400, "Configure the destination database.")
        profile_name = body.profile_name
        if body.save_as_profile:
            # Create it up front so the run has somewhere to pin its
            # decisions when it finishes.
            try:
                save_profile(build_profile(
                    body.save_as_profile,
                    user_request=body.request,
                    origins=body.origins,
                    destination=body.destination,
                ))
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            profile_name = body.save_as_profile
        try:
            thread_id = get_manager().start_workflow(
                body.request,
                _config_path,
                origins=body.origins,
                destination=body.destination,
                profile_name=profile_name,
                full_remerge=body.full_remerge,
                conflict_policy=body.conflict_policy,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:
            logger.error("start_workflow failed: %s", exc, exc_info=True)
            raise HTTPException(500, f"Failed to start workflow: {exc}")
        return {"thread_id": thread_id, "profile_name": profile_name}

    # ── Saved run profiles ───────────────────────────────────────────────────

    @app.get("/api/profiles")
    def profiles_list() -> dict[str, Any]:
        return {"profiles": list_profiles()}

    @app.get("/api/profiles/{name}")
    def profile_get(name: str) -> dict[str, Any]:
        profile = load_profile(name)
        if profile is None:
            raise HTTPException(404, f"No saved profile named {name!r}.")
        return profile

    @app.post("/api/profiles")
    def profile_save(body: ProfileSave) -> dict[str, Any]:
        if not body.name.strip():
            raise HTTPException(422, "A profile needs a name.")
        try:
            profile = build_profile(
                body.name,
                user_request=body.request,
                origins=body.origins,
                destination=body.destination,
                description=body.description,
            )
            save_profile(profile)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"saved": profile["name"], "id": profile["id"]}

    @app.delete("/api/profiles/{name}")
    def profile_delete(name: str) -> dict[str, Any]:
        if not delete_profile(name):
            raise HTTPException(404, f"No saved profile named {name!r}.")
        return {"deleted": name}

    @app.get("/api/workflow/active")
    def active_workflows() -> dict[str, Any]:
        return {"active": get_manager().list_active_workflows()}

    @app.get("/api/workflow/{thread_id}/status")
    def workflow_status(thread_id: str) -> dict[str, Any]:
        return get_manager().get_workflow_status(thread_id)

    @app.get("/api/workflow/{thread_id}/cost")
    def workflow_cost(thread_id: str) -> dict[str, Any]:
        """Tokens this run spent, and what the cache saved it (O.5)."""
        return {
            "run": COST_LEDGER.for_run(thread_id),
            "cache": SEMANTIC_CACHE.stats(),
        }

    @app.post("/api/workflow/{thread_id}/feedback")
    def workflow_feedback(thread_id: str, body: Feedback) -> dict[str, Any]:
        manager = get_manager()

        decision = (body.decision or "").strip().lower()
        if decision not in _DECISIONS:
            raise HTTPException(
                422,
                f"Unknown decision '{body.decision}'. "
                f"Expected one of: {', '.join(sorted(_DECISIONS))}.",
            )

        # Approve-race guard: re-fetch live status (with a short retry) before
        # resuming, exactly as the old Gradio submit_feedback did — the first
        # approve after an interrupt can otherwise be a silent no-op if the
        # client's cached status hadn't caught up.
        live = manager.get_workflow_status(thread_id)
        if live.get("status") != "interrupted":
            time.sleep(0.5)
            live = manager.get_workflow_status(thread_id)
        if live.get("status") != "interrupted":
            time.sleep(1.0)
            live = manager.get_workflow_status(thread_id)
        if live.get("status") != "interrupted":
            raise HTTPException(
                409,
                f"Workflow is not paused (status={live.get('status')}). "
                "Please wait a moment and try again.",
            )

        node = live.get("current_interrupt_node") or "human_review_deps"

        # "skip" means "continue past a step that failed" and only the error
        # gate has a failed step to skip. Accepting it at a review gate would
        # quietly read as approval of output nobody actually approved.
        if decision == "skip" and node != "handle_error":
            raise HTTPException(
                422,
                "'skip' is only valid at the error gate; this run is paused "
                f"at '{node}'. Use approve, edit, retry, or reject.",
            )

        feedback: dict[str, Any] = {
            # "approved" is what the older node bodies and the final-approval
            # guard read; "edit" is an approval that also carries changes.
            "approved": decision in ("approve", "edit"),
            "decision": decision,
            "node": node,
        }
        if body.reason:
            feedback["reason"] = body.reason
        if body.mapping_edits is not None:
            feedback["mapping_edits"] = body.mapping_edits
        if body.cluster_decisions is not None:
            feedback["cluster_decisions"] = body.cluster_decisions
        if body.entity_tables is not None:
            feedback["entity_tables"] = body.entity_tables

        manager.resume_workflow(thread_id, feedback)
        # Return the status right after resuming so the client can react
        # immediately (a mapping-edit validation failure re-interrupts and
        # surfaces the error in interrupt_data.data.error).
        return manager.get_workflow_status(thread_id)

    @app.post("/api/workflow/{thread_id}/abort")
    def workflow_abort(thread_id: str) -> dict[str, Any]:
        get_manager().abort_workflow(thread_id)
        return {"ok": True}

    # ── Static frontend ──────────────────────────────────────────────────────
    # Mounted last so it doesn't shadow /api routes. html=True serves
    # index.html for unknown paths (SPA fallback).
    if _FRONTEND_DIST.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend"
        )
    else:
        logger.warning(
            "Frontend bundle not found at %s — run `npm run build` in "
            "src/ui/frontend. API endpoints still work.",
            _FRONTEND_DIST,
        )

        @app.get("/")
        def _no_frontend() -> dict[str, str]:
            return {
                "status": "api-only",
                "message": "Frontend not built. Run `npm run build` in "
                "src/ui/frontend, then restart.",
            }

    return app
