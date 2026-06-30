"""
LangGraph Server Manager for merge-engine.

Manages workflows by communicating with a LangGraph CLI server via the
langgraph_sdk. Provides the same workflow lifecycle as WorkflowManager
but through HTTP/SSE API calls.

Usage:
    from src.orchestrator.server_manager import LangGraphServerManager
    
    manager = LangGraphServerManager(server_url="http://localhost:8123")
    thread_id = manager.start_workflow(user_request, origins, destination)
    status = manager.get_workflow_status(thread_id)
    manager.resume_workflow(thread_id, feedback)
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger("merge-engine.server_manager")


class LangGraphServerManager:
    """Manages merge workflows via LangGraph CLI server.
    
    This replaces WorkflowManager when running with the LangGraph server
    approach. It uses the langgraph_sdk to create threads, start runs,
    stream events, and handle HITL interrupts.
    """

    def __init__(self, server_url: str = "http://localhost:8123"):
        """Initialize the server manager.
        
        Parameters
        ----------
        server_url : str
            URL of the running LangGraph CLI server.
        """
        self.server_url = server_url
        self._client = None
        self._sync_client = None
        logger.debug("Initialized LangGraphServerManager (server_url=%s)", server_url)
    
    def _get_sync_client(self):
        """Get or create the synchronous SDK client."""
        if self._sync_client is None:
            try:
                from langgraph_sdk import get_sync_client
                logger.debug("Creating SyncLangGraphClient for %s", self.server_url)
                # get_sync_client() returns a SyncLangGraphClient with synchronous API
                self._sync_client = get_sync_client(url=self.server_url)
                logger.debug("Sync client created successfully")
            except Exception as exc:
                logger.error("Failed to connect to LangGraph server at %s: %s", 
                           self.server_url, exc, exc_info=True)
                raise
        return self._sync_client
    
    def start_workflow(
        self,
        user_request: str,
        config_path: str | None = None,
        thread_id: str | None = None,
        origins: list[dict[str, Any]] | None = None,
        destination: dict[str, Any] | None = None,
    ) -> str:
        """Start a new merge workflow.
        
        Creates a new thread and starts a run with the initial state
        containing origins, destination, and user request.
        
        Parameters
        ----------
        user_request : str
            Natural language prompt from the user.
        config_path : str, optional
            Ignored (server handles config via langgraph.json).
        thread_id : str, optional
            Custom thread ID. Auto-generated if None.
        origins : list[dict], optional
            Origin database configurations.
        destination : dict, optional
            Destination database configuration.
            
        Returns
        -------
        str
            thread_id for polling and resuming.
        """
        logger.debug("ENTER start_workflow")
        logger.debug("user_request (truncated): %.200s", user_request)
        logger.debug("origins count: %d", len(origins) if origins else 0)
        logger.debug("destination: %s", json.dumps(destination, default=str)[:300] if destination else "None")

        client = self._get_sync_client()
        
        # Build initial state
        initial_state = self._build_initial_state(user_request, origins, destination)
        logger.debug("Initial state keys: %s", list(initial_state.keys()))
        logger.debug("Initial state target_entities: %s", initial_state.get("target_entities"))
        logger.debug("Initial state origins count: %d", len(initial_state.get("origins", [])))
        
        try:
            # Let the server auto-generate thread_id (it returns the thread dict)
            logger.debug("Creating thread via client.threads.create()")
            thread = client.threads.create()
            thread_id = thread.get("thread_id")
            logger.info("Created thread: %s", thread_id)
            logger.debug("Thread data: %s", json.dumps(thread, default=str)[:300])
            
            # Start the run with initial state and auto_assign=True
            # The langgraph_sdk API uses assistant_id instead of graph_id
            # We use the default assistant for the "merge_engine" graph
            logger.debug("Starting run on thread %s (assistant_id=merge_engine)", thread_id)
            run = client.runs.create(
                thread_id=thread_id,
                assistant_id="merge_engine",
                input=initial_state,
                config={"configurable": {"auto_assign": True}},
                metadata={"source": "merge-engine"},
            )
            logger.info("Started run %s on thread %s", run.get("run_id"), thread_id)
            logger.debug("Run data: %s", json.dumps(run, default=str)[:500])
            
            logger.debug("EXIT start_workflow (thread_id=%s)", thread_id)
            return thread_id
            
        except Exception as exc:
            logger.error("Failed to start workflow: %s", exc, exc_info=True)
            raise
    
    def _build_initial_state(
        self, 
        user_request: str, 
        origins: list[dict] | None, 
        destination: dict | None
    ) -> dict[str, Any]:
        """Build the initial state dict for a new workflow.
        
        This mirrors get_initial_state() from graph.py but is needed here
        because the SDK sends the input directly to the server.
        """
        # Load LLM config from config.yaml (same as graph.py does in _load_llm_config)
        llm_config = self._load_llm_config()
        
        state = {
            "user_request": user_request,
            "target_entities": [],
            "origins": origins or [],
            "destination": destination or {},
            "llm_config": llm_config,
            "extraction_config": {"default_batch_size": 1000},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "human_feedback": None,
            "load_approval": None,
            "errors": [],
            "retry_counts": {},
            "interrupt_data": None,
        }
        logger.debug("_build_initial_state complete: %d keys", len(state))
        return state
    
    def _load_llm_config(self) -> dict[str, Any]:
        """Load LLM configuration from config file.
        
        Returns
        -------
        dict
            LLM configuration dict.
        """
        try:
            from src.utils.config_loader import load_config
            cfg = load_config("config/config.yaml")
            config = {
                "provider": cfg.llm.provider,
                "model": cfg.llm.model,
                "api_key": cfg.llm.api_key,
                "base_url": cfg.llm.base_url,
                "temperature": cfg.llm.temperature,
            }
            logger.debug("Loaded LLM config: provider=%s, model=%s", config["provider"], config["model"])
            return config
        except Exception:
            # Return placeholder -- LLM will be configured via env vars
            logger.warning("Failed to load LLM config, using defaults")
            return {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "",
                "base_url": None,
                "temperature": 0.0,
            }
    
    def get_workflow_status(self, thread_id: str) -> dict[str, Any]:
        """Get current workflow status and any pending interrupt data.
        
        Parameters
        ----------
        thread_id : str
            Workflow thread ID.
            
        Returns
        -------
        dict
            {status, interrupt_data, current_interrupt_node, summary, errors}
        """
        logger.debug("ENTER get_workflow_status (thread_id=%s)", thread_id)
        client = self._get_sync_client()
        
        try:
            # Get thread state
            logger.debug("Getting thread state for thread_id=%s", thread_id)
            state = client.threads.get_state(thread_id)
            logger.debug("Thread state received: keys=%s", list(state.keys()) if state else "None")
            
            if not state:
                logger.warning("Thread %s not found", thread_id)
                return {
                    "status": "error",
                    "interrupt_data": None,
                    "current_interrupt_node": None,
                    "error": f"Thread {thread_id} not found",
                }
            
            # Extract the actual state values
            raw_values = state.get("values", {})
            if isinstance(raw_values, dict):
                errors = raw_values.get("errors", [])
                interrupt_data_val = raw_values.get("interrupt_data")
                load_approval = raw_values.get("load_approval")
                target_entities = raw_values.get("target_entities", [])
                unified_entities = raw_values.get("unified_entities", {})
                logger.debug("State values: errors=%d, target_entities=%s", len(errors), target_entities)
            else:
                errors = []
                interrupt_data_val = None
                load_approval = None
                target_entities = []
                unified_entities = {}
                raw_values = {}
                logger.debug("State values is not a dict, using defaults")
            
            # Get the latest run status and current node
            current_node = None
            run_status = "running"
            try:
                logger.debug("Listing runs for thread_id=%s", thread_id)
                runs = client.runs.list(thread_id=thread_id, limit=1)
                if runs:
                    latest_run = runs[0]
                    run_status = latest_run.get("status", "unknown")
                    logger.debug("Latest run status: %s", run_status)
                    
                    # Get the next node(s) that will run - this tells us where we are
                    next_nodes = latest_run.get("next", [])
                    if next_nodes:
                        current_node = next_nodes[0]
                        logger.debug("Next node: %s", current_node)
            except Exception:
                logger.debug("Failed to get run status, defaulting to running")
                run_status = "running"
            
            # Check for pending interrupts (HITL checkpoints)
            if state.get("pendingWrites"):
                logger.debug("Found pendingWrites: %d writes", len(state.get("pendingWrites", [])))
                for write in state.get("pendingWrites", []):
                    if write.get("event") == "interrupt":
                        run_status = "interrupted"
                        current_node = write.get("node", current_node)
                        logger.debug("Interrupt found at node: %s", current_node)
            
            # Build interrupt_data from state if present
            interrupt_data = None
            current_interrupt_node = None
            if interrupt_data_val:
                run_status = "interrupted"
                interrupt_data = interrupt_data_val
                current_interrupt_node = interrupt_data_val.get("node") if isinstance(interrupt_data_val, dict) else None
                logger.debug("Interrupt data from state: node=%s", current_interrupt_node)
            
            # Override with current_node from run if not yet determined
            if current_node and not current_interrupt_node:
                current_interrupt_node = current_node
            
            # Determine final status
            final_status = "running"
            if run_status == "success":
                final_status = "completed"
            elif run_status == "error":
                final_status = "failed"
            elif run_status in ("interrupted", "pending"):
                final_status = "interrupted"
            elif load_approval is False:
                final_status = "aborted"
            
            result: dict[str, Any] = {
                "status": final_status,
                "interrupt_data": interrupt_data if final_status == "interrupted" else None,
                "current_interrupt_node": current_interrupt_node,
                "thread_state": state,
                "errors": errors,
            }
            
            # Add summary for completed workflows
            if final_status == "completed":
                result["summary"] = {
                    "target_entities": target_entities,
                    "entity_counts": {
                        k: len(v) if isinstance(v, list) else 0
                        for k, v in unified_entities.items()
                    },
                    "errors": errors,
                }
                logger.debug("Completed workflow summary: %s", result["summary"])
            elif final_status == "failed":
                result["error"] = "Run completed with errors"
            
            logger.debug("EXIT get_workflow_status (status=%s)", final_status)
            return result
            
        except Exception as exc:
            logger.error("Failed to get workflow status for thread %s: %s", thread_id, exc, exc_info=True)
            return {
                "status": "error",
                "error": str(exc),
                "interrupt_data": None,
                "current_interrupt_node": None,
            }
    
    def resume_workflow(
        self,
        thread_id: str,
        feedback: dict[str, Any],
    ) -> None:
        """Resume a paused workflow with user feedback.
        
        In the LangGraph SDK v3, we use stream().respond() to send
        HITL feedback back to the server.
        
        Parameters
        ----------
        thread_id : str
            Workflow thread ID.
        feedback : dict
            User's decisions from the HITL interrupt form.
        """
        logger.debug("ENTER resume_workflow (thread_id=%s, feedback=%s)", thread_id, json.dumps(feedback, default=str)[:300])
        client = self._get_sync_client()
        
        try:
            # Use update_state to submit human_feedback
            # The as_node parameter tells the server which node requested the interrupt
            logger.debug("Submitting feedback via client.threads.update_state()")
            client.threads.update_state(
                thread_id,
                {"human_feedback": feedback},
                as_node=None,  # Server determines the interrupt node
            )
            logger.info("Submitted feedback for thread %s, resuming workflow", thread_id)
            logger.debug("EXIT resume_workflow")
            
            # The workflow will automatically continue after update_state
            # No need to manually start a new run
            
        except Exception as exc:
            logger.error("Resume failed for thread %s: %s", thread_id, exc, exc_info=True)
            raise
    
    def abort_workflow(self, thread_id: str) -> None:
        """Abort a running workflow.
        
        Parameters
        ----------
        thread_id : str
            Workflow thread ID.
        """
        logger.debug("ENTER abort_workflow (thread_id=%s)", thread_id)
        client = self._get_sync_client()
        
        try:
            # List active runs for this thread
            logger.debug("Listing runs for thread_id=%s", thread_id)
            runs = client.runs.list(thread_id=thread_id, limit=10)
            logger.debug("Found %d runs", len(runs) if runs else 0)
            for run in runs or []:
                if run.get("status") in ("started", "running", "interrupted"):
                    logger.debug("Cancelling run %s", run.get("run_id"))
                    client.runs.cancel(thread_id, run["run_id"])
                    logger.info("Cancelled run %s on thread %s", run["run_id"], thread_id)
                    break
                    
        except Exception as exc:
            logger.error("Abort failed for thread %s: %s", thread_id, exc, exc_info=True)
        
        logger.debug("EXIT abort_workflow")
    
    def stream_workflow(
        self,
        thread_id: str,
        callback=None,
    ):
        """Stream events from a workflow run.
        
        This is the primary method for polling workflow progress.
        Opens an SSE connection that streams events in real-time.
        
        Parameters
        ----------
        thread_id : str
            Workflow thread ID.
        callback : callable, optional
            Function called for each event dict.
            
        Yields
        ------
        dict
            Event dicts from the streaming session.
        """
        logger.debug("ENTER stream_workflow (thread_id=%s)", thread_id)
        client = self._get_sync_client()
        
        try:
            # Open a streaming session for this thread
            logger.debug("Opening stream for thread_id=%s", thread_id)
            with client.threads.stream(thread_id) as stream:
                for event in stream.events:
                    if callback:
                        callback(event)
                    yield event
                    
                    # Check for interrupts
                    if stream.interrupted:
                        logger.debug("Stream interrupted")
                        interrupts = stream.interrupts
                        if interrupts:
                            # Return interrupt data for HITL handling
                            yield {"__interrupt__": True, "data": interrupts}
                        break
                            
        except Exception as exc:
            logger.error("Stream failed for thread %s: %s", thread_id, exc, exc_info=True)
            raise
    
    def list_active_workflows(self) -> list[dict[str, Any]]:
        """List all active (non-completed) workflows/threads.
        
        Returns
        -------
        list[dict]
            [{thread_id, status, ...}, ...]
        """
        logger.debug("ENTER list_active_workflows")
        client = self._get_sync_client()
        
        try:
            # List all threads — use http.get to call /threads API
            logger.debug("Listing threads via HTTP GET /threads")
            threads_response = client.http.get("/threads")
            threads = threads_response if isinstance(threads_response, list) else []
            logger.debug("Found %d threads", len(threads))
            active = []
            for thread in threads:
                tid = thread.get("thread_id")
                state = client.threads.get_thread(tid)
                metadata = state.get("metadata") or {}
                
                # Check if thread has active runs
                runs = client.runs.list(thread_id=tid, limit=1)
                if runs:
                    run = runs[0]
                    if run.get("status") in ("started", "running", "interrupted", "pending"):
                        active.append({
                            "thread_id": tid,
                            "status": run.get("status"),
                            "run_id": run.get("run_id"),
                            "created_at": run.get("created_at"),
                        })
                else:
                    # Thread exists but no active run
                    active.append({
                        "thread_id": tid,
                        "status": "idle",
                        "created_at": metadata.get("created_at"),
                    })
            
            logger.debug("Found %d active workflows", len(active))
            logger.debug("EXIT list_active_workflows")
            return active
            
        except Exception as exc:
            logger.error("Failed to list workflows: %s", exc, exc_info=True)
            return []
    
    def cleanup_completed(self, max_age_hours: int = 24) -> int:
        """Remove completed threads from the server.
        
        Parameters
        ----------
        max_age_hours : int
            Only remove threads older than this many hours.
            
        Returns
        -------
        int
            Number of threads removed.
        """
        logger.debug("ENTER cleanup_completed (max_age_hours=%d)", max_age_hours)
        # Note: Thread deletion is typically handled by the server
        # This method provides a cleanup hook
        logger.debug("EXIT cleanup_completed (no-op)")
        return 0