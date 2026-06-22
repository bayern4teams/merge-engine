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
    extraction_order = state.get("extraction_order", [])
    schemas = state.get("schemas", {})
    origins = state.get("origins", [])
    extraction_config = state.get("extraction_config", {})
    errors: list[str] = list(state.get("errors", []))
    extracted_data: dict[str, list[dict]] = dict(state.get("extracted_data", {}))

    default_batch_size = extraction_config.get("default_batch_size", 1000)

    # Group entities that can be extracted in parallel
    execution_groups = find_execution_groups(
        {e: state.get("dependency_tree", {}).get(e, []) for e in extraction_order}
    )

    for group in execution_groups:
        for entity_type in group:
            logger.info("Extracting entity type: %s", entity_type)

            # Determine which origin tables contain this entity
            table_candidates = _find_entity_tables(entity_type, schemas)

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

                # Extract all batches
                cursor = None
                total_fetched = 0

                while True:
                    try:
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

                        # Tag rows with origin metadata
                        for row in batch_rows:
                            row["_origin_id"] = origin_id
                            row["_origin_table"] = table_name

                        cursor = result.get("next_cursor")
                        if cursor is None:
                            break

                    except TransientError as exc:
                        msg = f"Transient error extracting {origin_id}:{table_name}: {exc}"
                        logger.error(msg)
                        errors.append(msg)
                        break
                    except Exception as exc:
                        msg = f"Extraction failed for {origin_id}:{table_name}: {exc}"
                        logger.error(msg)
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

    return {
        "extracted_data": extracted_data,
        "errors": errors,
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