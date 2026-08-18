# Files

- [discover_schema Tool](discover_schema.md) - MCP tool that connects to an origin database (PostgreSQL, ClickHouse, MongoDB) and returns all tables/collections with column names, types, one sample value per column, and foreign keys.
- [extend_schema Tool](extend_schema.md) - MCP tool that adds new columns to Databricks destination tables for unmapped fields requiring schema extension. Uses the DatabricksConnector's extend_schema_batch method.
- [extract_data Tool](extract_data.md) - MCP tool that extracts rows from an origin database using batch pagination (OFFSET/LIMIT for most sources, keyset for PostgreSQL), with optional watermark pushdown and filter application.
- [Fellegi-Sunter Weight Estimation](fellegi_sunter.md) - Probabilistic record linkage weight estimation based on the Fellegi-Sunter model. Estimates match/non-match probabilities for each field comparison to inform blocking thresholds.
- [load_data Tool](load_data.md) - MCP tool that upserts unified records into destination databases (PostgreSQL or ClickHouse), handling foreign key translation, value coercion, and conflict tracking with provenance metadata.
- [map_fields Tool](map_fields.md) - MCP tool that maps unified entity fields to destination database columns using hybrid heuristic scoring (similarity ≥ 0.85) with LLM fallback for borderline cases. Supports retry with human feedback.
- [Plan Scope Tool](plan_scope.md) - MCP tool for scoped merge planning — per-entity extraction filters for subject scoping (e.g. "all information about Nova").

- [resolve_entities Tool](resolve_entities.md) - MCP tool that clusters matching records across origins using deterministic blocking, heuristic scoring (Jaccard/difflib), LLM scoring for borderline cases, and Union-Find clustering. Supports cross-run caching via DuckDB-backed resolve cache.
- [unify_entity_group Tool](unify_entity_group.md) - MCP tool that merges matched record clusters into unified records, applying a conflict resolution policy (priority-based → completeness-based → recency-based) and producing provenance metadata.
