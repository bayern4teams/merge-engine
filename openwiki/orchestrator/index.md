# Files

- [State Graph Definition](graph.md) - Complete LangGraph StateGraph definition for merge-engine, including all 17 nodes, edges, conditional edges, retry policies, gate routers, and the MergeState TypedDict schema.
- [Orchestrator Overview](overview.md) - LangGraph StateGraph-based pipeline orchestrator for merge-engine, comprising 17 nodes (12 functional + 5 HITL gates) wired with retry policies, conditional edges, and cross-cutting timing instrumentation.
- [Saved Run Profiles](profiles.md) - Saved run profiles pin the LLM's non-deterministic metadata decisions (entity_tables, matching_rules, conflict_policy, field_mappings) so re-runs on identical data produce identical results.
- [Reconciliation Reports](reconciliation_reports.md) - Reconciliation reports and scoped merge guardrails: per-entity extraction/clustering/conflict/load numbers, data quality grading, and the pre-load guardrail that prevents duplicate loading.

- [DuckDB Spool](spool.md) - Per-run DuckDB file for extracted rows, replacing in-memory lists that blow up checkpoint size. Supports blocking/scoring as SQL against the same file.
- [Workflow Manager](workflow_manager.md) - Workflow lifecycle manager for merge-engine covering start, pause, resume, status, and abort operations with SQLite checkpoint persistence, profile integration, HITL resume mechanics, and error gate bookkeeping.

# Directories

- [nodes](nodes/)
- [utils](utils/)
