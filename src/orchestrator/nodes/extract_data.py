"""
extract_data node: Extracts data for all entities in dependency order.

Parses the dependency tree and target schemas to determine which tables
from which origins contain the target entity, then calls extract_data
in batches until all rows are fetched.
"""

from __future__ import annotations

import logging
from typing import Any

from src.mcp_server.tools.extract_data import extract_data_impl
from src.orchestrator.utils.dependency_sort import find_execution_groups
from src.orchestrator.utils.retry import TransientError

logger = logging.getLogger("merge-engine.orchestrator.extract_data")


async def extract_data(state: dict[str, Any]) -> dict[str, Any]:
    """Extract data for all entities in dependency order.

    Parameters
    ----------
    state : dict
        MergeState with ``extraction_order``, ``schemas``, ``origins``.

    Returns
    -------
    dict
        Update dict with ``extracted_data`` key.
    """
    logger.debug("ENTER extract_data")
    logger.debug("State keys: %s", list(state.keys()))
    extraction_order = state.get("extraction_order", [])
    schemas = state.get("schemas", {})
    origins = state.get("origins", [])
    extraction_config = state.get("extraction_config", {})
    errors: list[str] = list(state.get("errors", []))
    extracted_data: dict[str, list[dict]] = dict(state.get("extracted_data", {}))

    default_batch_size = extraction_config.get("default_batch_size", 1000)
    logger.debug("extraction_order: %s", extraction_order)
    logger.debug("default_batch_size: %d", default_batch_size)

    # Group entities that can be extracted in parallel
    dep_tree = {e: state.get("dependency_tree", {}).get(e, []) for e in extraction_order}
    execution_groups = find_execution_groups(dep_tree)
    logger.debug("Execution groups: %d groups", len(execution_groups))

    for group_idx, group in enumerate(execution_groups):
        logger.debug("Processing execution group %d: %s", group_idx, group)
        for entity_type in group:
            logger.info("Extracting entity type: %s", entity_type)

            # Determine which origin tables contain this entity
            table_candidates = _find_entity_tables(entity_type, schemas)
            logger.debug("Found %d table candidates for entity '%s'", len(table_candidates), entity_type)
            for tc in table_candidates:
                logger.debug("  Candidate: %s (%s)", tc["table_name"], tc["origin_id"])

            if not table_candidates:
                msg = f"No tables found for entity '{entity_type}' in any origin."
                logger.warning(msg)
                errors.append(msg)
                continue

            all_rows: list[dict] = []

            for candidate in table_candidates:
                origin_id = candidate["origin_id"]
                table_name = candidate["table_name"]
                columns = [c["name"] for c in candidate.get("columns", [])]

                if not columns:
                    columns = ["*"]

                logger.debug("Extracting from %s:%s (columns=%s, batch_size=%d)", origin_id, table_name, columns[:5], default_batch_size)

                # Extract all batches
                cursor = None
                total_fetched = 0
                batch_num = 0

                while True:
                    batch_num += 1
                    try:
                        logger.debug("  Batch %d for %s:%s, cursor=%s", batch_num, origin_id, table_name, cursor)
                        result = await extract_data_impl(
                            origin_id=origin_id,
                            table_name=table_name,
                            columns=columns,
                            batch_size=default_batch_size,
                            cursor=cursor,
                            filter={"_imported": False},
                        )

                        batch_rows = result.get("rows", [])
                        all_rows.extend(batch_rows)
                        total_fetched += len(batch_rows)
                        logger.debug("  Batch %d returned %d rows, total=%d, next_cursor=%s", batch_num, len(batch_rows), total_fetched, result.get("next_cursor"))

                        # Tag rows with origin metadata
                        for row in batch_rows:
                            row["_origin_id"] = origin_id
                            row["_origin_table"] = table_name

                        cursor = result.get("next_cursor")
                        if cursor is None:
                            break

                    except TransientError as exc:
                        msg = f"Transient error extracting {origin_id}:{table_name}: {exc}"
                        logger.error(msg, exc_info=True)
                        errors.append(msg)
                        break
                    except Exception as exc:
                        msg = f"Extraction failed for {origin_id}:{table_name}: {exc}"
                        logger.error(msg, exc_info=True)
                        errors.append(msg)
                        break

                logger.info(
                    "Extracted %d rows from %s:%s for entity '%s'",
                    total_fetched,
                    origin_id,
                    table_name,
                    entity_type,
                )

            extracted_data[entity_type] = all_rows
            logger.debug("Entity '%s' total rows: %d", entity_type, len(all_rows))

    logger.debug("Total entities in extracted_data: %d", len(extracted_data))
    for et, rows in extracted_data.items():
        logger.debug("  Entity '%s': %d rows", et, len(rows))
    logger.debug("Total errors: %d", len(errors))
    logger.debug("EXIT extract_data")

    return {
        "extracted_data": extracted_data,
        "errors": errors,
        "origins": state.get("origins", []),
        "destination": state.get("destination", {}),
        "llm_config": state.get("llm_config", {}),
        "extraction_config": state.get("extraction_config", {}),
        "target_entities": state.get("target_entities", []),
        "schemas": state.get("schemas", {}),
        "dependency_tree": state.get("dependency_tree", {}),
        "extraction_order": state.get("extraction_order", []),
        "entity_clusters": state.get("entity_clusters", {}),
        "unified_entities": state.get("unified_entities", {}),
        "field_mappings": state.get("field_mappings", {}),
        "pending_schema_changes": state.get("pending_schema_changes", []),
        "human_feedback": state.get("human_feedback"),
        "load_approval": state.get("load_approval"),
        "retry_counts": state.get("retry_counts", {}),
        "interrupt_data": None,
    }


def _find_entity_tables(
    entity_type: str, schemas: dict[str, Any]
) -> list[dict[str, Any]]:
    """Find tables that plausibly contain the given entity type.

    Returns list of {origin_id, table_name, columns, full_name}.
    """
    candidates: list[dict[str, Any]] = []

    for origin_id, schema_data in schemas.items():
        for table in schema_data.get("tables", []):
            table_name = table.get("name", "")
            full_name = table.get("full_name", table_name)

            if _table_matches_entity(table_name, full_name, entity_type):
                candidates.append({
                    "origin_id": origin_id,
                    "table_name": full_name,
                    "columns": table.get("columns", []),
                    "foreign_keys": table.get("foreign_keys", []),
                })

    return candidates


def _table_matches_entity(table_name: str, full_name: str, entity_type: str) -> bool:
    """Heuristic: does a table name match an entity type?"""
    t1 = table_name.lower().replace("_", " ").replace("-", " ")
    t2 = full_name.lower().replace("_", " ").replace("-", " ").split(".")[-1]

    for name in (t1, t2):
        if entity_type in name or name in entity_type:
            return True
        # Plural forms
        if entity_type + "s" in name:
            return True
        if entity_type + "es" in name:
            return True

    return False