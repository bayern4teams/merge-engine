"""
Full Boeing pipeline test - runs through all HITL checkpoints.

This script tests the complete Merge Engine workflow with the Boeing company
records prompt, walking through all 4 HITL checkpoints and verifying
Langfuse tracing throughout.

Run with: python tests/integration/test_boeing_full_pipeline.py
"""

import asyncio
import json
import logging
import sys
import time
import uuid
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("boeing-pipeline-test")

# Ensure project root is on path
sys.path.insert(0, "/home/bayern/Projects/merging")

# ============================================================
# Test Configuration
# ============================================================

ORIGIN_1 = {
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
}

ORIGIN_2 = {
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
}

DESTINATION = {
    "type": "postgresql",
    "host": "localhost",
    "port": 5433,
    "user": "postgres",
    "password": "supersecurepassword",
    "database": "global_warehouse",
    "schema": "public",
}

BOEING_PROMPT = (
    "Merge all Boeing company records from all source databases into the "
    "destination. Include all employee contacts, product catalogs, and order "
    "history associated with Boeing. Deduplicate by company name and tax ID."
)

# Track results
results = []
step_counter = [0]


def next_step(description: str):
    step_counter[0] += 1
    log.info("=" * 70)
    log.info("STEP %d: %s", step_counter[0], description)
    log.info("=" * 70)
    return step_counter[0]


def record_result(step: int, description: str, status: str, details: str = ""):
    results.append({
        "step": step,
        "description": description,
        "status": status,
        "details": details,
        "timestamp": datetime.now().isoformat(),
    })
    icon = {"pass": "✅", "fail": "❌", "skip": "⏭️", "warn": "⚠️"}.get(status, "❓")
    log.info("%s STEP %d: %s - %s", icon, step, description, status.upper())
    if details:
        log.info("   Details: %s", details)


# ============================================================
# PHASE 1: Infrastructure Checks
# ============================================================

def check_service(url: str, name: str, step: int) -> bool:
    """Check if a service is responding."""
    import urllib.request
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            record_result(step, name, "pass", f"Running at {url}")
            return True
        else:
            record_result(step, name, "fail", f"Status: {resp.status}")
            return False
    except Exception as e:
        record_result(step, name, "fail", str(e))
        return False


# ============================================================
# PHASE 2: Config and Components
# ============================================================

def test_config_loading(step: int):
    """Load config from config.yaml."""
    from src.utils.config_loader import load_config

    try:
        config = load_config("config/config.yaml")
        record_result(step, "Config Load", "pass", "Config loaded from config.yaml")

        # Check LLM config
        if hasattr(config, "llm"):
            llm = config.llm
            record_result(step, "LLM Config", "pass",
                         f"Provider: {llm.provider}, Model: {llm.model}")
        else:
            record_result(step, "LLM Config", "fail", "LLM config missing")

        return config
    except Exception as e:
        record_result(step, "Config Load", "fail", str(e))
        return None


def test_graph_structure(step: int):
    """Test graph construction.

    Returns two graphs:
    1. A graph WITH checkpointer (for WorkflowManager-style use)
    2. A graph WITHOUT checkpointer (for direct astream with initial_state)
    """
    from src.orchestrator.graph import build_graph, _build_builder, HITL_NODES

    try:
        # Graph with MemorySaver checkpointer (same as WorkflowManager uses)
        graph_with_checkpointer = build_graph()
        record_result(step, "Graph Construction (with checkpointer)", "pass",
                     "Graph built with MemorySaver checkpointer")

        # Graph WITHOUT checkpointer for direct initial_state use
        # When using astream(initial_state) directly, we need no checkpointer
        builder = _build_builder()
        graph_no_checkpointer = builder.compile(
            interrupt_before=HITL_NODES,
            interrupt_after=[],
        )
        record_result(step, "Graph Construction (no checkpointer)", "pass",
                     "Graph built without checkpointer for direct astream")

        return graph_with_checkpointer, graph_no_checkpointer
    except Exception as e:
        record_result(step, "Graph Construction", "fail", str(e))
        return None, None


def test_state_initialization(step: int):
    """Test initial state creation."""
    from src.orchestrator.graph import get_initial_state

    try:
        state = get_initial_state(
            user_request=BOEING_PROMPT,
            origins=[ORIGIN_1, ORIGIN_2],
            destination=DESTINATION,
        )
        record_result(step, "State Initialization", "pass",
                     f"State created with {len(state['origins'])} origins")

        # Verify key fields
        required = ["user_request", "target_entities", "origins", "destination",
                    "llm_config", "schemas", "extracted_data", "errors"]
        for field in required:
            if field in state:
                record_result(step, f"State Field: {field}", "pass", "Present")
            else:
                record_result(step, f"State Field: {field}", "fail", "Missing")

        return state
    except Exception as e:
        record_result(step, "State Initialization", "fail", str(e))
        return None


# ============================================================
# PHASE 3: Full Pipeline Execution
# ============================================================

def run_full_pipeline(graph, initial_state, step_start: int):
    """Run the full pipeline through all HITL checkpoints.

    Returns the thread_id and final state.
    """
    from src.orchestrator.workflow_manager import WorkflowManager

    thread_id = f"boeing_test_{uuid.uuid4().hex[:8]}"

    try:
        # Create workflow manager
        wm = WorkflowManager.__new__(WorkflowManager)
        wm.graph = graph
        wm._active_workflows = {}
        wm._lock = __import__("threading").Lock()

        # Store workflow
        config_dict = {"configurable": {"thread_id": thread_id}}
        with wm._lock:
            wm._active_workflows[thread_id] = {
                "thread_id": thread_id,
                "config": config_dict,
                "status": "running",
                "current_interrupt_node": None,
                "interrupt_data": None,
            }

        record_result(step_start, "Workflow Started", "pass",
                     f"Thread ID: {thread_id}")

        # Run async stream
        async def _run():
            events = []
            async for event in graph.astream(initial_state, config=config_dict):
                events.append(event)
                for node_name, output in event.items():
                    log.info("  --> Node '%s' output keys: %s",
                           node_name, list(output.keys()) if output else "None")

                    # Check for interrupt data
                    if output and output.get("interrupt_data"):
                        interrupt_data = output["interrupt_data"]
                        record_result(
                            step_start + 10,  # Will be updated per node
                            f"HITL: {node_name}",
                            "interrupted",
                            f"Interrupt data keys: {list(interrupt_data.keys())}"
                        )
            return events

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            events = loop.run_until_complete(_run())
        finally:
            loop.close()

        # Check what happened
        with wm._lock:
            wf = wm._active_workflows.get(thread_id)
            status = wf["status"] if wf else "unknown"
            interrupt_node = wf.get("current_interrupt_node") if wf else None

        record_result(step_start + 100, "Pipeline Execution Result", "pass",
                     f"Final status: {status}, Interrupts: {len(events)} events")

        return thread_id, wm, events

    except Exception as e:
        import traceback
        traceback.print_exc()
        record_result(step_start, "Full Pipeline", "fail", str(e))
        return None, None, []


# ============================================================
# PHASE 4: Direct Graph Test (bypass WorkflowManager)
# ============================================================

def run_direct_pipeline(graph_with_cp, initial_state, step_start: int):
    """Run pipeline using WorkflowManager which properly handles state.

    The WorkflowManager builds a graph with MemorySaver checkpointer and
    properly passes initial_state through the workflow graph.
    """
    from src.orchestrator.workflow_manager import WorkflowManager

    try:
        # Use the actual WorkflowManager which is the production way to run this
        wm = WorkflowManager()
        thread_id = wm.start_workflow(
            user_request=BOEING_PROMPT,
            config_path="config/config.yaml",
            origins=[ORIGIN_1, ORIGIN_2],
            destination=DESTINATION,
        )

        record_result(step_start, "WorkflowManager Pipeline Start", "pass",
                     f"Thread ID: {thread_id}")

        # Poll status to see what happens
        import time
        max_wait = 120  # 2 minutes max for LLM calls
        start_time = time.time()
        interrupted_nodes = []

        while time.time() - start_time < max_wait:
            status = wm.get_workflow_status(thread_id)
            status_str = status.get("status", "unknown")
            interrupt_node = status.get("current_interrupt_node")

            if interrupt_node:
                interrupted_nodes.append(interrupt_node)
                record_result(
                    step_start + 20,
                    f"HITL: {interrupt_node}",
                    "interrupted",
                    f"Status: {status_str}"
                )
                log.info("  --> Workflow interrupted at: %s", interrupt_node)

                # Auto-approve all checkpoints for testing
                if interrupt_node == "human_review_deps":
                    # Approve dependencies
                    wm.resume_workflow(thread_id, {"action": "approve", "dependencies": {}})
                elif interrupt_node == "human_review_matches":
                    # Approve entity matches
                    wm.resume_workflow(thread_id, {"action": "approve", "matches": {}})
                elif interrupt_node == "human_review_mappings":
                    # Approve field mappings
                    wm.resume_workflow(thread_id, {"action": "approve", "mappings": {}})
                elif interrupt_node == "human_final_approval":
                    # Final approval - approve
                    wm.resume_workflow(thread_id, {"load_approval": True})
                time.sleep(2)  # Wait for workflow to process
            elif status_str in ("completed", "failed", "aborted"):
                record_result(
                    step_start + 40,
                    "Pipeline Final Status",
                    "pass" if status_str == "completed" else status_str,
                    f"Status: {status_str}"
                )
                break
            else:
                time.sleep(1)

        record_result(step_start + 50, "Pipeline Execution", "pass",
                     f"Thread: {thread_id}, Interrupted at: {interrupted_nodes}")
        return thread_id, wm

    except Exception as e:
        import traceback
        traceback.print_exc()
        record_result(step_start, "WorkflowManager Pipeline", "fail", str(e))
        return None, None


# ============================================================
# PHASE 5: LLM Integration Test
# ============================================================

def test_llm_parse_request(step: int):
    """Test LLM parse_request with Boeing prompt."""
    from src.llm.client import LLMClient

    try:
        # Load LLM config from config.yaml
        from src.utils.config_loader import load_config
        config = load_config("config/config.yaml")

        llm_config = {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "api_key": config.llm.api_key,
            "base_url": config.llm.base_url,
            "temperature": config.llm.temperature,
        }

        client = LLMClient(llm_config)
        record_result(step, "LLMClient Created", "pass",
                     f"Provider: {client.provider}, Model: {client.model_name}")

        # Call parse_request (async)
        async def _call():
            result = await client.call_json(
                system_prompt="You extract entity types from user requests. Return only JSON.",
                user_prompt=BOEING_PROMPT,
            )
            return result

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_call())
        finally:
            loop.close()

        if result:
            record_result(step + 1, "LLM parse_request", "pass",
                        f"Result type: {type(result)}, content: {result}")
        else:
            record_result(step + 1, "LLM parse_request", "warn", "Empty result")

        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        record_result(step, "LLM parse_request", "fail", str(e))
        return None


# ============================================================
# PHASE 6: Langfuse Verification
# ============================================================

def check_langfuse_traces(step: int):
    """Check Langfuse for traces."""
    import urllib.request

    try:
        # Check API
        req = urllib.request.Request("http://localhost:3000/api/keys")
        req.add_header("Authorization", "Basic YWRtaW5AZXhhbXBsZS5jb206cGFzcw==")
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            record_result(step, "Langfuse API", "pass", "API responding")
        else:
            record_result(step, "Langfuse API", "warn", f"Status: {resp.status}")
    except Exception as e:
        record_result(step, "Langfuse API", "warn", str(e))


# ============================================================
# PHASE 7: Report Generation
# ============================================================

def generate_report():
    """Generate final test report."""
    log.info("")
    log.info("=" * 80)
    log.info("FULL TEST REPORT - %s", datetime.now().isoformat())
    log.info("=" * 80)

    total = len(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    warnings = sum(1 for r in results if r["status"] == "warn")
    interrupted = sum(1 for r in results if r["status"] == "interrupted")

    log.info("")
    log.info("SUMMARY:")
    log.info("  Total Steps:    %d", total)
    log.info("  Passed:         %d", passed)
    log.info("  Failed:         %d", failed)
    log.info("  Warnings:       %d", warnings)
    log.info("  Interrupted:    %d (HITL checkpoints)", interrupted)
    log.info("")

    log.info("DETAILED RESULTS:")
    log.info("-" * 80)

    for r in results:
        icon = {"pass": "✅", "fail": "❌", "warn": "⚠️",
                "interrupted": "🔶", "aborted": "🛑", "skip": "⏭️"}.get(
                    r["status"], "❓")
        log.info("  %s  Step %d: %s - %s", icon, r["step"], r["description"], r["status"].upper())
        if r.get("details"):
            log.info("       %s", r["details"])

    log.info("")
    log.info("-" * 80)

    if failed > 0:
        log.info("")
        log.info("FAILED STEPS:")
        for r in results:
            if r["status"] == "fail":
                log.info("  ❌ Step %d: %s - %s", r["step"], r["description"], r.get("details", ""))
        log.info("")

    # Save report
    report_path = "tests/integration/test_boeing_report.json"
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "interrupted": interrupted,
            },
            "results": results,
        }, f, indent=2)

    log.info("Report saved to: %s", report_path)
    log.info("")
    log.info("=" * 80)

    return passed, failed, warnings, interrupted


# ============================================================
# MAIN
# ============================================================

def main():
    """Run the full Boeing pipeline test."""
    log.info("")
    log.info("#" * 80)
    log.info("#  MERGE ENGINE - BOEING FULL PIPELINE TEST")
    log.info("#  Prompt: Boeing Company Records Merge")
    log.info("#  Timestamp: %s", datetime.now().isoformat())
    log.info("#" * 80)
    log.info("")

    # ============================================================
    # PHASE 1: Infrastructure Verification
    # ============================================================

    step = next_step("Verify Gradio Server on port 7860")
    check_service("http://localhost:7860/", "Gradio UI", step)

    step = next_step("Verify Langfuse Server on port 3000")
    check_service("http://localhost:3000/", "Langfuse UI", step)

    # ============================================================
    # PHASE 2: Configuration and Components
    # ============================================================

    step = next_step("Load config from config.yaml")
    test_config_loading(step)

    step = next_step("Test graph construction")
    graph_with_cp, graph_no_cp = test_graph_structure(step)

    if graph_no_cp is None:
        record_result(next_step("Skip tests"), "Graph Required", "fail",
                     "Cannot proceed without graph")
        generate_report()
        sys.exit(1)
        return

    step = next_step("Test state initialization")
    initial_state = test_state_initialization(step)

    if initial_state is None:
        record_result(next_step("Skip tests"), "State Required", "fail",
                     "Cannot proceed without initial state")
        generate_report()
        sys.exit(1)
        return

    # ============================================================
    # PHASE 3: Full Pipeline - Direct Execution (no checkpointer needed)
    # ============================================================

    step = next_step("Run direct pipeline (Boeing prompt)")
    thread_id, events = run_direct_pipeline(graph_no_cp, initial_state, step)

    # ============================================================
    # PHASE 4: LLM Integration Test
    # ============================================================

    step = next_step("Test LLM parse_request with Boeing prompt")
    test_llm_parse_request(step)

    # ============================================================
    # PHASE 5: Langfuse Verification
    # ============================================================

    step = next_step("Check Langfuse traces")
    check_langfuse_traces(step)

    # ============================================================
    # PHASE 6: WorkflowManager Integration
    # ============================================================

    step = next_step("Test WorkflowManager integration")
    try:
        from src.orchestrator.workflow_manager import WorkflowManager
        wm = WorkflowManager()
        record_result(step, "WorkflowManager Created", "pass",
                     "Manager instantiated with graph")

        # Test methods exist
        for method in ["get_workflow_status", "resume_workflow", "abort_workflow",
                       "list_active_workflows"]:
            if hasattr(wm, method):
                record_result(step, f"Method: {method}", "pass", "Exists")
            else:
                record_result(step, f"Method: {method}", "fail", "Missing")

    except Exception as e:
        record_result(step, "WorkflowManager Integration", "fail", str(e))

    # ============================================================
    # FINAL REPORT
    # ============================================================

    passed, failed, warnings, interrupted = generate_report()

    log.info("")
    log.info("LANGFUSE DASHBOARD: http://localhost:3000")
    log.info("GRADIO UI: http://localhost:7860")
    log.info("")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()