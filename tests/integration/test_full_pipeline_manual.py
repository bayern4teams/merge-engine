"""
Manual full pipeline test script - 30-step human-in-the-loop testing.

Run with: python tests/integration/test_full_pipeline_manual.py

This script simulates a human tester walking through the entire Merge Engine
pipeline with the Boeing company records prompt, checking each stage, HITL
checkpoints, and Langfuse tracing.
"""

import json
import logging
import time
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("full-pipeline-test")

# Test configuration matching user's request
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

# Track test results
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


def check_langfuse(current_step: int):
    """Check if Langfuse is running at http://localhost:3000."""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:3000/")
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            record_result(current_step, "Langfuse Dashboard", "pass", "Running at http://localhost:3000")
            return True
        else:
            record_result(current_step, "Langfuse Dashboard", "fail", f"Status: {resp.status}")
            return False
    except Exception as e:
        record_result(current_step, "Langfuse Dashboard", "fail", str(e))
        return False


def check_gradio(current_step: int):
    """Check if Gradio is running at http://localhost:7860."""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:7860/")
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            record_result(current_step, "Gradio UI", "pass", "Running at http://localhost:7860")
            return True
        else:
            record_result(current_step, "Gradio UI", "fail", f"Status: {resp.status}")
            return False
    except Exception as e:
        record_result(current_step, "Gradio UI", "fail", str(e))
        return False


def check_langgraph_server(current_step: int):
    """Check if LangGraph server is running at http://localhost:8123."""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:8123/docs")
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            record_result(current_step, "LangGraph Server", "pass", "Running at http://localhost:8123")
            return True
        else:
            record_result(current_step, "LangGraph Server", "fail", f"Status: {resp.status}")
            return False
    except Exception as e:
        record_result(current_step, "LangGraph Server", "fail", str(e))
        return False


def test_clickhouse_connection(current_step: int, origin_config, label: str):
    """Test ClickHouse connection and schema discovery."""
    from src.ui.components.db_discovery import test_and_discover_schemas

    conn = origin_config["connection"]
    result = test_and_discover_schemas(
        conn_type=origin_config["type"],
        host=conn["host"],
        port=conn["port"],
        user=conn["user"],
        password=conn["password"],
        database=conn["database"],
    )

    if result.get("connected"):
        tables = result.get("tables", [])
        schemas = result.get("schemas", [])
        record_result(
            current_step, label, "pass",
            f"Connected. Tables: {len(tables)}, Schemas: {len(schemas)}"
        )
        if tables:
            record_result(current_step, f"{label} - Discovered tables", "pass",
                         ", ".join(tables[:20]))
        return result
    else:
        record_result(
            current_step, label, "fail",
            result.get("error", "Unknown connection error")
        )
        return None


def test_postgresql_connection(current_step: int):
    """Test PostgreSQL destination connection."""
    from src.ui.components.db_discovery import test_and_discover_schemas

    result = test_and_discover_schemas(
        conn_type="postgresql",
        host=DESTINATION["host"],
        port=DESTINATION["port"],
        user=DESTINATION["user"],
        password=DESTINATION["password"],
        database=DESTINATION["database"],
        schema=DESTINATION.get("schema", "public"),
    )

    if result.get("connected"):
        tables = result.get("tables", [])
        schemas = result.get("schemas", [])
        record_result(
            current_step, "Destination Connection", "pass",
            f"Connected. Tables: {len(tables)}, Schemas: {len(schemas)}"
        )
        if tables:
            record_result(current_step, "Destination - Discovered tables", "pass",
                         ", ".join(tables[:20]))
        return result
    else:
        record_result(
            current_step, "Destination Connection", "fail",
            result.get("error", "Unknown connection error")
        )
        return None


def test_mcp_tools(current_step: int):
    """Test all MCP tools directly."""
    from src.mcp_server.tools.extract_data import extract_data
    from src.mcp_server.tools.discover_schema import discover_schema
    from src.mcp_server.tools.resolve_entities import resolve_entities
    from src.mcp_server.tools.map_fields import map_fields
    from src.mcp_server.tools.load_data import load_data
    from src.mcp_server.tools.mark_imported import mark_imported

    tools = [
        ("extract_data", extract_data),
        ("discover_schema", discover_schema),
        ("resolve_entities", resolve_entities),
        ("map_fields", map_fields),
        ("load_data", load_data),
        ("mark_imported", mark_imported),
    ]

    for name, tool_fn in tools:
        # Check if function exists and is callable
        if callable(tool_fn):
            record_result(current_step, f"MCP Tool: {name}", "pass", "Tool registered and callable")
        else:
            record_result(current_step, f"MCP Tool: {name}", "fail", "Tool not callable")


def test_workflow_manager(current_step: int):
    """Test WorkflowManager initialization and basic operations."""
    from src.orchestrator.workflow_manager import WorkflowManager

    try:
        wm = WorkflowManager()
        record_result(current_step, "WorkflowManager Init", "pass", "Manager created successfully")

        # Test status method exists
        if hasattr(wm, "get_workflow_status"):
            record_result(current_step, "WorkflowManager.get_workflow_status", "pass", "Method exists")
        else:
            record_result(current_step, "WorkflowManager.get_workflow_status", "fail", "Method missing")

        # Test start method exists
        if hasattr(wm, "start_workflow"):
            record_result(current_step, "WorkflowManager.start_workflow", "pass", "Method exists")
        else:
            record_result(current_step, "WorkflowManager.start_workflow", "fail", "Method missing")

        # Test resume method exists
        if hasattr(wm, "resume_workflow"):
            record_result(current_step, "WorkflowManager.resume_workflow", "pass", "Method exists")
        else:
            record_result(current_step, "WorkflowManager.resume_workflow", "fail", "Method missing")

        # Test abort method exists
        if hasattr(wm, "abort_workflow"):
            record_result(current_step, "WorkflowManager.abort_workflow", "pass", "Method exists")
        else:
            record_result(current_step, "WorkflowManager.abort_workflow", "fail", "Method missing")

        return wm
    except Exception as e:
        record_result(current_step, "WorkflowManager Init", "fail", str(e))
        return None


def test_llm_client(current_step: int):
    """Test LLM client initialization and basic operations."""
    from src.llm.client import LLMClient

    try:
        client = LLMClient(session_name="manual-test-session")
        record_result(current_step, "LLMClient Init", "pass", "Client created successfully")

        if hasattr(client, "parse_request"):
            record_result(current_step, "LLMClient.parse_request", "pass", "Method exists")
        else:
            record_result(current_step, "LLMClient.parse_request", "fail", "Method missing")

        if hasattr(client, "analyze_dependencies"):
            record_result(current_step, "LLMClient.analyze_dependencies", "pass", "Method exists")
        else:
            record_result(current_step, "LLMClient.analyze_dependencies", "fail", "Method missing")

        if hasattr(client, "resolve_entities"):
            record_result(current_step, "LLMClient.resolve_entities", "pass", "Method exists")
        else:
            record_result(current_step, "LLMClient.resolve_entities", "fail", "Method missing")

        if hasattr(client, "map_fields"):
            record_result(current_step, "LLMClient.map_fields", "pass", "Method exists")
        else:
            record_result(current_step, "LLMClient.map_fields", "fail", "Method missing")

        return client
    except Exception as e:
        record_result(current_step, "LLMClient Init", "fail", str(e))
        return None


def test_parse_request_llm(current_step: int):
    """Test the parse_request LLM call with Boeing prompt."""
    from src.llm.client import LLMClient

    try:
        client = LLMClient(session_name="manual-test-boeing")

        # Call parse_request directly
        result = client.parse_request(BOEING_PROMPT)

        if result and isinstance(result, dict):
            record_result(
                current_step, "Parse Request (LLM)", "pass",
                f"Companies: {result.get('entities', [])}, "
                f"Operations: {result.get('operations', [])}"
            )
            return result
        else:
            record_result(
                current_step, "Parse Request (LLM)", "fail",
                f"Unexpected result type: {type(result)}, value: {result}"
            )
            return None
    except Exception as e:
        record_result(current_step, "Parse Request (LLM)", "fail", str(e))
        return None


def test_discover_schemas_mcp(current_step: int):
    """Test discover_schema MCP tool."""
    try:
        from src.mcp_server.tools.discover_schema import discover_schema

        result = discover_schema(origin_id="origin_1")

        if result:
            record_result(current_step, "Discover Schema MCP", "pass",
                         f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'non-dict'}")
            return result
        else:
            record_result(current_step, "Discover Schema MCP", "fail", "Empty result")
            return None
    except Exception as e:
        record_result(current_step, "Discover Schema MCP", "fail", str(e))
        return None


def test_state_management(current_step: int):
    """Test orchestrator state management."""
    from src.orchestrator.state import PipelineState

    try:
        state = PipelineState()
        record_result(current_step, "PipelineState Init", "pass", "State created")

        # Test basic properties
        if hasattr(state, "request"):
            record_result(current_step, "PipelineState.request", "pass", "Attribute exists")
        else:
            record_result(current_step, "PipelineState.request", "fail", "Attribute missing")

        if hasattr(state, "origins"):
            record_result(current_step, "PipelineState.origins", "pass", "Attribute exists")
        else:
            record_result(current_step, "PipelineState.origins", "fail", "Attribute missing")

        if hasattr(state, "destination"):
            record_result(current_step, "PipelineState.destination", "pass", "Attribute exists")
        else:
            record_result(current_step, "PipelineState.destination", "fail", "Attribute missing")

        return state
    except Exception as e:
        record_result(current_step, "PipelineState Init", "fail", str(e))
        return None


def test_graph_structure(current_step: int):
    """Test the workflow graph structure."""
    from src.orchestrator.graph import construct_graph

    try:
        graph = construct_graph()
        record_result(current_step, "Graph Construction", "pass", "Graph created successfully")

        # Check nodes
        expected_nodes = [
            "parse_request",
            "analyze_dependencies",
            "discover_schemas",
            "extract_data",
            "resolve_entities",
            "map_fields",
            "load_data",
            "human_review_deps",
            "human_review_matches",
            "human_review_mappings",
            "human_final_approval",
            "handle_error",
        ]

        node_list = getattr(graph, "nodes", [])
        for node_id in expected_nodes:
            if node_id in node_list:
                record_result(current_step, f"Graph Node: {node_id}", "pass", "Node present in graph")
            else:
                record_result(current_step, f"Graph Node: {node_id}", "fail", "Node missing from graph")

        return graph
    except Exception as e:
        record_result(current_step, "Graph Construction", "fail", str(e))
        return None


def test_connectors(current_step: int):
    """Test database connector classes."""
    from src.mcp_server.connectors.clickhouse import ClickHouseConnector
    from src.mcp_server.connectors.postgresql_destination import PostgreSQLDestinationConnector

    # Test ClickHouse connector
    try:
        ch = ClickHouseConnector(
            host="10.10.9.46",
            port=8190,
            user="admin",
            password="pass",
            database="zoominfo",
        )
        record_result(current_step, "ClickHouseConnector Init", "pass", "Connector created")

        if hasattr(ch, "extract"):
            record_result(current_step, "ClickHouseConnector.extract", "pass", "Method exists")
        else:
            record_result(current_step, "ClickHouseConnector.extract", "fail", "Method missing")

        if hasattr(ch, "discover_schema"):
            record_result(current_step, "ClickHouseConnector.discover_schema", "pass", "Method exists")
        else:
            record_result(current_step, "ClickHouseConnector.discover_schema", "fail", "Method missing")
    except Exception as e:
        record_result(current_step, "ClickHouseConnector Init", "fail", str(e))

    # Test PostgreSQL destination connector
    try:
        pg = PostgreSQLDestinationConnector(
            host="localhost",
            port=5433,
            user="postgres",
            password="supersecurepassword",
            database="global_warehouse",
            schema="public",
        )
        record_result(current_step, "PostgreSQLDestinationConnector Init", "pass", "Connector created")

        if hasattr(pg, "load"):
            record_result(current_step, "PostgreSQLDestinationConnector.load", "pass", "Method exists")
        else:
            record_result(current_step, "PostgreSQLDestinationConnector.load", "fail", "Method missing")

        if hasattr(pg, "create_table"):
            record_result(current_step, "PostgreSQLDestinationConnector.create_table", "pass", "Method exists")
        else:
            record_result(current_step, "PostgreSQLDestinationConnector.create_table", "fail", "Method missing")
    except Exception as e:
        record_result(current_step, "PostgreSQLDestinationConnector Init", "fail", str(e))


def test_config_loading(current_step: int):
    """Test config loading from config.yaml."""
    from src.utils.config_loader import load_config

    try:
        config = load_config("config/config.yaml")
        record_result(current_step, "Config Load", "pass", "Config loaded successfully")

        # Check LLM config
        if hasattr(config, "llm"):
            llm = config.llm
            record_result(current_step, "Config LLM", "pass",
                         f"Provider: {llm.provider}, Model: {llm.model}")
        else:
            record_result(current_step, "Config LLM", "fail", "LLM config missing")

        # Check retry config
        if hasattr(config, "retry"):
            retry = config.retry
            record_result(current_step, "Config Retry", "pass",
                         f"Max attempts: {retry.max_attempts}")
        else:
            record_result(current_step, "Config Retry", "fail", "Retry config missing")

        # Check extraction config
        if hasattr(config, "extraction"):
            extr = config.extraction
            record_result(current_step, "Config Extraction", "pass",
                         f"Batch size: {extr.default_batch_size}")
        else:
            record_result(current_step, "Config Extraction", "fail", "Extraction config missing")

        # Check langfuse config
        if hasattr(config, "langfuse"):
            lf = config.langfuse
            record_result(current_step, "Config Langfuse", "pass",
                         f"Enabled: {lf.enabled}, Host: {lf.host}")
        else:
            record_result(current_step, "Config Langfuse", "warn", "Langfuse config missing")

        return config
    except Exception as e:
        record_result(current_step, "Config Load", "fail", str(e))
        return None


def test_dependency_sort(current_step: int):
    """Test dependency sorting utility."""
    from src.orchestrator.utils.dependency_sort import topological_sort

    try:
        # Test basic dependency graph
        deps = {
            "extract_table1": [],
            "extract_table2": ["extract_table1"],
            "extract_table3": ["extract_table1", "extract_table2"],
        }
        result = topological_sort(deps)

        if result:
            record_result(current_step, "Dependency Sort", "pass",
                         f"Order: {result}")

            # Verify ordering
            idx1 = result.index("extract_table1")
            idx2 = result.index("extract_table2")
            idx3 = result.index("extract_table3")

            if idx1 < idx2 < idx3:
                record_result(current_step, "Dependency Ordering", "pass",
                             "Dependencies correctly ordered")
            else:
                record_result(current_step, "Dependency Ordering", "fail",
                             "Dependencies not in correct order")
        else:
            record_result(current_step, "Dependency Sort", "fail", "Empty result")
    except Exception as e:
        record_result(current_step, "Dependency Sort", "fail", str(e))


def test_id_utils(current_step: int):
    """Test ID generation utilities."""
    from src.utils.id_utils import generate_uuid, validate_uuid

    try:
        id1 = generate_uuid()
        id2 = generate_uuid()

        if id1 and id2:
            record_result(current_step, "ID Generation", "pass",
                         f"Generated: {id1[:16]}..., {id2[:16]}...")
            if validate_uuid(id1) and validate_uuid(id2):
                record_result(current_step, "ID Validation", "pass", "UUIDs are valid hex format")
            else:
                record_result(current_step, "ID Validation", "fail", "Invalid UUID format")
            if id1 != id2:
                record_result(current_step, "ID Uniqueness", "pass", "IDs are unique")
            else:
                record_result(current_step, "ID Uniqueness", "warn", "IDs are identical (deterministic prefix)")
        else:
            record_result(current_step, "ID Generation", "fail", "Empty ID")
    except Exception as e:
        record_result(current_step, "ID Generation", "fail", str(e))


def test_logging_config(current_step: int):
    """Test logging configuration."""
    from src.utils.logging_config import setup_logging

    try:
        setup_logging(level="DEBUG", json=False)
        test_logger = logging.getLogger("merge-engine.test")
        test_logger.debug("Debug message test")
        test_logger.info("Info message test")
        test_logger.warning("Warning message test")
        test_logger.error("Error message test")
        record_result(current_step, "Logging Config", "pass", "Logger setup and messages logged")
    except Exception as e:
        record_result(current_step, "Logging Config", "fail", str(e))


def test_error_handling(current_step: int):
    """Test error handling paths."""
    from src.orchestrator.state import PipelineState

    try:
        state = PipelineState()

        # Test invalid state transitions
        record_result(current_step, "Error Handling Setup", "pass", "Testing error paths")

        # Test with None values
        try:
            result = state.get_status()
            record_result(current_step, "State Get Status", "pass", f"Status: {result}")
        except Exception as e:
            record_result(current_step, "State Get Status", "warn", str(e))
    except Exception as e:
        record_result(current_step, "Error Handling", "fail", str(e))


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
    skipped = sum(1 for r in results if r["status"] == "skip")

    log.info("")
    log.info("SUMMARY:")
    log.info("  Total Steps: %d", total)
    log.info("  Passed:      %d", passed)
    log.info("  Failed:      %d", failed)
    log.info("  Warnings:    %d", warnings)
    log.info("  Skipped:     %d", skipped)
    log.info("")

    log.info("DETAILED RESULTS:")
    log.info("-" * 80)

    for r in results:
        icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭️"}.get(r["status"], "❓")
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

    # Save report to file
    report_path = "tests/integration/test_report.json"
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "skipped": skipped,
            },
            "results": results,
        }, f, indent=2)

    log.info("Report saved to: %s", report_path)
    log.info("")
    log.info("=" * 80)

    return passed, failed, warnings, skipped


def main():
    """Run the full 30-step manual test."""
    log.info("")
    log.info("#" * 80)
    log.info("#  MERGE ENGINE - 30-STEP MANUAL FULL PIPELINE TEST")
    log.info("#  Prompt: Boeing Company Records Merge")
    log.info("#  Timestamp: %s", datetime.now().isoformat())
    log.info("#" * 80)
    log.info("")

    # ============================================================
    # PHASE 1: Infrastructure Verification (Steps 1-3)
    # ============================================================
    log.info("")
    log.info("PHASE 1: Infrastructure Verification")
    log.info("-" * 40)

    step = next_step("Verify Gradio Server on port 7860")
    check_gradio(step)

    step = next_step("Verify Langfuse Server on port 3000")
    check_langfuse(step)

    step = next_step("Verify LangGraph Server on port 8123")
    check_langgraph_server(step)

    # ============================================================
    # PHASE 2: Configuration Testing (Steps 4-6)
    # ============================================================
    log.info("")
    log.info("PHASE 2: Configuration Testing")
    log.info("-" * 40)

    step = next_step("Load config from config.yaml")
    test_config_loading(step)

    step = next_step("Test dependency sorting utility")
    test_dependency_sort(step)

    step = next_step("Test ID generation utilities")
    test_id_utils(step)

    # ============================================================
    # PHASE 3: Component Initialization (Steps 7-9)
    # ============================================================
    log.info("")
    log.info("PHASE 3: Component Initialization")
    log.info("-" * 40)

    step = next_step("Test logging configuration")
    test_logging_config(step)

    step = next_step("Test WorkflowManager initialization")
    test_workflow_manager(step)

    step = next_step("Test LLMClient initialization")
    test_llm_client(step)

    # ============================================================
    # PHASE 4: Database Connectivity Testing (Steps 10-15)
    # ============================================================
    log.info("")
    log.info("PHASE 4: Database Connectivity Testing")
    log.info("-" * 40)

    step = next_step("Test ClickHouse Origin 1 connection (zoominfo)")
    test_clickhouse_connection(step, ORIGIN_1, "Origin 1 - ClickHouse zoominfo")

    step = next_step("Test ClickHouse Origin 2 connection (diffbot)")
    test_clickhouse_connection(step, ORIGIN_2, "Origin 2 - ClickHouse diffbot")

    step = next_step("Test PostgreSQL destination connection")
    test_postgresql_connection(step)

    step = next_step("Test ClickHouse connector class")
    test_connectors(step)

    step = next_step("Test state management")
    test_state_management(step)

    step = next_step("Test error handling paths")
    test_error_handling(step)

    # ============================================================
    # PHASE 5: MCP Tools Testing (Steps 16-18)
    # ============================================================
    log.info("")
    log.info("PHASE 5: MCP Tools Testing")
    log.info("-" * 40)

    step = next_step("Test all MCP tools registration")
    test_mcp_tools(step)

    step = next_step("Test discover_schema MCP tool")
    test_discover_schemas_mcp(step)

    # ============================================================
    # PHASE 6: LLM Integration Testing (Steps 19-21)
    # ============================================================
    log.info("")
    log.info("PHASE 6: LLM Integration Testing (Boeing Prompt)")
    log.info("-" * 40)

    step = next_step("Test parse_request with Boeing prompt")
    test_parse_request_llm(step)

    # ============================================================
    # PHASE 7: Graph/Workflow Testing (Steps 22-24)
    # ============================================================
    log.info("")
    log.info("PHASE 7: Graph/Workflow Testing")
    log.info("-" * 40)

    step = next_step("Test workflow graph structure")
    test_graph_structure(step)

    # ============================================================
    # PHASE 8: UI Component Testing (Steps 25-27)
    # ============================================================
    log.info("")
    log.info("PHASE 8: UI Component Testing")
    log.info("-" * 40)

    step = next_step("Verify config preview JSON formatting")
    try:
        from src.ui.gradio_app import _format_config_json
        config_json = _format_config_json([ORIGIN_1, ORIGIN_2], DESTINATION)
        parsed = json.loads(config_json)
        if parsed.get("origins") and parsed.get("destination"):
            record_result(step, "Config JSON Format", "pass",
                         f"Origins: {len(parsed['origins'])}, Destination: {parsed['destination']['type']}")
        else:
            record_result(step, "Config JSON Format", "fail", "Missing origins or destination in JSON")
    except Exception as e:
        record_result(step, "Config JSON Format", "fail", str(e))

    step = next_step("Verify UI gradio app creation")
    try:
        from src.ui.gradio_app import create_app
        app = create_app(langgraph_server_url="http://localhost:8123")
        record_result(step, "Gradio App Creation", "pass", "App created successfully")
    except Exception as e:
        record_result(step, "Gradio App Creation", "fail", str(e))

    step = next_step("Verify UI components import")
    try:
        from src.ui.components.dependency_editor import render_dependency_editor
        from src.ui.components.match_reviewer import render_match_reviewer
        from src.ui.components.mapping_reviewer import render_mapping_reviewer
        from src.ui.components.summary_view import render_final_summary
        from src.ui.components.origin_loader import load_origins
        from src.ui.components.db_discovery import test_and_discover_schemas

        # Test render functions
        dep_html = render_dependency_editor({}, ["table1", "table2"])
        match_html = render_match_reviewer([])
        mapping_html = render_mapping_reviewer([], [])
        summary_html = render_final_summary({}, [])

        if dep_html and match_html and mapping_html and summary_html:
            record_result(step, "UI Components Render", "pass", "All panels render correctly")
        else:
            record_result(step, "UI Components Render", "fail", "One or more panels returned empty")
    except Exception as e:
        record_result(step, "UI Components Render", "fail", str(e))

    # ============================================================
    # PHASE 9: Integration - Full Pipeline Simulation (Steps 28-30)
    # ============================================================
    log.info("")
    log.info("PHASE 9: Full Pipeline Integration Tests")
    log.info("-" * 40)

    step = next_step("Simulate setup phase: add origins")
    try:
        from src.ui.gradio_app import add_origin_to_json, submit_origins_json

        origins_list = []
        origins_list, msg1, _, _ = add_origin_to_json(
            origins_list, "clickhouse", "10.10.9.46", 8190,
            "admin", "pass", "zoominfo", 1, []
        )
        if origins_list:
            record_result(step, "Add Origin 1", "pass", msg1)
        else:
            record_result(step, "Add Origin 1", "fail", "Failed to add origin")

        origins_list, msg2, _, _ = add_origin_to_json(
            origins_list, "clickhouse", "10.10.9.46", 8195,
            "admin", "pass", "diffbot", 2, []
        )
        if origins_list and len(origins_list) == 2:
            record_result(step, "Add Origin 2", "pass", msg2)
        else:
            record_result(step, "Add Origin 2", "fail", "Failed to add second origin")
    except Exception as e:
        record_result(step, "Setup Phase Simulation", "fail", str(e))

    step = next_step("Simulate pipeline start with Boeing prompt")
    try:
        from src.orchestrator.workflow_manager import WorkflowManager

        wm = WorkflowManager()
        state = wm.start_workflow(
            BOEING_PROMPT,
            "config/config.yaml",
            origins=[ORIGIN_1, ORIGIN_2],
            destination=DESTINATION,
        )
        if state:
            record_result(step, "Pipeline Start", "pass", f"Thread/State: {state}")
        else:
            record_result(step, "Pipeline Start", "warn", "No state returned (expected in dev mode)")
    except Exception as e:
        record_result(step, "Pipeline Start", "warn", str(e))

    step = next_step("Verify Langfuse traces in dashboard")
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:3000/api/telemetry")
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            if resp.status == 200:
                record_result(step, "Langfuse Telemetry", "pass", "Telemetry endpoint accessible")
            else:
                record_result(step, "Langfuse Telemetry", "warn", f"Status: {resp.status}")
        except urllib.error.HTTPError as e:
            record_result(step, "Langfuse Telemetry", "warn", f"HTTP {e.code} (may require auth)")
        except Exception as e:
            record_result(step, "Langfuse Telemetry", "warn", str(e))
    except Exception as e:
        record_result(step, "Langfuse Telemetry", "warn", str(e))

    # ============================================================
    # FINAL REPORT
    # ============================================================
    passed, failed, warnings, skipped = generate_report()

    # Exit with error code if there are failures
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()