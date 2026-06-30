"""
Workflow manager for merge-engine.

Manages long-running workflows with pause/resume capability via LangGraph
checkpoints. Used by both the Gradio UI and programmatic API.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import Any

from src.orchestrator.graph import build_graph, get_initial_state
from src.utils.config_loader import load_config

logger = logging.getLogger("merge-engine.workflow_manager")


class WorkflowManager:
    """Manages multiple concurrent merge workflows with interrupt support.

    Each workflow runs in a background thread and can be paused at HITL
    checkpoints for user review. State is persisted via LangGraph checkpoints.
    """

    def __init__(self) -> None:
        """Initialize the workflow manager.

        Builds the LangGraph once and reuses it for all workflows.
        """
        self.graph = build_graph()
        self._active_workflows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start_workflow(
        self,
        user_request: str,
        config_path: str | None = None,
        thread_id: str | None = None,
        origins: list[dict[str, Any]] | None = None,
        destination: dict[str, Any] | None = None,
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

        Returns
        -------
        str
            thread_id for polling and resuming.
        """
        thread_id = thread_id or f"wf_{uuid.uuid4().hex[:8]}"
        config_path = config_path or "config/config.yaml"

        # Build initial state with origins and destination from UI
        initial_state = get_initial_state(
            user_request=user_request,
            config_path=config_path,
            origins=origins,
            destination=destination,
        )

        # Store workflow metadata
        with self._lock:
            self._active_workflows[thread_id] = {
                "thread_id": thread_id,
                "config": {"configurable": {"thread_id": thread_id}},
                "status": "running",
                "current_interrupt_node": None,
                "interrupt_data": None,
            }

        # Start background thread
        thread = threading.Thread(
            target=self._run_workflow,
            args=(thread_id, initial_state, self._active_workflows[thread_id]["config"]),
            daemon=True,
        )
        thread.start()

        logger.info(
            "Started workflow %s for request: %r (origins: %d, dest: %s)",
            thread_id, user_request[:50],
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
        import sys
        
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

                # If we get here without interruption, workflow completed
                with self._lock:
                    wf = self._active_workflows.get(thread_id)
                    if wf:
                        wf["status"] = "completed"
                        logger.info("Workflow %s completed successfully", thread_id)
            finally:
                loop.close()

        except Exception as exc:
            logger.error("Workflow %s failed: %s", thread_id, exc, exc_info=True)
            with self._lock:
                wf = self._active_workflows.get(thread_id)
                if wf:
                    wf["status"] = "failed"
                    wf["error"] = str(exc)

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
                return {"status": "not_found"}

            result = {
                "status": wf["status"],
                "interrupt_data": wf.get("interrupt_data"),
                "current_interrupt_node": wf.get("current_interrupt_node"),
            }

            # Add summary if running/completed
            if wf["status"] in ("completed", "failed"):
                result["summary"] = self._get_workflow_summary(thread_id)

            return result

    def _get_workflow_summary(self, thread_id: str) -> dict[str, Any]:
        """Get a summary of the completed workflow state."""
        config = self._active_workflows.get(thread_id, {}).get("config", {})
        if not config:
            return {}

        # Get the latest state from the graph
        try:
            latest = self.graph.get_state(config)
            if latest and latest.values:
                values = latest.values
                return {
                    "target_entities": values.get("target_entities", []),
                    "entity_counts": {
                        k: len(v) if isinstance(v, list) else 0
                        for k, v in values.get("unified_entities", {}).items()
                    },
                    "errors": values.get("errors", []),
                }
        except Exception:
            pass

        return {}

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

        try:
            # Update the graph state with user feedback
            self.graph.update_state(
                config,
                {"human_feedback": feedback},
                as_node=interrupt_node,
            )

            logger.info("Resumed workflow %s from node '%s'", thread_id, interrupt_node)

            # Continue streaming from the checkpoint using async API
            async def _async_resume_stream():
                async for event in self.graph.astream(None, config=config):
                    yield event

            loop = asyncio.new_event_loop()
            try:
                for event in loop.run_until_complete(_async_resume_stream()):
                    for node_name, output in event.items():
                        with self._lock:
                            wf = self._active_workflows.get(thread_id)
                            if wf:
                                wf["current_interrupt_node"] = node_name

                                if output and output.get("interrupt_data"):
                                    wf["status"] = "interrupted"
                                    wf["interrupt_data"] = output["interrupt_data"]
                                elif output and output.get("load_approval") is not None:
                                    if output["load_approval"]:
                                        wf["status"] = "running"
                                    else:
                                        wf["status"] = "aborted"

                with self._lock:
                    wf = self._active_workflows.get(thread_id)
                    if wf:
                        wf["status"] = "completed"
                        logger.info("Workflow %s completed after resume", thread_id)
            finally:
                loop.close()

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