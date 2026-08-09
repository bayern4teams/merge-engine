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

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.llm.cost import COST_LEDGER, SEMANTIC_CACHE
from src.llm.tracing import flush as flush_traces
from src.orchestrator.graph import REVIEW_GATES
from src.orchestrator.profiles import (
    build_profile,
    delete_profile,
    list_profiles,
    load_profile,
    save_profile,
    strip_secrets,
)
from src.orchestrator.workflow_manager import WorkflowManager
from src.ui import connections
from src.ui.components.db_discovery import sample_table_rows, test_and_discover_schemas
from src.utils.config_loader import (
    load_platform_overrides,
    load_raw_config_dict,
    save_platform_overrides,
)

logger = logging.getLogger("merge-engine.api")

_ROOT_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _ROOT_DIR / ".env"


def _init_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]

        if _ENV_FILE.exists():
            load_dotenv(dotenv_path=_ENV_FILE, override=True)
        else:
            load_dotenv(override=True)
    except ImportError:
        pass


_init_env()

# Single long-lived manager shared across requests (mirrors the one the
# Gradio app held). Built lazily so importing this module is cheap.
_manager: WorkflowManager | None = None
_config_path = "config/config.yaml"

# Part 1.6.B: where §0.6's per-run reports land (timings, entity resolution
# stats, reconciliation) — the same file drive_app.py --assert-budgets reads.
_RUN_REPORTS_DIR = Path("logs") / "run_reports"

# compare_to_golden.py only has golden data for these three subjects.
_GOLDEN_SUBJECTS = ("boeing", "airbus", "lockheed")


def _load_run_report(thread_id: str) -> dict[str, Any] | None:
    path = _RUN_REPORTS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


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


class ExploreSampleRequest(BaseModel):
    """Part 1.6.B data explorer: a bounded, read-only row sample."""

    conn_type: str
    host: str
    port: int
    user: str = ""
    password: str = ""
    database: str = ""
    table: str
    schema_name: str = ""
    limit: int = 20


class RunConfig(BaseModel):
    """Part 1.6.A: run-by-run overrides, set in the UI's Advanced panel.

    Applied on top of config.yaml for this run only — takes effect
    immediately, no restart, and never touches the config file. A field left
    ``None`` leaves the file-loaded value untouched (see
    ``graph._apply_run_config_overrides``), so submitting only one override
    can't silently reset the rest to a default.
    """

    model: str | None = None
    fallback_models: list[str] | None = None
    temperature: float | None = None
    entity_resolution_enabled: bool | None = None
    field_mapping_enabled: bool | None = None
    batch_size: int | None = None
    # Gate names (from REVIEW_GATES) to resume through automatically instead
    # of pausing for human review. Empty (the default) pauses at every gate,
    # same as today. Validated against REVIEW_GATES in
    # WorkflowManager.start_workflow — "handle_error" can never be one of
    # these, its resume semantics aren't a plain approve.
    auto_approve_gates: list[str] = Field(default_factory=list)


class WorkflowStart(BaseModel):
    request: str
    # Subject-first mode (Phase 3.3 of the fix-bugs follow-up plan): when
    # set, merge_scope is derived directly from this name instead of asking
    # the LLM to extract a subject from `request`'s free text. `request`
    # still carries some text — parse_request's LLM call still needs it for
    # entity-group naming and explicit-vs-inferred classification, which are
    # independent of scope extraction.
    subject: str | None = None
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
    # Per-entity matching override for this run (O.2 / Part 1.6.A), keyed by
    # entity type with an optional "_default" entry applied when no
    # per-entity type is known yet (target_entities isn't derived until
    # after parse_request runs). Takes effect ahead of a saved profile's
    # pinned matching_rules.
    matching_rules: dict[str, Any] | None = None
    # Part 1.6.A Advanced-panel overrides for this run only.
    run_config: RunConfig | None = None


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
    # Custom cross-table column dependencies added at the deps gate (Phase
    # 2.4) — {child_origin, child_table, child_column, parent_origin,
    # parent_table, parent_column, rationale}, one dict per link.
    column_dependencies: list[dict[str, Any]] | None = None


_DECISIONS = {"approve", "edit", "retry", "reject", "abort", "skip"}


class PlatformDestinationOverride(BaseModel):
    type: str | None = None
    host: str | None = None
    port: int | None = None
    schema_name: str | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class PlatformLLMOverride(BaseModel):
    base_url: str | None = None


class PlatformRetryOverride(BaseModel):
    max_attempts: int | None = None
    backoff: str | None = None
    min_backoff_seconds: float | None = None
    max_backoff_seconds: float | None = None


class PlatformExtractionOverride(BaseModel):
    default_batch_size: int | None = None
    max_batch_size: int | None = None


class EnvOverridesUpdate(BaseModel):
    """Session-only environment variable overrides (not saved to .env)."""

    overrides: dict[str, str]


_SESSION_ENV_OVERRIDES: dict[str, str] = {}


class PlatformConfigUpdate(BaseModel):
    """Platform/system settings (Part 1.6.A Settings page).

    No secret fields exist on this model by design — platform settings are
    ports/hosts/behavioural defaults; credentials stay in .env.
    """

    destination: PlatformDestinationOverride | None = None
    llm: PlatformLLMOverride | None = None
    retry: PlatformRetryOverride | None = None
    extraction: PlatformExtractionOverride | None = None


# ── App ──────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Part 1.5 C.6: flush buffered Langfuse spans on shutdown.

    ``flush()`` was previously called from exactly one place in the whole
    repo — ``tests/prompts/harness.py`` — and never by the app itself. The
    v4 SDK batches spans before exporting them; an abrupt process exit loses
    whatever hadn't been flushed yet, which on a live demo looks like a
    trace that mysteriously stops partway through the graph.
    """
    yield
    flush_traces()


def create_api(config_path: str = "config/config.yaml") -> FastAPI:
    global _config_path
    _config_path = config_path

    app = FastAPI(title="merge-engine", version="1.0.0", lifespan=_lifespan)

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
            "items": [{**it, "label": connections.conn_label(it)} for it in trimmed],
        }

    # ── Config (Part 1.6.A) ─────────────────────────────────────────────────
    # Platform/system settings, read by the Settings page. Run-by-run
    # overrides (Advanced panel) do NOT live here — they ride on
    # /api/workflow/start's body (RunConfig/matching_rules above) and never
    # touch a file, so a value here always describes the platform default a
    # run starts from, not what any particular run used.

    _ENV_EXPECTED = (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "CLICKHOUSE_HOST",
        "CLICKHOUSE_PORT",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_PASSWORD",
    )

    def _mask_env_val(val: str) -> str:
        if not val:
            return ""
        if len(val) <= 8:
            return "****"
        return val[:3] + "..." + val[-4:]

    def _build_env_details() -> dict[str, dict[str, Any]]:
        details: dict[str, dict[str, Any]] = {}
        for name in _ENV_EXPECTED:
            val = os.environ.get(name, "")
            is_override = name in _SESSION_ENV_OVERRIDES
            is_present = bool(val)
            if is_override:
                status = "overridden"
                source = "session_override"
            elif is_present:
                status = "set"
                source = "env"
            else:
                status = "not_set"
                source = "not_set"
            details[name] = {
                "status": status,
                "source": source,
                "masked_value": _mask_env_val(val),
                "is_set": is_present,
            }
        return details

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        """Effective platform config (base + overlay), with per-key provenance.

        Secrets are always stripped — this endpoint is read by a UI, and its
        response is never assumed private.
        """
        base_raw = load_raw_config_dict(_config_path, resolve_env=False)
        effective = load_raw_config_dict(_config_path, resolve_env=True)
        overlay = load_platform_overrides()

        def _provenance(section: str, key: str) -> str:
            if key in (overlay.get(section) or {}):
                return "override"
            raw_val = (base_raw.get(section) or {}).get(key)
            if isinstance(raw_val, str) and raw_val.startswith("${") and raw_val.endswith("}"):
                return "env"
            return "file"

        sources = {
            section: {key: _provenance(section, key) for key in (effective.get(section) or {})}
            for section in effective
            if isinstance(effective.get(section), dict)
        }
        return {
            "effective": strip_secrets(effective),
            "platform_overrides": strip_secrets(overlay),
            "sources": sources,
            "env_present": {name: bool(os.environ.get(name)) for name in _ENV_EXPECTED},
            "env_details": _build_env_details(),
        }

    @app.put("/api/config/platform")
    def update_platform_config(body: PlatformConfigUpdate) -> dict[str, Any]:
        """Persist a platform override, deep-merged into the gitignored overlay.

        Never writes config.yaml (the committed file) — moving this project to
        another machine means editing this overlay, not the tracked default.
        """
        overlay = load_platform_overrides()
        incoming = body.model_dump(exclude_none=True, by_alias=True)
        for section, values in incoming.items():
            overlay[section] = {**overlay.get(section, {}), **values}
        save_platform_overrides(overlay)
        return {"platform_overrides": strip_secrets(overlay)}

    @app.post("/api/config/env")
    def update_env_overrides(body: EnvOverridesUpdate) -> dict[str, Any]:
        """Apply session-only environment overrides.

        Takes effect immediately in runtime memory for all LLM and connection calls.
        Does NOT alter the .env file on disk. When the process restarts (Ctrl+C),
        overrides naturally reset back to .env defaults.
        """
        for k, v in body.overrides.items():
            if v and v.strip():
                clean_v = v.strip()
                _SESSION_ENV_OVERRIDES[k] = clean_v
                os.environ[k] = clean_v
            elif k in _SESSION_ENV_OVERRIDES:
                del _SESSION_ENV_OVERRIDES[k]
        return {
            "env_details": _build_env_details(),
            "env_present": {name: bool(os.environ.get(name)) for name in _ENV_EXPECTED},
        }

    @app.post("/api/config/env/reset")
    def reset_env_overrides() -> dict[str, Any]:
        """Clear all session overrides and reload .env from disk."""
        _SESSION_ENV_OVERRIDES.clear()
        _init_env()
        return {
            "env_details": _build_env_details(),
            "env_present": {name: bool(os.environ.get(name)) for name in _ENV_EXPECTED},
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
        if body.run_config and body.run_config.auto_approve_gates:
            invalid = set(body.run_config.auto_approve_gates) - set(REVIEW_GATES)
            if invalid:
                raise HTTPException(
                    422,
                    f"auto_approve_gates contains unknown gate(s): {sorted(invalid)}. "
                    f"Valid: {REVIEW_GATES}.",
                )
        profile_name = body.profile_name
        if body.save_as_profile:
            # Create it up front so the run has somewhere to pin its
            # decisions when it finishes.
            try:
                save_profile(
                    build_profile(
                        body.save_as_profile,
                        user_request=body.request,
                        origins=body.origins,
                        destination=body.destination,
                    )
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
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
                matching_rules=body.matching_rules,
                run_config=body.run_config.model_dump() if body.run_config else None,
                subject=body.subject,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            logger.error("start_workflow failed: %s", exc, exc_info=True)
            raise HTTPException(500, f"Failed to start workflow: {exc}") from exc
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
            raise HTTPException(422, str(exc)) from exc
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

    # ── Investigation tools (Part 1.6.B) ────────────────────────────────────
    # All four are read-only over data the pipeline already produces or a
    # bounded, explicitly-limited read against a configured database — none
    # can mutate a run or write to any database.

    @app.get("/api/workflow/{thread_id}/matches")
    def workflow_matches(thread_id: str, entity_type: str | None = None) -> dict[str, Any]:
        """B.1 match & cluster inspector: resolution_stats + (optionally)
        one entity type's materialised clusters, straight from the
        checkpoint — works for a completed run, not just an active one."""
        return get_manager().get_match_inspection(thread_id, entity_type)

    @app.get("/api/workflow/{thread_id}/timeline")
    def workflow_timeline(thread_id: str) -> dict[str, Any]:
        """B.2 run trace & cost timeline: per-node wall time from this run's
        own written report (logs/run_reports/<id>.json, §0.6), alongside the
        same cost-ledger data /cost already exposes."""
        report = _load_run_report(thread_id)
        return {
            "found": report is not None,
            "timings": (report or {}).get("timings") or {},
            "entities": (report or {}).get("entities") or {},
            "generated_at": (report or {}).get("generated_at"),
            "cost": {"run": COST_LEDGER.for_run(thread_id), "cache": SEMANTIC_CACHE.stats()},
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
        if body.column_dependencies is not None:
            feedback["column_dependencies"] = body.column_dependencies

        manager.resume_workflow(thread_id, feedback)
        # Return the status right after resuming so the client can react
        # immediately (a mapping-edit validation failure re-interrupts and
        # surfaces the error in interrupt_data.data.error).
        return manager.get_workflow_status(thread_id)

    @app.post("/api/workflow/{thread_id}/abort")
    def workflow_abort(thread_id: str) -> dict[str, Any]:
        get_manager().abort_workflow(thread_id)
        return {"ok": True}

    @app.post("/api/explore/sample")
    def explore_sample(body: ExploreSampleRequest) -> dict[str, Any]:
        """B.3 schema & data explorer: a bounded (<=200-row), read-only
        sample from a configured origin or destination. Never used by the
        merge pipeline itself — investigation only."""
        return sample_table_rows(
            conn_type=body.conn_type,
            host=body.host,
            port=body.port,
            user=body.user,
            password=body.password,
            database=body.database,
            table=body.table,
            schema=body.schema_name,
            limit=body.limit,
        )

    @app.get("/api/runs/diff/golden/{subject}")
    def runs_diff_golden(subject: str) -> dict[str, Any]:
        """B.4 run comparison: wraps compare_to_golden.py (the project's own
        independent evaluator, already used by drive_app.py
        --assert-budgets) so its parity numbers are inspectable in-app
        instead of only as a one-off script run."""
        subject_key = subject.strip().lower()
        if subject_key not in _GOLDEN_SUBJECTS:
            raise HTTPException(
                404, f"No golden data for {subject!r}. Have: {', '.join(_GOLDEN_SUBJECTS)}."
            )
        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "diagnostics" / "compare_to_golden.py"
        )
        try:
            proc = subprocess.run(
                [sys.executable, str(script), subject_key],
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
            )
        except Exception as exc:
            raise HTTPException(500, f"compare_to_golden.py failed to run: {exc}") from exc
        if proc.returncode != 0:
            raise HTTPException(
                500, f"compare_to_golden.py exited {proc.returncode}: {proc.stderr[-2000:]}"
            )
        try:
            diff: Any = json.loads(proc.stdout)
        except Exception as exc:
            raise HTTPException(
                500, f"compare_to_golden.py produced non-JSON output: {exc}"
            ) from exc
        return diff if isinstance(diff, dict) else {"result": diff}

    # ── Static frontend ──────────────────────────────────────────────────────
    # Mounted last so it doesn't shadow /api routes. html=True serves
    # index.html for unknown paths (SPA fallback).
    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
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
