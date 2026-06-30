"""
Full end-to-end pipeline test through all HITL checkpoints via LangGraph Server.

Runs the complete merge pipeline with the test configuration:
- Origins: ClickHouse zoominfo, ClickHouse diffbot
- Destination: PostgreSQL global_warehouse

Auto-approves each HITL checkpoint to test the full flow.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.orchestrator.server_manager import LangGraphServerManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("merge-engine.full_pipeline_test")


# Test configuration
TEST_CONFIG = {
    "origins": [
        {
            "id": "origin_1",
            "type": "clickhouse",
            "connection": {
                "host": "10.10.9.46",
                "port": 8190,
                "user": "admin",
                "password": "pass",
                "database": "zoominfo",
            },
            "priority": 1,
        },
        {
            "id": "origin_2",
            "type": "clickhouse",
            "connection": {
                "host": "10.10.9.46",
                "port": 8195,
                "user": "admin",
                "password": "pass",
                "database": "diffbot",
            },
            "priority": 2,
        },
    ],
    "destination": {
        "type": "postgresql",
        "host": "localhost",
        "port": 5433,
        "user": "postgres",
        "password": "supersecurepassword",
        "database": "global_warehouse",
        "schema": "public",
    },
}

USER_REQUEST = "Import company and contact data from all source databases into the warehouse"


def run_full_pipeline(server_url: str = "http://localhost:8123") -> dict[str, Any]:
    """Run the full pipeline through all HITL checkpoints via LangGraph server.
    
    Parameters
    ----------
    server_url : str
        URL of the running LangGraph CLI server.
        
    Returns
    -------
    dict
        Pipeline results with status, stages, and timing.
    """
    manager = LangGraphServerManager(server_url=server_url)
    results = {
        "workflow_id": None,
        "status": "unknown",
        "stages": [],
        "start_time": time.time(),
        "errors": [],
        "server_url": server_url,
    }

    logger.info("=" * 60)
    logger.info("Starting full pipeline test via LangGraph Server")
    logger.info("Server URL: %s", server_url)
    logger.info("User request: %r", USER_REQUEST)
    logger.info("Origins: %d", len(TEST_CONFIG["origins"]))
    logger.info("Destination: %s", TEST_CONFIG["destination"])
    logger.info("=" * 60)

    # Start workflow
    thread_id = manager.start_workflow(
        user_request=USER_REQUEST,
        origins=TEST_CONFIG["origins"],
        destination=TEST_CONFIG["destination"],
    )
    results["workflow_id"] = thread_id
    logger.info("Workflow started on thread: %s", thread_id)

    # Poll loop using stream to detect interrupts
    max_iterations = 50
    iteration = 0
    interrupt_count = 0
    max_interrupts = 4  # human_review_deps, human_review_matches, human_review_mappings, human_final_approval

    while iteration < max_iterations:
        iteration += 1
        status = manager.get_workflow_status(thread_id)
        wf_status = status.get("status", "running")
        interrupt_data = status.get("interrupt_data")
        current_node = status.get("current_interrupt_node")

        logger.info(
            "  [Iter %d] Status: %s, Current node: %s",
            iteration, wf_status, current_node,
        )

        if wf_status in ("completed", "failed", "aborted"):
            results["status"] = wf_status
            break

        if wf_status == "interrupted" and interrupt_data:
            node = interrupt_data.get("node", "")
            data = interrupt_data.get("data", {})

            logger.info("-" * 40)
            logger.info("HITL Interrupt at node: %s", node)
            logger.info("  Title: %s", interrupt_data.get("title", ""))

            # Log interrupt details
            if node == "human_review_deps":
                dep_tree = data.get("dependency_tree", {})
                extraction_order = data.get("extraction_order", [])
                logger.info("  Dependency tree keys: %s", list(dep_tree.keys()))
                logger.info("  Extraction order: %s", extraction_order)
                results["stages"].append({
                    "stage": "dependency_review",
                    "status": "completed",
                    "tables": list(dep_tree.keys()),
                    "order": extraction_order,
                })

            elif node == "human_review_matches":
                clusters = data.get("low_confidence_clusters", [])
                logger.info("  Low-confidence clusters: %d", len(clusters))
                results["stages"].append({
                    "stage": "entity_match_review",
                    "status": "completed",
                    "clusters": len(clusters),
                })

            elif node == "human_review_mappings":
                unmapped = data.get("unmapped_fields", [])
                schema_changes = data.get("pending_schema_changes", [])
                logger.info("  Unmapped fields: %d", len(unmapped))
                logger.info("  Pending schema changes: %d", len(schema_changes))
                results["stages"].append({
                    "stage": "field_mapping_review",
                    "status": "completed",
                    "unmapped": len(unmapped),
                    "schema_changes": len(schema_changes),
                })

            elif node == "human_final_approval":
                summary = data.get("summary", {})
                logger.info("  Summary: %s", json.dumps(summary, indent=2))
                results["stages"].append({
                    "stage": "final_approval",
                    "status": "completed",
                    "summary": summary,
                })

            # Approve and resume
            interrupt_count += 1
            feedback = {"approved": True}
            logger.info("  Approving checkpoint and resuming...")
            manager.resume_workflow(thread_id, feedback)

            if interrupt_count >= max_interrupts:
                logger.info("All expected HITL checkpoints completed")

        else:
            import asyncio
            time.sleep(0.5)

    elapsed = time.time() - results["start_time"]
    results["elapsed_seconds"] = elapsed

    logger.info("=" * 60)
    logger.info("Pipeline test complete!")
    logger.info("Status: %s", results["status"])
    logger.info("Elapsed: %.2f seconds", elapsed)
    logger.info("Interrupts handled: %d", interrupt_count)
    logger.info("Stages: %s", json.dumps(results["stages"], indent=2, default=str))
    logger.info("=" * 60)

    return results


def main():
    """Run the full pipeline test."""
    server_url = "http://localhost:8123"
    logger.info("Connecting to LangGraph server at %s", server_url)
    
    # Test connection first
    try:
        manager = LangGraphServerManager(server_url=server_url)
        # Simple ping test
        threads = manager.list_active_workflows()
        logger.info("Connected to server successfully")
    except Exception as exc:
        logger.error("Cannot connect to LangGraph server at %s: %s", server_url, exc)
        logger.info("Start the server with: langgraph dev --port 8123")
        return 2
    
    results = run_full_pipeline(server_url=server_url)

    if results["status"] == "completed":
        logger.info("SUCCESS: Full pipeline completed through all HITL checkpoints!")
        return 0
    else:
        logger.warning("Pipeline ended with status: %s", results["status"])
        logger.info("Stages completed: %s", json.dumps(results["stages"], indent=2, default=str))
        return 1


if __name__ == "__main__":
    exit(main())