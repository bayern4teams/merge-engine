# Glossary

One fixed term per concept. Every entry gives a one-line definition and
where the term lives in code (`path::symbol`). Other documents must use
these words and no synonyms (rule J5).

<!-- verified: 4d33e0c -->

| Term | Definition | Lives in |
|---|---|---|
| run | One execution of the merge graph, identified by a `run_id` (= LangGraph `thread_id`). | `src/orchestrator/graph.py::get_initial_state` |
| origin | A source database a run reads from (PostgreSQL, ClickHouse, or MongoDB). | `src/mcp_server/connectors/base.py` |
| destination | The single database a run writes to; exactly one per run. | `src/mcp_server/connectors/postgresql_destination.py`, `src/mcp_server/connectors/clickhouse_destination.py` |
| HITL gate | A human-in-the-loop pause where the graph stops and an operator approves, edits, retries, or rejects. | `src/orchestrator/graph.py::HITL_NODES` |
| merge request | The user's plain-English description of what to merge, parsed by the LLM into entity types. | `src/orchestrator/nodes/parse_request.py` |
| entity type | A business object the merge reasons about (`person`, `company`, …), not a table. | `src/mcp_server/tools/select_entity_tables.py::select_entity_tables_impl` |
| entity map | The destination table recording which source record became which canonical entity id. | `src/mcp_server/connectors/postgresql_destination.py::ensure_er_side_tables` (docstring) |
| candidate pair | Two extracted records worth comparing for same-entity identity. | `src/mcp_server/tools/resolve_entities.py::resolve_entities_impl` |
| blocking | The lossy-by-design step that narrows all-pairs down to candidate pairs (token blocks, filtering, purging, WNP meta-blocking). | `src/mcp_server/tools/resolve_entities.py::_generate_candidate_pairs` |
| meta-blocking (WNP) | Weighted-node-pair pruning: a candidate edge survives when its shared-block weight reaches the mean at either endpoint. | `src/mcp_server/tools/resolve_entities.py::_wnp_prune` |
| Fellegi–Sunter model | The probabilistic match model: per-field m (agree given match) and u (agree given non-match), summed as log2 odds. | `src/mcp_server/tools/fellegi_sunter.py::MatchModel` |
| term frequency | How often a value occurs in an entity type; used as `u` for exact agreements so rare agreement counts more. | `src/mcp_server/tools/fellegi_sunter.py::build_term_frequencies` |
| comparison level | How two field values compare: exact, strong, weak, disagree, or null (null contributes zero evidence). | `src/mcp_server/tools/fellegi_sunter.py::FieldModel` |
| auto-match / auto-exclude thresholds | The score bars at 0.95 and 0.70: above, below, and (between) the human-review band. | `src/mcp_server/tools/resolve_entities.py::_AUTO_MATCH` |
| band | The score region between the two thresholds; pairs there are never auto-merged, they go to the matches gate. | `src/mcp_server/tools/resolve_entities.py::rebuild_band_clusters` |
| cluster | A set of candidate records resolved to one real-world entity; id is a deterministic sha256 of its sorted member ids. | `src/mcp_server/tools/resolve_entities.py::_build_clusters` |
| unification | Merging a cluster's records into one destination record, field by field. | `src/mcp_server/tools/unify_entity_group.py::unify_entity_group_impl` |
| survivorship | Which conflicting field value survives unification, per a per-field rule chain. | `src/mcp_server/tools/unify_entity_group.py::resolve_policy` |
| conflict policy | The configurable per-field rule chain (`priority`, `longest`, `recency`, `shortest`, `first_seen`, numeric rules). | `src/mcp_server/tools/unify_entity_group.py::resolve_policy` |
| field mapping | The assignment of unified source fields to destination columns; `map`/`skip` only — new columns are refused. | `src/mcp_server/tools/map_fields.py::map_fields_impl` |
| unmapped field | A unified field with no confident destination column; it triggers the mapping review gate. | `src/orchestrator/nodes/map_fields_node.py::has_unmapped_fields` |
| spool | The per-run DuckDB file holding extracted rows outside graph state. | `src/orchestrator/spool.py::RunSpool` |
| keyset pagination | Seek-style extraction (`>=` boundary plus a hash tiebreak), replacing OFFSET, so no row is skipped or repeated. | `src/mcp_server/connectors/keyset.py` |
| watermark | The per `{origin}:{table}` last-seen value that makes a later run incremental. | `src/mcp_server/connectors/watermark.py` |
| preflight | The first node: seconds-scale reachability and permission checks before extraction starts. | `src/orchestrator/nodes/preflight.py` |
| error taxonomy | The four exception classes (`TransientError`, `PermanentError`, `DataQualityError`, `UserInputError`) and their dispositions. | `src/orchestrator/utils/retry.py::classify_exception` |
| error gate | The HITL stop that shows a failure and routes on the operator's retry/skip/abort decision. | `src/orchestrator/nodes/human_nodes.py::handle_error` |
| reconciliation report | The per-run account of rows in, rows out, conflicts, and refusals. | `src/orchestrator/nodes/load_data.py::build_reconciliation_report` |
| pre-load gate | Invariants measured on the run itself that can refuse a load before anything is written. | `src/orchestrator/nodes/load_data.py::pre_load_gate` |
| run profile | A saved JSON pinning the four non-deterministic LLM decisions so a re-run is reproducible. | `src/orchestrator/profiles.py::PINNED_KEYS` |
| semantic cache | A keyed (model, temperature, prompts, format) LRU of LLM responses inside the client. | `src/llm/cost.py::SemanticCache` |
| cost ledger | Per-run token accounting (tokens, not currency), including cache savings. | `src/llm/cost.py::CostLedger` |
| scoped merge | A "merge everything about X" request: the LLM plans per-entity link paths and a scope gate confirms the subject. | `src/mcp_server/tools/plan_scope.py::plan_scope_impl` |
| ER service | The online `/v1` entity-resolution API for the Enrichment Engine, sharing the destination DB with batch runs. | `src/orchestrator/er_service.py`, `src/api/er_routes.py::build_er_router` |
| enrichment record | A record submitted through `/v1`, treated as one more origin with the lowest survivorship priority. | `src/orchestrator/er_hooks.py::inject_enrichment_records` |
| decision memory | A file of past band-gate answers, replayed so an identical later question is not asked again. | `src/orchestrator/decision_memory.py` |
| checkpoint | The LangGraph SQLite record of state after every node — what lets gates survive an app restart. | `src/orchestrator/graph.py::build_graph` |
| golden | The hand-merged reference output the app's result is diffed against. | `tests/eval/golden.py` |
| ground truth | The independently built, byte-reproducible answer key a run is scored against. | `docs/verification/GROUND_TRUTH_BUILD_2026-09-24_full.md` |
