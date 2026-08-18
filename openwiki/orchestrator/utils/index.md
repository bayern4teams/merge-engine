# Files

- [Cluster Edits](cluster_edits.md) - Applies HITL reviewer decisions (keep/split/reject) to entity clusters stored in the DuckDB spool. Called from the match review gate.
- [Dependency Sort](dependency_sort.md) - Topological sort implementation (Kahn's algorithm), execution group finding, extraction order validation, and dependency tree analysis for the merge pipeline.
- [Entity Aliases](entity_aliases.md) - Business concept to table-name matching. Maps canonical entity types (company, person, product, etc.) to their possible table names via alias groups. Used by dependency analysis and load_data table resolution.
- [Entity Types](entity_types.md) - Canonical entity type definitions used throughout merge-engine. Defines the universe of supported business entity types and their metadata.
- [Error Handling](error_handling.md) - Typed error taxonomy for merge-engine, including the MergeEngineError base class with TransientError, PermanentError, DataQualityError, and UserInputError subclasses. Exception classification drives per-node retry policy and error gate routing.
- [Resolve Cache](resolve_cache.md) - Cross-run resolution result cache using DuckDB persistence. Prevents re-computing matching decisions after a downstream failure by caching results keyed on a deterministic content hash.
- [Retry Hints](retry_hints.md) - Bridges HITL user feedback into LLM prompts. When a reviewer sends a gate back with "retry", their written reason is stored and appended to the producing node's LLM prompt.
- [Entity Table Selection](select_entity_tables.md) - LLM-based selection of which source tables hold each target entity. Called by analyze_dependencies orchestrator node (not an MCP tool).

