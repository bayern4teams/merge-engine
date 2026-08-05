"""
Workflow manager for merge-engine.

Manages long-running workflows with pause/resume capability via LangGraph
checkpoints. Used by both the Gradio UI and programmatic API.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from src.llm.tracing import flush as flush_traces
from src.llm.tracing import trace_config
from src.orchestrator.graph import (
    GATE_PRODUCER,
    HITL_NODES,
    REVIEW_GATES,
    build_graph,
    get_initial_state,
)
from src.orchestrator.profiles import (
    PINNED_KEYS,
    apply_profile,
    capture_from_state,
    load_profile,
    save_profile,
)
from src.orchestrator.spool import (
    cleanup_old_spools,
    clusters_for,
    collection_count,
    entity_records_by_id,
    store_clusters,
    unified_for,
)
from src.orchestrator.utils.cluster_edits import apply_cluster_decisions

logger = logging.getLogger("merge-engine.workflow_manager")

_CHECKPOINT_DB = Path("data") / "checkpoints.db"


def _create_checkpointer() -> Any:
    """Create a SQLite checkpointer so workflows survive app restarts.

    Built on the sync SqliteSaver (not AsyncSqliteSaver) because each graph
    run uses a fresh event loop in a daemon thread while the UI calls
    get_state/update_state from its own thread — a loop-bound async saver
    would break. The graph itself runs via astream, so the async checkpoint
    methods are bridged to the (lock-serialized, thread-safe) sync ones via
    asyncio.to_thread; this works from any event loop.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        class _LoopSafeSqliteSaver(SqliteSaver):
            async def aget_tuple(self, config: Any) -> Any:
                return await asyncio.to_thread(self.get_tuple, config)

            async def alist(self, config: Any, **kwargs: Any) -> Any:
                items = await asyncio.to_thread(lambda: list(self.list(config, **kwargs)))
                for item in items:
                    yield item

            async def aput(
                self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any
            ) -> Any:
                return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

            async def aput_writes(
                self, config: Any, writes: Any, task_id: str, task_path: str = ""
            ) -> None:
                return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

            async def adelete_thread(self, thread_id: str) -> None:
                return await asyncio.to_thread(self.delete_thread, thread_id)

        _CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CHECKPOINT_DB), check_same_thread=False)
        return _LoopSafeSqliteSaver(conn)
    except Exception as exc:
        logger.warning(
            "SQLite checkpointer unavailable (%s); falling back to in-memory "
            "checkpoints — workflows will NOT survive a restart.",
            exc,
        )
        return None


def _traced_run_config(thread_id: str) -> dict[str, Any]:
    """Build the one traced run config a workflow uses for its whole life.

    Part 1.5 C.3: the single place the Langfuse callback handler + session
    id get bound. Both ``start_workflow`` (a fresh run) and
    ``rehydrate_workflow`` (recovering a run after an app restart) must
    build this identically — before this fix, ``rehydrate_workflow`` built a
    bare ``{"configurable": ...}`` config with no callbacks at all, so every
    resume after a restart ran completely untraced.
    """
    run_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    run_config.update(trace_config(session_id=thread_id, tags=["merge-engine"]))
    return run_config


def _bare_config(config: dict[str, Any]) -> dict[str, Any]:
    """Strip callbacks/metadata, keeping only ``configurable``.

    Part 1.5 C.3: ``get_state``/``update_state`` are checkpoint reads and a
    pseudo-node write, not a traced graph run — passing the traced config's
    ``callbacks`` into ``update_state`` is what made every HITL resume emit
    its own root Langfuse trace instead of continuing the run's one trace
    (LangGraph treats it as a pseudo-node run). Only ``astream`` should ever
    receive the traced config; every other graph call goes through this.
    """
    return {"configurable": dict(config.get("configurable") or {})}


class WorkflowManager:
    """Manages multiple concurrent merge workflows with interrupt support.

    Each workflow runs in a background thread and can be paused at HITL
    checkpoints for user review. State is persisted via LangGraph checkpoints.
    """

    def __init__(self) -> None:
        """Initialize the workflow manager.

        Builds the LangGraph once (with a persistent SQLite checkpointer
        when available) and reuses it for all workflows.
        """
        self.graph = build_graph(checkpointer=_create_checkpointer())
        self._active_workflows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start_workflow(
        self,
        user_request: str,
        config_path: str | None = None,
        thread_id: str | None = None,
        origins: list[dict[str, Any]] | None = None,
        destination: dict[str, Any] | None = None,
        profile_name: str | None = None,
        full_remerge: bool = False,
        conflict_policy: dict[str, Any] | None = None,
        matching_rules: dict[str, Any] | None = None,
        run_config: dict[str, Any] | None = None,
    ) -> str:
        """Start a new merge workflow in a background thread.

        Parameters
        ----------
        user_request : str
            Natural language prompt from the user.
        config_path : str, optional
            Path to config.yaml. Defaults to "config/config.yaml".
        thread_id : str, optional
            Custom LangGraph thread ID. Auto-generated if None.
        origins : list[dict], optional
            Origin database configurations from the UI. Each dict has:
            {id, type, connection: {host, port, user, password, database}, priority}
        destination : dict, optional
            Destination database configuration from the UI:
            {type, host, port, user, password, database, schema}
        conflict_policy : dict, optional
            Per-field survivorship override for this run (Phase 5, O.3) —
            e.g. {"revenue": ["numeric_max"], "_default": [...]}.
        matching_rules : dict, optional
            Per-entity matching overrides for this run (O.2 / Part 1.6.A),
            keyed by entity type with an optional "_default" entry.
        run_config : dict, optional
            Part 1.6.A run-by-run overrides (model, temperature, batch size,
            entity_resolution_enabled, field_mapping_enabled,
            auto_approve_gates) applied on top of config.yaml for this run
            only — no restart, config.yaml untouched.

        Returns
        -------
        str
            thread_id for polling and resuming.
        """
        thread_id = thread_id or f"wf_{uuid.uuid4().hex[:8]}"
        config_path = config_path or "config/config.yaml"

        # Part 1.6.A: only forward gate names the graph actually knows as
        # review gates — an unrecognised or mistyped entry must never
        # silently do nothing while looking like it was accepted, and
        # "handle_error" is deliberately excluded (its resume semantics
        # aren't a plain approve; see resume_workflow's handle_error branch).
        if run_config and run_config.get("auto_approve_gates"):
            requested = set(run_config["auto_approve_gates"])
            invalid = requested - set(REVIEW_GATES)
            if invalid:
                raise ValueError(
                    f"auto_approve_gates contains unknown gate(s): {sorted(invalid)}. "
                    f"Valid: {REVIEW_GATES}."
                )

        # Build initial state with origins and destination from UI
        initial_state = get_initial_state(
            user_request=user_request,
            config_path=config_path,
            origins=origins,
            destination=destination,
            run_id=thread_id,
            conflict_policy=conflict_policy,
            matching_rules=matching_rules,
            run_config=run_config,
        )

        # A profile replays the decisions an earlier run made — which tables
        # hold each entity, how to match, how to resolve conflicts, and how
        # fields map onto destination columns. Without it those are re-derived
        # by the LLM every run, and the mapping in particular is not stable:
        # two identical runs produced destination schemas of 15 and 13
        # columns. Pinning them is what makes a re-run a re-run.
        self._profile_for_run: dict[str, str] = getattr(self, "_profile_for_run", {})
        # A profile that has not yet captured a completed run (no pins) is
        # treated as fresh: it re-extracts in full rather than trusting a
        # watermark from a run that never finished. Combined with the explicit
        # toggle, this is what makes "delete from the destination, re-run under
        # a new profile" actually bring the rows back.
        profile_is_fresh = False
        if profile_name:
            profile = load_profile(profile_name)
            if profile is None:
                raise ValueError(f"No saved profile named {profile_name!r}.")
            apply_profile(initial_state, profile)
            self._profile_for_run[thread_id] = profile_name
            profile_is_fresh = not any(profile.get(k) for k in PINNED_KEYS)
            logger.info(
                "Run %s starts from profile %r (pins: %s)",
                thread_id,
                profile_name,
                ", ".join(k for k in PINNED_KEYS if profile.get(k)) or "none",
            )

        initial_state["force_full_extraction"] = bool(full_remerge or profile_is_fresh)
        if initial_state["force_full_extraction"]:
            logger.info(
                "Run %s will re-extract every table in full (%s).",
                thread_id,
                "explicit full re-merge" if full_remerge else "fresh profile",
            )

        # Langfuse tracing: bind the shared callback handler + a session id
        # (the thread_id) into the graph's run config. Because resume_workflow
        # reuses this same config dict, a run's initial execution and every
        # HITL resume land in one Langfuse session with a span per node. Named
        # distinctly from the `run_config` parameter above (Part 1.6.A's
        # Advanced-panel overrides) -- same word, different concept: this one
        # is LangGraph's own astream() config, not a user override.
        graph_run_config: dict[str, Any] = _traced_run_config(thread_id)
        with self._lock:
            self._active_workflows[thread_id] = {
                "thread_id": thread_id,
                "config": graph_run_config,
                "status": "running",
                "current_interrupt_node": None,
                "interrupt_data": None,
            }
            # Spools are per-run scratch files; without a sweep they build up
            # one per merge until the preflight disk check starts failing.
            # Every known run is kept — including ones parked at a gate,
            # whose rows must still be there when a human comes back.
            known = set(self._active_workflows) | {thread_id}
        try:
            cleanup_old_spools(keep_run_ids=known)
        except Exception as exc:
            logger.warning("Spool cleanup skipped: %s", exc)

        # Start background thread
        thread = threading.Thread(
            target=self._run_workflow,
            args=(thread_id, initial_state, self._active_workflows[thread_id]["config"]),
            daemon=True,
        )
        thread.start()

        logger.info(
            "Started workflow %s for request: %r (origins: %d, dest: %s)",
            thread_id,
            user_request[:50],
            len(origins) if origins else 0,
            destination.get("host", "unknown") if destination else "none",
        )
        return thread_id

    def _run_workflow(
        self,
        thread_id: str,
        initial_state: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        """Run the graph in a background thread, polling for interrupts.

        Uses asyncio to handle async nodes in the graph.
        """

        try:
            # Check if there's already an event loop running
            # If we're in a background thread, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Run the async stream directly
                async def _collect_events():
                    events = []
                    async for event in self.graph.astream(initial_state, config=config):
                        events.append(event)
                    return events

                events = loop.run_until_complete(_collect_events())

                for event in events:
                    # event is a dict like {"node_name": output_dict}
                    for node_name, output in event.items():
                        # Update workflow status
                        with self._lock:
                            wf = self._active_workflows.get(thread_id)
                            if wf:
                                wf["current_interrupt_node"] = node_name

                                # Check if this node is a HITL node (has interrupt_data)
                                if output and output.get("interrupt_data"):
                                    wf["status"] = "interrupted"
                                    wf["current_interrupt_node"] = node_name
                                    wf["interrupt_data"] = output["interrupt_data"]
                                    logger.info(
                                        "Workflow %s interrupted at node '%s'",
                                        thread_id,
                                        node_name,
                                    )
                                elif output and output.get("load_approval") is not None:
                                    if output["load_approval"]:
                                        wf["status"] = "running"  # Resume after approval
                                    else:
                                        wf["status"] = "aborted"
                                        logger.info("Workflow %s aborted by user", thread_id)

                # The stream also ends when the graph pauses at an
                # interrupt_before checkpoint — inspect the checkpoint to
                # distinguish "paused at HITL node" from "actually done".
                self._finalize_stream_status(thread_id)
            finally:
                loop.close()
                # Part 1.5 C.6: flush now, not just at process shutdown — a
                # HITL gate can leave the process running for a long time
                # before the next resume, and a live demo wants this run's
                # trace visible in Langfuse right away, not after the app
                # eventually exits.
                flush_traces()

        except Exception as exc:
            logger.error("Workflow %s failed: %s", thread_id, exc, exc_info=True)
            with self._lock:
                wf = self._active_workflows.get(thread_id)
                if wf:
                    wf["status"] = "failed"
                    wf["error"] = str(exc)

    def _finalize_stream_status(self, thread_id: str) -> None:
        """After a graph stream ends, set interrupted/completed status.

        With interrupt_before, the HITL node has NOT run when the stream
        ends — the pending node is in checkpoint state.next and its
        interrupt payload must be synthesized from the state values.
        """
        with self._lock:
            wf = self._active_workflows.get(thread_id)
        if wf is None:
            return
        config = wf["config"]

        pending: tuple = ()
        values: dict[str, Any] = {}
        try:
            snapshot = self.graph.get_state(_bare_config(config))
            pending = tuple(getattr(snapshot, "next", ()) or ())
            values = dict(snapshot.values or {}) if snapshot else {}
        except Exception as exc:
            logger.warning("Could not read checkpoint state for %s: %s", thread_id, exc)

        capture_profile: str | None = None
        auto_approve_node: str | None = None
        with self._lock:
            wf = self._active_workflows.get(thread_id)
            if wf is None or wf["status"] == "aborted":
                return
            if pending:
                node = pending[0]
                wf["status"] = "interrupted"
                wf["current_interrupt_node"] = node
                wf["interrupt_data"] = self._build_interrupt_data(node, values)
                visited = wf.setdefault("visited_gates", [])
                if node in HITL_NODES and node not in visited:
                    visited.append(node)
                wf["skipped_gates"] = self._derive_skipped_gates(node, values, visited)
                logger.info("Workflow %s interrupted at node '%s'", thread_id, node)
                # Part 1.6.A: a gate the user opted into auto-approving (the
                # Advanced panel's per-gate toggles) resumes immediately
                # instead of waiting for a human. "handle_error" can never
                # appear here — REVIEW_GATES excludes it, and start_workflow
                # already rejects it in auto_approve_gates before the run
                # begins. Deferred until outside this lock (resume_workflow
                # takes it itself).
                if node in REVIEW_GATES and node in (values.get("auto_approve_gates") or []):
                    auto_approve_node = node
            elif (values.get("reconciliation_report") or {}).get("load_rejected"):
                wf["status"] = "rejected"
                logger.info("Workflow %s ended: load rejected by user", thread_id)
            elif (values.get("reconciliation_report") or {}).get("load_failed_entities"):
                # A run that loaded nothing must never show a green
                # "completed" — that is how an empty destination gets
                # mistaken for a successful merge.
                failed = values["reconciliation_report"]["load_failed_entities"]
                wf["status"] = "failed"
                wf["error"] = (
                    f"Load failed for {len(failed)} entity type(s): "
                    f"{', '.join(failed)}. Nothing was committed for them; "
                    f"watermarks were not advanced, so a re-run will retry."
                )
                logger.error("Workflow %s: %s", thread_id, wf["error"])
            elif wf.get("failed_at_gate") or values.get("failure"):
                # Either the user abandoned the run at the error gate, or a
                # node recorded a failure the graph never got to escalate.
                failure = values.get("failure") or {}
                handled = values.get("handled_failures") or []
                detail = failure.get("error") or (handled[-1].get("error") if handled else "")
                wf["status"] = "failed"
                wf["error"] = (
                    f"Run stopped after a failure in "
                    f"'{failure.get('node') or (handled[-1].get('node') if handled else '?')}'. "
                    f"{str(detail)[:400]}"
                )
                logger.error("Workflow %s: %s", thread_id, wf["error"])
            elif wf.get("rejected_at_gate"):
                wf["status"] = "rejected"
                logger.info(
                    "Workflow %s ended: rejected at gate '%s'",
                    thread_id,
                    wf["rejected_at_gate"],
                )
            elif not values.get("target_entities") and values.get("errors"):
                # parse_request failed (unparseable request, or the LLM was
                # unreachable / out of quota) and every later stage then had
                # nothing to do. Reporting "completed" here is exactly how an
                # untouched destination gets mistaken for a clean merge.
                first_error = str(values["errors"][0])[:300]
                wf["status"] = "failed"
                wf["error"] = (
                    "No entities could be determined from the request, so "
                    f"nothing was merged. First error: {first_error}"
                )
                logger.error("Workflow %s: %s", thread_id, wf["error"])
            else:
                wf["status"] = "completed"
                skipped = [
                    h.get("node", "?")
                    for h in (values.get("handled_failures") or [])
                    if h.get("decision") == "skip"
                ]
                if skipped:
                    # The user chose to continue past a broken step, so this
                    # is a real completion — but an unqualified green tick
                    # would misrepresent what actually landed.
                    wf["warning"] = (
                        "Completed, but "
                        f"{len(skipped)} step(s) were skipped after failing: "
                        f"{', '.join(skipped)}. The merged output is "
                        "incomplete by that much."
                    )
                    logger.warning("Workflow %s: %s", thread_id, wf["warning"])
                else:
                    logger.info("Workflow %s completed successfully", thread_id)
                capture_profile = (
                    self._profile_for_run.get(thread_id)
                    if hasattr(self, "_profile_for_run")
                    else None
                )

        # Outside the lock: writing the profile touches the filesystem, and a
        # slow disk must not hold up every other status read.
        if wf["status"] == "completed" and capture_profile:
            self._capture_profile(capture_profile, values, thread_id)

        # Also outside the lock: resume_workflow takes it itself, and spawns
        # its own background thread (_continue_workflow), which calls back
        # into this same method when its stream ends — so a run with several
        # auto-approved gates in a row cascades through them one thread hop
        # at a time instead of recursing on this call stack.
        if auto_approve_node is not None:
            logger.warning(
                "Workflow %s: auto-approving gate '%s' per this run's "
                "Advanced-panel setting — no human reviewed this checkpoint.",
                thread_id,
                auto_approve_node,
            )
            self.resume_workflow(
                thread_id, {"approved": True, "decision": "approve", "node": auto_approve_node}
            )

    def _capture_profile(self, name: str, values: dict[str, Any], thread_id: str) -> None:
        """Write the decisions this run actually made back to its profile.

        Only on a clean completion. Harvesting a failed or partially skipped
        run would pin whatever half-formed mapping it reached, and the next
        run would inherit it silently.
        """
        try:
            profile = load_profile(name)
            if profile is None:
                return
            save_profile(capture_from_state(profile, values))
            logger.info("Run %s pinned its decisions into profile %r", thread_id, name)
        except Exception as exc:
            # A profile is a convenience; failing to update it must never
            # turn a successful merge into a reported failure.
            logger.warning("Could not update profile %r: %s", name, exc)

    @staticmethod
    def _mapping_rows_from_values(values: dict[str, Any]) -> list[dict[str, Any]]:
        """Build the COMPLETE per-field mapping table from state values.

        Emits one row for every field in field_mappings (auto-mapped fields
        included — the reviewer must see everything, not just the subset
        needing attention) plus one skip-row per unmapped field.
        """
        schema_changes = values.get("pending_schema_changes", []) or []
        mapping_rows: list[dict[str, Any]] = []
        for entity_type, mapping in (values.get("field_mappings") or {}).items():
            for src_field, info in (mapping or {}).items():
                if info.get("requires_schema_change"):
                    col = info.get("dest_column", src_field)
                    col_type = next(
                        (
                            c.get("data_type", "TEXT")
                            for c in schema_changes
                            if c.get("column") == col
                        ),
                        "TEXT",
                    )
                    mapping_rows.append(
                        {
                            "entity_type": entity_type,
                            "source_field": src_field,
                            "action": "new_column",
                            "dest_column": "",
                            "new_column_name": col,
                            "new_column_type": col_type,
                            "confidence": info.get("confidence", 0.0),
                        }
                    )
                else:
                    mapping_rows.append(
                        {
                            "entity_type": entity_type,
                            "source_field": src_field,
                            "action": "map",
                            "dest_column": info.get("dest_column", ""),
                            "new_column_name": "",
                            "new_column_type": "",
                            "confidence": info.get("confidence", 0.0),
                        }
                    )
        for entity_type, fields in (values.get("unmapped_fields") or {}).items():
            for item in fields or []:
                mapping_rows.append(
                    {
                        "entity_type": entity_type,
                        "source_field": item.get("field", ""),
                        "action": "skip",
                        "dest_column": item.get("suggested_column") or "",
                        "new_column_name": "",
                        "new_column_type": "",
                        "confidence": item.get("confidence", 0.0),
                    }
                )
        return mapping_rows

    @staticmethod
    def _auto_match_stats(values: dict[str, Any]) -> dict[str, int]:
        """Aggregate per-entity resolution stats into pipeline-wide counts."""
        stats = values.get("resolution_stats") or {}
        return {
            "auto_matched_pairs": sum(s.get("auto_matched", 0) for s in stats.values()),
            "auto_excluded_pairs": sum(s.get("auto_excluded", 0) for s in stats.values()),
            "llm_scored_pairs": sum(s.get("llm_scored", 0) for s in stats.values()),
        }

    @staticmethod
    def _derive_skipped_gates(
        current_node: str,
        values: dict[str, Any],
        visited_gates: list[str] | None = None,
    ) -> dict[str, str]:
        """Which HITL gates before current_node were auto-skipped, and why.

        Re-applies the graph's own routing predicates over checkpoint values;
        a gate the graph routed around was never scheduled, so it cannot
        appear in visited_gates. visited_gates (when tracked) wins over the
        predicate re-check, covering edge cases where a review mutated the
        very state the predicate reads (e.g. cleared pending_schema_changes).
        """
        if current_node not in HITL_NODES:
            return {}
        visited = set(visited_gates or [])
        skipped: dict[str, str] = {}
        current_idx = HITL_NODES.index(current_node)

        if (
            HITL_NODES.index("human_review_scope") < current_idx
            and "human_review_scope" not in visited
            and not (values.get("merge_scope") or {}).get("active")
        ):
            skipped["human_review_scope"] = (
                "This merge targets no single organisation, so there was no scope to confirm"
            )

        if (
            HITL_NODES.index("human_review_matches") < current_idx
            and "human_review_matches" not in visited
        ):
            # Clusters live in the spool, so ask the handles rather than
            # iterating the container — iterating a handle yields its keys,
            # not clusters.
            containers = values.get("entity_clusters") or {}
            clusters_exist = any(collection_count(containers, e) for e in containers)
            needs_review = any(
                len(c.get("record_ids", [])) > 1 and c.get("required_human_review", False)
                for e in containers
                for c in clusters_for(values, e)
            )
            if clusters_exist and not needs_review:
                s = WorkflowManager._auto_match_stats(values)
                skipped["human_review_matches"] = (
                    f"All matches scored ≥85% confidence — "
                    f"{s['auto_matched_pairs']} pair(s) auto-matched, "
                    f"{s['auto_excluded_pairs']} auto-excluded; nothing was ambiguous"
                )

        if (
            HITL_NODES.index("human_review_mappings") < current_idx
            and "human_review_mappings" not in visited
        ):
            has_changes = bool(values.get("pending_schema_changes"))
            has_unmapped = any((values.get("unmapped_fields") or {}).values())
            if not has_changes and not has_unmapped:
                n_fields = sum(len(m or {}) for m in (values.get("field_mappings") or {}).values())
                skipped["human_review_mappings"] = (
                    f"All {n_fields} source field(s) mapped automatically — "
                    f"no new columns or unmapped fields needed a decision"
                )

        return skipped

    @staticmethod
    def _build_interrupt_data(node: str, values: dict[str, Any]) -> dict[str, Any]:
        """Synthesize the interrupt payload a HITL node would produce.

        Mirrors the interrupt_data structures in nodes/human_nodes.py, since
        with interrupt_before the node body never executes before the pause.
        """
        if node == "handle_error":
            from src.orchestrator.nodes.human_nodes import build_error_interrupt

            return build_error_interrupt(values)
        if node == "human_review_deps":
            data: dict[str, Any] = {
                "dependency_tree": values.get("dependency_tree", {}),
                "extraction_order": values.get("extraction_order", []),
                # Reviewed BEFORE extraction: a source table missing from this
                # list contributes nothing to the merge, and that used to be
                # invisible until the destination came up short.
                "entity_tables": values.get("entity_tables", {}) or {},
                "table_selection_notes": values.get("table_selection_notes", ""),
            }
            warning = values.get("dependency_analysis_warning", "")
            if warning:
                data["warning"] = warning
            return {
                "node": node,
                "title": "Review Dependency Tree",
                "data": data,
            }
        if node == "human_review_scope":
            from src.orchestrator.nodes.human_nodes import _build_scope_interrupt

            return _build_scope_interrupt(values.get("scope_plan", {}) or {})
        if node == "human_review_matches":
            low_conf: list[dict[str, Any]] = []
            for entity_type in values.get("entity_clusters") or {}:
                clusters = clusters_for(values, entity_type)
                # Fetch only the records the gate actually shows. Building a
                # lookup of every extracted row first is what made opening
                # this gate cost as much as the extraction itself.
                needed = [
                    rid
                    for cluster in clusters
                    if cluster.get("required_human_review", False)
                    for rid in cluster.get("record_ids", [])
                ]
                records_by_id = entity_records_by_id(values, entity_type, needed)
                for cluster in clusters:
                    if cluster.get("required_human_review", False):
                        record_ids = cluster.get("record_ids", [])
                        # Surface the actual field values (not just the opaque
                        # internal record key) so a human reviewer can tell
                        # WHY two records look alike instead of guessing from
                        # an id like "origin_1:companies:42".
                        records_preview = []
                        for rid in record_ids:
                            rec = records_by_id.get(rid, {})
                            fields = {
                                k: v
                                for k, v in rec.items()
                                if not k.startswith("_") and v not in (None, "")
                            }
                            records_preview.append({"record_id": rid, "fields": fields})
                        low_conf.append(
                            {
                                "entity_type": entity_type,
                                "cluster_id": cluster.get("cluster_id", ""),
                                "record_ids": record_ids,
                                "records": records_preview,
                                "confidence": cluster.get("match_confidence", 0),
                            }
                        )
            # Pairs scored in the uncertain band and therefore NOT merged.
            # They form no cluster, so they would otherwise be invisible —
            # the reviewer would never learn the system had hesitated.
            pending: list[dict[str, Any]] = []
            for entity_type, stats in (values.get("resolution_stats") or {}).items():
                candidates = stats.get("pending_merge_candidates") or []
                records_by_id = entity_records_by_id(
                    values,
                    entity_type,
                    [rid for c in candidates for rid in c.get("record_ids", [])],
                )
                for candidate in candidates:
                    preview = []
                    for rid in candidate.get("record_ids", []):
                        rec = records_by_id.get(rid, {})
                        preview.append(
                            {
                                "record_id": rid,
                                "fields": {
                                    k: v
                                    for k, v in rec.items()
                                    if not k.startswith("_") and v not in (None, "")
                                },
                            }
                        )
                    pending.append(
                        {
                            "entity_type": entity_type,
                            "record_ids": candidate.get("record_ids", []),
                            "records": preview,
                            "confidence": candidate.get("confidence", 0),
                        }
                    )

            return {
                "node": node,
                "title": "Review Entity Matches",
                "data": {
                    "low_confidence_clusters": low_conf,
                    "total_clustering_decisions": len(low_conf),
                    "auto_match_stats": WorkflowManager._auto_match_stats(values),
                    "pending_merge_candidates": pending,
                    "pending_merge_note": (
                        f"{len(pending)} pair(s) looked similar but not similar "
                        "enough to merge automatically. They were left as "
                        "separate records — approve to keep them separate."
                    )
                    if pending
                    else "",
                },
            }
        if node == "human_review_mappings":
            schema_changes = values.get("pending_schema_changes", []) or []
            mapping_rows = WorkflowManager._mapping_rows_from_values(values)
            unmapped: list[dict[str, Any]] = [
                {"entity_type": r["entity_type"], "source_field": r["source_field"]}
                for r in mapping_rows
                if r["action"] == "skip"
            ]
            return {
                "node": node,
                "title": "Review Field Mappings",
                "data": {
                    "mapping_rows": mapping_rows,
                    "unmapped_fields": unmapped,
                    "pending_schema_changes": schema_changes,
                },
            }
        if node == "human_final_approval":
            unified = values.get("unified_entities") or {}
            pending_changes = values.get("pending_schema_changes", [])
            errors = values.get("errors", [])
            summary: dict[str, Any] = {
                "total_entities": len(unified),
                # Counts come from the handle; conflicts need the records,
                # so they are read one entity at a time rather than all at once.
                "entity_counts": {k: collection_count(unified, k) for k in unified},
                "total_conflicts": sum(
                    len(rec.get("_conflicts", []))
                    for k in unified
                    for rec in unified_for(values, k)
                    if isinstance(rec.get("_conflicts", []), list)
                ),
                "schema_changes": len(pending_changes),
                "errors": len(errors),
            }
            # Pre-load reconciliation preview: the user approves NUMBERS
            # (extraction/cluster/conflict counts), not vibes. Load results
            # are zero here — the load hasn't run yet.
            from src.orchestrator.nodes.load_data import build_reconciliation_report

            reconciliation = build_reconciliation_report(values, {}, list(errors))
            return {
                "node": node,
                "title": "Final Approval",
                "data": {
                    "summary": summary,
                    "pending_schema_changes": pending_changes,
                    "reconciliation": reconciliation,
                    # The matches/mappings gates can legitimately auto-skip,
                    # so the final gate must show the full picture: every
                    # field mapping and the auto-decision counts.
                    "field_mappings_rows": WorkflowManager._mapping_rows_from_values(values),
                    "auto_match_stats": WorkflowManager._auto_match_stats(values),
                },
            }
        return {"node": node, "title": node, "data": {}}

    def get_workflow_status(self, thread_id: str) -> dict[str, Any]:
        """Get current workflow status and any pending interrupt data.

        Parameters
        ----------
        thread_id : str
            Workflow thread ID.

        Returns
        -------
        dict
            {status, interrupt_data, current_interrupt_node, summary}
        """
        with self._lock:
            wf = self._active_workflows.get(thread_id)

        if wf is None:
            # Not in memory — the app may have restarted since the workflow
            # started. Rehydrate from the persistent checkpoint if one exists.
            wf = self.rehydrate_workflow(thread_id)
            if wf is None:
                return {"status": "not_found"}

        with self._lock:
            result = {
                "status": wf["status"],
                "interrupt_data": wf.get("interrupt_data"),
                "current_interrupt_node": wf.get("current_interrupt_node"),
                "skipped_gates": wf.get("skipped_gates", {}),
            }
            # Why a run failed, and the caveat on a run that completed only
            # because the user skipped a broken step. Both are omitted when
            # empty so a clean run's payload is unchanged.
            if wf.get("error"):
                result["error"] = wf["error"]
            if wf.get("warning"):
                result["warning"] = wf["warning"]

            # Add summary if running/completed
            if wf["status"] in ("completed", "failed"):
                result["summary"] = self._get_workflow_summary(thread_id)

            return result

    def rehydrate_workflow(self, thread_id: str) -> dict[str, Any] | None:
        """Rebuild an in-memory workflow entry from a persisted checkpoint.

        After an app restart, _active_workflows is empty but SQLite still
        holds the graph checkpoint. Derive status from snapshot.next:
        pending HITL node → interrupted; empty next with values → completed.
        Registers the entry so subsequent resume/abort calls work normally.
        """
        # Part 1.5 C.3: build the SAME traced config start_workflow does —
        # this used to be a bare {"configurable": ...} dict with no
        # callbacks at all, so every resume after an app restart ran
        # completely untraced.
        config = _traced_run_config(thread_id)
        try:
            snapshot = self.graph.get_state(_bare_config(config))
        except Exception as exc:
            logger.warning("Rehydrate failed for %s: %s", thread_id, exc)
            return None
        if snapshot is None or not (snapshot.values or getattr(snapshot, "next", None)):
            return None

        pending = tuple(getattr(snapshot, "next", ()) or ())
        values = dict(snapshot.values or {})
        wf: dict[str, Any] = {
            "thread_id": thread_id,
            "config": config,
            "status": "running",
            "current_interrupt_node": None,
            "interrupt_data": None,
        }
        if pending:
            node = pending[0]
            wf["current_interrupt_node"] = node
            if node in HITL_NODES:
                wf["status"] = "interrupted"
                wf["interrupt_data"] = self._build_interrupt_data(node, values)
                # visited_gates is lost on restart; predicate re-check alone
                # still identifies gates the graph routed around.
                wf["skipped_gates"] = self._derive_skipped_gates(node, values, [node])
            # Non-HITL pending node after a restart means the run died
            # mid-node; surface as failed so the user can restart it.
            else:
                wf["status"] = "failed"
                wf["error"] = f"App restarted while node '{node}' was running"
        else:
            wf["status"] = "completed"

        with self._lock:
            self._active_workflows.setdefault(thread_id, wf)
            wf = self._active_workflows[thread_id]
        logger.info(
            "Rehydrated workflow %s from checkpoint (status=%s, node=%s)",
            thread_id,
            wf["status"],
            wf.get("current_interrupt_node"),
        )
        return wf

    def _get_workflow_summary(self, thread_id: str) -> dict[str, Any]:
        """Get a summary of the completed workflow state."""
        config = self._active_workflows.get(thread_id, {}).get("config", {})
        if not config:
            return {}

        # Get the latest state from the graph
        try:
            latest = self.graph.get_state(_bare_config(config))
            if latest and latest.values:
                values = latest.values
                return {
                    "target_entities": values.get("target_entities", []),
                    "entity_counts": {
                        k: collection_count(values.get("unified_entities") or {}, k)
                        for k in (values.get("unified_entities") or {})
                    },
                    "errors": values.get("errors", []),
                    "reconciliation_report": values.get("reconciliation_report", {}),
                }
        except Exception:
            pass

        return {}

    def _safe_state_values(self, config: dict[str, Any]) -> dict[str, Any]:
        """Read checkpoint state values, returning {} on any failure.

        Strips tracing from ``config`` itself (Part 1.5 C.3) so every one of
        this method's many callers gets the "no callbacks on a state read"
        rule for free, rather than each having to remember it.
        """
        try:
            snapshot = self.graph.get_state(_bare_config(config))
            return dict(snapshot.values or {}) if snapshot else {}
        except Exception:
            return {}

    def get_match_inspection(
        self, thread_id: str, entity_type: str | None = None
    ) -> dict[str, Any]:
        """Part 1.6.B: read-only view into resolve_entities' own output.

        Works for ANY thread the checkpointer still knows about — active,
        interrupted, or long completed — the same "read straight from the
        checkpoint" approach ``rehydrate_workflow``/``resume_workflow``
        already rely on, not a new data path. ``resolution_stats`` (blocking/
        scoring counts per entity, O.6/Part-1.5-C.4's own numbers) is always
        returned; materialised cluster membership is only fetched when
        ``entity_type`` is given, since a large entity's full cluster list
        can be a lot of data to build for a listing call that doesn't need it.
        """
        values = self._safe_state_values({"configurable": {"thread_id": thread_id}})
        if not values:
            return {
                "found": False,
                "entity_types": [],
                "resolution_stats": {},
                "requested_entity_type": entity_type,
                "clusters": [],
            }
        resolution_stats: dict[str, Any] = values.get("resolution_stats") or {}
        entity_types = sorted(resolution_stats) or sorted(
            (values.get("entity_clusters") or {}).keys()
        )
        clusters: list[dict[str, Any]] = []
        if entity_type:
            try:
                clusters = clusters_for(values, entity_type)
            except Exception as exc:
                logger.warning(
                    "Could not materialise clusters for %s/%s: %s", thread_id, entity_type, exc
                )
        return {
            "found": True,
            "entity_types": entity_types,
            "resolution_stats": resolution_stats,
            "requested_entity_type": entity_type,
            "clusters": clusters,
        }

    def apply_mapping_feedback(
        self,
        config: dict[str, Any],
        edits: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any] | str]:
        """Rebuild mapping state from human-edited mapping rows.

        Each edit row: {entity_type, source_field, action, dest_column,
        new_column_name, new_column_type}. Only ``map`` and ``skip`` are
        accepted — the destination schema is a fixed contract, so a reviewer
        cannot add a column here any more than a node can. Validates dest
        columns against the live destination schema.

        Returns
        -------
        (True, state_update) on success; (False, error_message) if any
        edit is invalid — the gate should stay interrupted.
        """
        from src.orchestrator.utils.entity_aliases import resolve_dest_table

        values = self._safe_state_values(config)
        destination = values.get("destination")

        # Live destination schema for validation (best-effort: if the
        # destination is unreachable, validate names/identifiers only).
        dest_columns_by_entity: dict[str, set[str]] = {}
        dest_table_by_entity: dict[str, str] = {}
        try:
            from src.mcp_server.tools.load_data import _get_destination_connector

            dest_connector = _get_destination_connector(
                destination if isinstance(destination, dict) else None
            )
            available_tables = dest_connector.list_tables()
        except Exception as exc:
            logger.warning("Mapping validation: destination unreachable: %s", exc)
            dest_connector = None
            available_tables = []

        entity_types = {str(row.get("entity_type", "")) for row in edits}
        for entity_type in entity_types:
            dest_table = entity_type
            if available_tables:
                resolved = resolve_dest_table(entity_type, available_tables)
                if resolved:
                    dest_table = resolved
            dest_table_by_entity[entity_type] = dest_table
            cols: set[str] = set()
            if dest_connector is not None:
                try:
                    schema = dest_connector.get_table_schema(dest_table)
                    cols = {c["name"] for c in schema.get("columns", [])}
                except Exception as exc:
                    logger.warning(
                        "Mapping validation: could not read schema for '%s': %s",
                        dest_table,
                        exc,
                    )
            dest_columns_by_entity[entity_type] = cols

        errors: list[str] = []
        field_mappings: dict[str, dict[str, Any]] = {et: {} for et in entity_types}
        pending_schema_changes: list[dict[str, Any]] = []

        for row in edits:
            entity_type = str(row.get("entity_type", "")).strip()
            src_field = str(row.get("source_field", "")).strip()
            action = str(row.get("action", "")).strip().lower()
            if not entity_type or not src_field:
                continue

            if action == "map":
                dest_col = str(row.get("dest_column", "")).strip()
                known = dest_columns_by_entity.get(entity_type, set())
                if not dest_col:
                    errors.append(f"{entity_type}.{src_field}: action=map requires dest_column")
                    continue
                if known and dest_col not in known:
                    errors.append(
                        f"{entity_type}.{src_field}: dest_column '{dest_col}' does not exist "
                        f"in table '{dest_table_by_entity.get(entity_type)}'"
                    )
                    continue
                field_mappings[entity_type][src_field] = {
                    "dest_column": dest_col,
                    "confidence": 1.0,
                    "mapped_by": "human",
                }
            elif action == "new_column":
                errors.append(
                    f"{entity_type}.{src_field}: the destination schema is a fixed "
                    f"contract — columns cannot be added. Map this field onto an "
                    f"existing column in '{dest_table_by_entity.get(entity_type)}', "
                    f"or skip it."
                )
            elif action == "skip":
                continue
            else:
                errors.append(f"{entity_type}.{src_field}: unknown action '{action}'")

        if errors:
            return False, "; ".join(errors)

        # Preserve mappings for entities the edit table didn't cover.
        merged_mappings = dict(values.get("field_mappings") or {})
        merged_mappings.update(field_mappings)
        merged_changes = [
            c
            for c in (values.get("pending_schema_changes") or [])
            if c.get("table") not in {dest_table_by_entity.get(et) for et in entity_types}
        ]
        merged_changes.extend(pending_schema_changes)
        unmapped = dict(values.get("unmapped_fields") or {})
        for et in entity_types:
            unmapped[et] = []

        return True, {
            "field_mappings": merged_mappings,
            "pending_schema_changes": merged_changes,
            "unmapped_fields": unmapped,
        }

    @staticmethod
    def _sanitise_entity_tables(
        edited: Any, values: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        """Keep only entity->table picks that exist in the discovered schema.

        The user can add tables the LLM missed, so this can't just intersect
        with the previous selection — but it must not let a typo become a
        silently-skipped table at extraction time either.
        """
        schemas = values.get("schemas", {}) or {}
        known: dict[str, set[str]] = {}
        for origin_id, schema_data in schemas.items():
            names: set[str] = set()
            for table in schema_data.get("tables", []):
                name = table.get("name")
                full = table.get("full_name") or name
                names.update(n for n in (name, full) if n)
            known[origin_id] = names

        out: dict[str, list[dict[str, Any]]] = {}
        for entity, items in (edited or {}).items():
            kept: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                origin_id = str(item.get("origin_id") or "")
                table = str(item.get("table") or "")
                if not table or table not in known.get(origin_id, set()):
                    logger.warning(
                        "Ignoring unknown table in edited selection: %s:%s",
                        origin_id,
                        table,
                    )
                    continue
                if (origin_id, table) in seen:
                    continue
                seen.add((origin_id, table))
                kept.append(
                    {
                        "origin_id": origin_id,
                        "table": table,
                        "confidence": item.get("confidence", 1.0),
                        "reason": str(item.get("reason", ""))[:200],
                        "source": "user",
                    }
                )
            out[str(entity)] = kept
        return out

    def resume_workflow(
        self,
        thread_id: str,
        feedback: dict[str, Any],
    ) -> None:
        """Resume a paused workflow with user feedback.

        Parameters
        ----------
        thread_id : str
            Workflow thread ID.
        feedback : dict
            User's decisions from the HITL interrupt form.
        """
        with self._lock:
            wf = self._active_workflows.get(thread_id)
            if wf is None:
                logger.error("Cannot resume unknown workflow: %s", thread_id)
                return

            interrupt_node = wf.get("current_interrupt_node")
            if not interrupt_node:
                logger.warning("No interrupt to resume for workflow %s", thread_id)
                return

        config = wf["config"]

        # HITL node bodies never execute on resume (interrupt_before), so
        # mapping edits must be merged into state here. Validate BEFORE
        # marking the workflow running — invalid edits keep the gate open.
        mapping_update: dict[str, Any] = {}
        if (
            interrupt_node == "human_review_mappings"
            and feedback.get("approved")
            and feedback.get("mapping_edits") is not None
        ):
            ok, result = self.apply_mapping_feedback(config, feedback["mapping_edits"])
            if not ok:
                logger.warning("Mapping edits rejected: %s", result)
                with self._lock:
                    wf = self._active_workflows.get(thread_id)
                    if wf:
                        wf["status"] = "interrupted"
                        interrupt_data = wf.get("interrupt_data") or self._build_interrupt_data(
                            interrupt_node, self._safe_state_values(config)
                        )
                        interrupt_data.setdefault("data", {})["error"] = str(result)
                        wf["interrupt_data"] = interrupt_data
                return
            mapping_update = result

        # Normalise the decision verb once. Older clients send only
        # {"approved": bool}; the gate routers understand both.
        decision = str(feedback.get("decision") or "").strip().lower()
        if not decision:
            decision = "approve" if feedback.get("approved") else "reject"
            feedback = {**feedback, "decision": decision}

        with self._lock:
            wf = self._active_workflows.get(thread_id)
            if wf is None:
                return
            wf["status"] = "running"
            wf["interrupt_data"] = None
            if interrupt_node == "handle_error":
                # Abandoning a run because a step broke is a failure, not a
                # user's considered rejection of good output — reporting it
                # as "rejected" would hide a real fault.
                if decision not in ("retry", "skip"):
                    wf["failed_at_gate"] = interrupt_node
                elif decision == "skip":
                    wf.setdefault("skipped_failures", []).append(
                        (self._safe_state_values(config).get("failure") or {}).get(
                            "node", "unknown"
                        )
                    )
            elif decision in ("reject", "abort"):
                wf["rejected_at_gate"] = interrupt_node
                wf["reject_reason"] = str(feedback.get("reason") or "")

        try:
            # Record the feedback as if the skipped HITL node produced it.
            state_update: dict[str, Any] = {"human_feedback": feedback}
            state_update.update(mapping_update)
            if interrupt_node == "human_final_approval":
                state_update["load_approval"] = decision in ("approve", "edit")

            # Same reason as the cluster decisions below: the deps gate's node
            # body never runs, so an edited table selection is merged here.
            if interrupt_node == "human_review_deps" and feedback.get("entity_tables") is not None:
                edited = self._sanitise_entity_tables(
                    feedback["entity_tables"], self._safe_state_values(config)
                )
                state_update["entity_tables"] = edited
                logger.info(
                    "Workflow %s: table selection edited by user -> %s",
                    thread_id,
                    {e: len(v) for e, v in edited.items()},
                )

            # The scope gate's body never runs on resume either, so an edited
            # scope plan (e.g. after narrowing to one specific "Nova") is
            # merged into state here or the confirmation would change nothing.
            if interrupt_node == "human_review_scope" and feedback.get("scope_plan") is not None:
                state_update["scope_plan"] = feedback["scope_plan"]
                logger.info("Workflow %s: merge scope confirmed/edited by user", thread_id)

            # HITL node bodies never run on resume (interrupt_before), so the
            # matches gate's split/keep decisions have to be applied to state
            # here — otherwise the review changes nothing.
            if interrupt_node == "human_review_matches" and feedback.get("cluster_decisions"):
                live_values = self._safe_state_values(config)
                run_id = str(live_values.get("run_id") or thread_id)
                # Clusters live in the spool; materialise, apply the splits,
                # then store the result back as handles.
                materialised = {
                    e: clusters_for(live_values, e)
                    for e in (live_values.get("entity_clusters") or {})
                }
                rebuilt, stats = apply_cluster_decisions(
                    materialised,
                    feedback["cluster_decisions"],
                )
                state_update["entity_clusters"] = {
                    e: store_clusters(
                        run_id,
                        e,
                        cs,
                        needs_review=sum(1 for c in cs if c.get("required_human_review")),
                        multi_record=sum(1 for c in cs if len(c.get("record_ids", [])) > 1),
                    )
                    for e, cs in rebuilt.items()
                }
                logger.info("Workflow %s: cluster decisions applied (%s)", thread_id, stats)

            # The error gate's bookkeeping. It has to happen here, not in the
            # node body: update_state(as_node=...) tells LangGraph the node
            # already ran, so handle_error's body never executes and the
            # outbound router would find no decision to act on.
            if interrupt_node == "handle_error":
                live = self._safe_state_values(config)
                open_failure = dict(live.get("failure") or {})
                failed_node = open_failure.get("node") or ""
                # Cleared so the failing node's own router lets it through on
                # a retry instead of immediately re-catching the same failure.
                state_update["failure"] = {}
                state_update["handled_failures"] = [{**open_failure, "decision": decision}]
                if decision == "retry" and failed_node:
                    counts = dict(live.get("retry_counts") or {})
                    counts[failed_node] = counts.get(failed_node, 0) + 1
                    state_update["retry_counts"] = counts
                logger.warning(
                    "Error gate: user chose '%s' for the failure in '%s'.",
                    decision,
                    failed_node or "?",
                )

            elif decision == "retry":
                # Count the attempt (the router's loop guard reads this) and
                # hand the user's reason to the node about to be re-run.
                live = self._safe_state_values(config)
                counts = dict(live.get("gate_retry_counts") or {})
                counts[interrupt_node] = counts.get(interrupt_node, 0) + 1
                state_update["gate_retry_counts"] = counts

                producer = GATE_PRODUCER.get(interrupt_node)
                reason = str(feedback.get("reason") or "").strip()
                if producer and reason:
                    hints = dict(live.get("retry_hints") or {})
                    hints[producer] = reason
                    state_update["retry_hints"] = hints

            # Part 1.5 C.3: bare config, not the traced one. update_state is a
            # pseudo-node write, not a graph run — LangGraph was treating a
            # traced update_state as its own root trace, fragmenting one
            # run's HITL resumes into several unrelated Langfuse traces.
            self.graph.update_state(_bare_config(config), state_update, as_node=interrupt_node)
            logger.info("Resumed workflow %s from node '%s'", thread_id, interrupt_node)
        except Exception as exc:
            logger.error("Resume failed for workflow %s: %s", thread_id, exc, exc_info=True)
            with self._lock:
                wf = self._active_workflows.get(thread_id)
                if wf:
                    wf["status"] = "failed"
                    wf["error"] = str(exc)
            return

        # Adjacent HITL nodes: after update_state(as_node=...), the pending
        # node may itself be a HITL gate. Streaming with None input would be
        # indistinguishable from resuming past THAT gate, so LangGraph would
        # execute it without pausing — surface the new interrupt instead.
        try:
            snapshot = self.graph.get_state(_bare_config(config))
            pending = tuple(getattr(snapshot, "next", ()) or ())
        except Exception:
            pending = ()
        if pending and pending[0] in HITL_NODES:
            self._finalize_stream_status(thread_id)
            return

        # Continue the graph in a background thread; the UI polls for status.
        thread = threading.Thread(
            target=self._continue_workflow,
            args=(thread_id, config),
            daemon=True,
        )
        thread.start()

    def _continue_workflow(self, thread_id: str, config: dict[str, Any]) -> None:
        """Stream the graph from its checkpoint until done or next interrupt."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:

                async def _collect_events():
                    events = []
                    async for event in self.graph.astream(None, config=config):
                        events.append(event)
                    return events

                events = loop.run_until_complete(_collect_events())

                for event in events:
                    for node_name, _output in event.items():
                        with self._lock:
                            wf = self._active_workflows.get(thread_id)
                            if wf:
                                wf["current_interrupt_node"] = node_name

                self._finalize_stream_status(thread_id)
            finally:
                loop.close()
                flush_traces()  # Part 1.5 C.6 — same rationale as _run_workflow

        except Exception as exc:
            logger.error("Resume failed for workflow %s: %s", thread_id, exc, exc_info=True)
            with self._lock:
                wf = self._active_workflows.get(thread_id)
                if wf:
                    wf["status"] = "failed"
                    wf["error"] = str(exc)

    def abort_workflow(self, thread_id: str) -> None:
        """Mark a workflow as aborted.

        Parameters
        ----------
        thread_id : str
            Workflow thread ID.
        """
        with self._lock:
            wf = self._active_workflows.get(thread_id)
            if wf:
                wf["status"] = "aborted"
                logger.info("Workflow %s aborted", thread_id)

    def list_active_workflows(self) -> list[dict[str, Any]]:
        """List all active (non-completed) workflows.

        Returns
        -------
        list[dict]
            [{thread_id, status, ...}, ...]
        """
        with self._lock:
            return [
                {
                    "thread_id": tid,
                    "status": wf["status"],
                    "current_interrupt_node": wf.get("current_interrupt_node"),
                }
                for tid, wf in self._active_workflows.items()
                if wf["status"] not in ("completed", "failed", "aborted")
            ]

    def cleanup_completed(self) -> int:
        """Remove completed workflows from memory.

        Returns
        -------
        int
            Number of workflows removed.
        """
        removed = 0
        with self._lock:
            to_remove = [
                tid
                for tid, wf in self._active_workflows.items()
                if wf["status"] in ("completed", "failed", "aborted")
            ]
            for tid in to_remove:
                del self._active_workflows[tid]
                removed += 1
        return removed
