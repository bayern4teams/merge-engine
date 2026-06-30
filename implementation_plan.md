# Implementation Plan

[Overview]
Comprehensive logging infrastructure overhaul with extensive DEBUG-level logging at every step of the merge pipeline, combined with fixes for the UnboundLocalError in `poll_status()` and blocking HTTP calls in schema discovery. This will generate hundreds of log lines showing every state change, API call, LLM interaction, connector operation, and error — all written to both console and `logs/debug.log`.

The plan covers: (1) Enhancing the logging infrastructure with dual handlers (console + file), (2) Adding DEBUG logging to every orchestrator node, server manager, LLM client, Gradio app, and connector, (3) Fixing the `UnboundLocalError` for `_pipeline_steps` in `poll_status()`, (4) Adding `--allow-blocking` to the LangGraph CLI start command.

[Types]
No new types introduced. The plan uses existing `logging.Logger` types, `logging.FileHandler`, `logging.StreamHandler`, and `logging.Formatter` from Python's stdlib. The existing `setup_logging()` function in `src/utils/logging_config.py` will be enhanced with optional file handler support.

[Files]
- **`src/utils/logging_config.py`** — MODIFY: Add `log_dir` parameter for automatic log directory creation. Enhance to support both DEBUG console output and file logging with full timestamps, module names, and line numbers.
- **`src/main.py`** — MODIFY: Change `setup_logging()` call to use DEBUG level by default when `--log-level` is not specified. Add `log_file="logs/debug.log"` parameter. Print "=== DEBUG LOGGING ENABLED ===" at startup.
- **`src/orchestrator/nodes/parse_request.py`** — MODIFY: Add DEBUG logs at entry/exit, log state keys, log LLM prompt content (truncated), log LLM response, log parsed entities, log errors with full context.
- **`src/orchestrator/nodes/analyze_dependencies.py`** — MODIFY: Add DEBUG logs at entry/exit, log target_entities, log schema_summary content, log LLM prompt/response, log dependency_tree and extraction_order results.
- **`src/orchestrator/nodes/discover_schemas.py`** — MODIFY: Add DEBUG logs at entry/exit, log each origin being processed, log schema result per origin (table count, table names), log errors with full exception details.
- **`src/orchestrator/nodes/extract_data.py`** — MODIFY: Add DEBUG logs at entry/exit, log extraction order, log each origin/table extraction, log batch sizes and row counts, log errors.
- **`src/orchestrator/nodes/resolve_entities.py`** — MODIFY: Add DEBUG logs at entry/exit, log entity clusters, log confidence scores, log low-confidence clusters, log LLM prompt/response.
- **`src/orchestrator/nodes/unify_entities.py`** — MODIFY: Add DEBUG logs at entry/exit, log unified entity groups, log match decisions.
- **`src/orchestrator/nodes/map_fields.py`** — MODIFY: Add DEBUG logs at entry/exit, log field mappings created, log unmapped fields, log LLM prompt/response.
- **`src/orchestrator/nodes/load_data.py`** — MODIFY: Add DEBUG logs at entry/exit, log load operations per table, log row counts, log errors.
- **`src/orchestrator/nodes/human_nodes.py`** — MODIFY: Add DEBUG logs for each HITL checkpoint (which node triggered, what data is available).
- **`src/orchestrator/server_manager.py`** — MODIFY: Add DEBUG logs for thread creation, run creation, status polling, run results, resume/abort operations. Log every SDK API call and response.
- **`src/llm/client.py`** — MODIFY: Add DEBUG logs for model initialization, provider selection, every LLM call with full prompt and response content.
- **`src/ui/gradio_app.py`** — MODIFY: Fix `UnboundLocalError` for `_pipeline_steps`. Add DEBUG logs for every UI action, state changes, and workflow events.
- **`src/mcp_server/connectors/clickhouse.py`** — MODIFY: Add DEBUG logs for connection, query execution, result fetching.
- **`src/mcp_server/connectors/postgres.py`** — MODIFY: Add DEBUG logs for connection, query execution, result fetching.
- **`src/mcp_server/tools/discover_schema.py`** — MODIFY: Add DEBUG logs for schema discovery steps.
- **`logs/debug.log`** — CREATE (at runtime): Log file for all DEBUG+ level output.

[Functions]
- **`src/utils/logging_config.setup_logging()`** — MODIFY: Accept new `log_dir` parameter (default="logs"). Create log directory if not exists. Always add file handler when `log_file` is set. Add verbose console formatter with `[%(levelname)-8s] %(asctime)s %(name)s:%(lineno)d — %(message)s` format.
- **`src/main.main()`** — MODIFY: Change default `--log-level` to "DEBUG". Add `--log-file` argument (default="logs/debug.log"). Print `=== MERGE ENGINE DEBUG MODE ===` banner at startup with config path, log file path, and server URLs.
- **`src/ui.gradio_app.poll_status()`** — MODIFY: Fix `UnboundLocalError` by using `global _pipeline_steps` declaration at the top of the function, OR restructure the function to avoid reassigning the global. Replace `for ps in _pipeline_steps:` with a locally-scoped variable approach.
- **`src.ui.gradio_app.start_workflow()`** — MODIFY: Add DEBUG log for state validation, thread creation, and initial pipeline setup.
- **`src.orchestrator.server_manager.start_workflow()`** — MODIFY: Add DEBUG log for initial_state content, thread create response, run create response.
- **`src.orchestrator.server_manager.get_workflow_status()`** — MODIFY: Add DEBUG log for state values, run status, current node determination, and final result.
- **`src.orchestrator.server_manager.resume_workflow()`** — MODIFY: Add DEBUG log for feedback submission.
- **`src.orchestrator.server_manager.abort_workflow()`** — MODIFY: Add DEBUG log for cancellation.

[Classes]
No new classes. Existing classes (`LLMClient`, `LangGraphServerManager`, `ChatState`) get enhanced logging in their methods.

[Dependencies]
No new package dependencies. Uses Python stdlib `logging`, `pathlib.Path`, `sys`, `time`. The existing `structlog` dependency remains for structured logging but will be supplemented with stdlib DEBUG logging.

[Testing]
Manual testing only: Run `python -m src.main --mode gradio --port 7860 --config config/config.yaml --start-langgraph` and verify:
1. Console shows verbose DEBUG output with timestamps, module names, and line numbers
2. `logs/debug.log` file is created and populated with hundreds of log lines
3. Every pipeline step (parse_request → discover_schemas → analyze_dependencies → ...) logs entry, processing, and exit
4. `UnboundLocalError` no longer occurs when polling status
5. Schema discovery errors are clearly visible in logs with full stack traces
6. Langfuse traces appear if LANGFUSE_ENABLED=true and server is running

[Implementation Order]
1. Enhance `src/utils/logging_config.py` with file handler support
2. Update `src/main.py` to use DEBUG level and log file
3. Fix `UnboundLocalError` in `src/ui/gradio_app.py`
4. Add DEBUG logging to orchestrator nodes (parse_request, discover_schemas, analyze_dependencies, extract_data, resolve_entities, unify_entities, map_fields, load_data, human_nodes)
5. Add DEBUG logging to `src/orchestrator/server_manager.py`
6. Add DEBUG logging to `src/llm/client.py`
7. Add DEBUG logging to Gradio app (`src/ui/gradio_app.py`)
8. Add DEBUG logging to connectors and MCP tools