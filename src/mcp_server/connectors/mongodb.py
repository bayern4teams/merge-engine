"""
MongoDB connector for schema discovery, data extraction, and import flagging.

Uses PyMongo for async driver access.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.server_api import ServerApi

from src.mcp_server.connectors.base import BaseConnector
from src.orchestrator.utils.retry import TransientError, retry_on_transient

logger = logging.getLogger("merge-engine.connectors.mongodb")


class MongoDBConnector(BaseConnector):
    """MongoDB database connector."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._client: AsyncMongoClient | None = None
        self._db_name: str = "admin"

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def _ensure_client(self) -> AsyncMongoClient:
        """Create or return the async PyMongo client."""
        logger.debug("ENTER _ensure_client")
        if self._client is not None:
            logger.debug("Client already exists, returning")
            return self._client

        conn = self.config.get("connection", {})
        uri = conn.get("uri") or ""

        # Extract database from URI if not set in config
        if not uri:
            logger.debug("No URI provided, raising ValueError")
            raise ValueError("MongoDB connection URI is required")

        # Try to parse database from URI
        if not self._db_name or self._db_name == "admin":
            # Parse dbname from mongodb://host:port/dbname
            parts = uri.split("/")
            if len(parts) >= 4:
                self._db_name = parts[3].split("?")[0].split("&")[0]
                logger.debug("Parsed database from URI: %s", self._db_name)

        logger.debug(
            "Creating MongoDB async client: uri=%s:%s, db=%s",
            uri[:50] + "..." if len(uri) > 50 else uri,
            "...", self._db_name,
        )
        self._client = AsyncMongoClient(
            uri,
            server_api=ServerApi("1"),
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
        )
        logger.debug("MongoDB async client created successfully")
        return self._client

    async def close(self) -> None:
        logger.debug("ENTER close")
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.debug("MongoDB client closed")

    # ── Schema Discovery ──────────────────────────────────────────────

    async def discover_schema(self) -> dict[str, Any]:
        """Sample documents from each collection to infer schema."""
        logger.debug("ENTER discover_schema")
        client = await self._ensure_client()

        tables: list[dict[str, Any]] = []

        try:
            db = client[self._db_name]
            collection_names = await db.list_collection_names()
            logger.debug("Found %d collections in database %s", len(collection_names), self._db_name)
        except Exception as exc:
            logger.warning("Failed to list MongoDB collections: %s", exc)
            logger.debug("EXIT discover_schema - returning empty tables")
            return {"tables": []}

        for coll_name in sorted(collection_names):
            logger.debug("Discovering schema for collection: %s", coll_name)
            try:
                collection = db[coll_name]
                sample_count = min(100, 100)  # Sample up to 100 docs
                logger.debug("Sampling up to %d documents from %s", sample_count, coll_name)

                # Fetch sample documents
                samples = []
                cursor = collection.find({}).limit(sample_count)
                async for doc in cursor:
                    samples.append(doc)
                logger.debug("Fetched %d sample documents from %s", len(samples), coll_name)

                if not samples:
                    tables.append({
                        "name": coll_name,
                        "columns": [],
                        "foreign_keys": [],
                        "sample_row": {},
                    })
                    logger.debug("Collection %s is empty, skipping schema inference", coll_name)
                    continue

                # Infer flat schema from samples
                fields: dict[str, dict[str, Any]] = {}
                for doc in samples:
                    for key, val in doc.items():
                        if key not in fields:
                            fields[key] = {
                                "name": key,
                                "types": set(),
                                "nullable": True,
                            }
                        if val is not None:
                            fields[key]["nullable"] = False
                            fields[key]["types"].add(self._infer_type(val))
                        else:
                            fields[key]["nullable"] = True

                columns: list[dict[str, Any]] = [
                    {
                        "name": f["name"],
                        "type": ",".join(sorted(f["types"])) if f["types"] else "UNKNOWN",
                        "nullable": f["nullable"],
                    }
                    for f in fields.values()
                ]
                logger.debug("Inferred %d fields for collection %s", len(columns), coll_name)

                # Sample row (first document, flattened)
                sample_row = self._flatten_doc(samples[0])

                tables.append({
                    "name": coll_name,
                    "columns": columns,
                    "foreign_keys": [],  # MongoDB doesn't have native FKs
                    "sample_row": sample_row,
                })
            except Exception as exc:
                logger.warning("Failed to discover schema for collection %s: %s", coll_name, exc)
                logger.debug("Continuing to next collection after error")

        logger.debug("EXIT discover_schema - total collections: %d", len(tables))
        return {"tables": tables}

    # ── Data Extraction ───────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def extract(
        self,
        table_name: str,
        columns: list[str],
        filter: dict[str, Any] | None = None,
        batch_size: int = 1000,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Extract rows using _id-based cursor pagination."""
        logger.debug("ENTER extract: collection=%s, columns=%s, batch_size=%d, cursor=%s",
                      table_name, columns, batch_size, cursor)
        client = await self._ensure_client()
        db = client[self._db_name]
        collection = db[table_name]
        logger.debug("Using collection: %s.%s", self._db_name, table_name)

        # Build query filter
        query_filter: dict[str, Any] = {}
        logger.debug("Initial query_filter: %s", query_filter)

        # Default: exclude imported docs
        has_imported = False
        if filter and filter.get("_imported") is not None:
            query_filter["_imported"] = {"$ne": filter["_imported"]}
            has_imported = True
            logger.debug("Adding _imported filter: $ne %s", filter["_imported"])

        # Custom filters
        if filter and not has_imported:
            for k, v in filter.items():
                query_filter[k] = v
                logger.debug("Adding custom filter: %s = %s", k, v)

        # Column projection
        projection: dict[str, Any] | None = None
        if columns and columns != ["*"]:
            projection = {col: 1 for col in columns}
            projection["_id"] = 0  # Exclude _id from projection for cleaner output
            logger.debug("Using column projection: %s", columns)

        # Total count
        logger.debug("Counting documents matching filter")
        total_count = await collection.count_documents(query_filter)
        logger.debug("Total matching documents: %d", total_count)

        # Cursor-based pagination using _id
        skip = 0
        if cursor is not None:
            # MongoDB cursor is the _id string of the last document
            query_filter["_id"] = {"$gt": cursor}
            logger.debug("Using cursor-based pagination: $gt %s", cursor)

        logger.debug("Fetching with limit=%d", batch_size + 1)
        cursor_obj = collection.find(query_filter, projection).limit(batch_size + 1)
        rows = []
        last_id = None
        async for doc in cursor_obj:
            last_id = str(doc.get("_id", ""))
            if len(rows) < batch_size:
                rows.append(self._flatten_doc(doc))

        logger.debug("Fetched %d rows (fetched %d with extra)", len(rows), len(rows) + (1 if last_id else 0))
        next_cursor = last_id if len(rows) == batch_size + 1 and last_id else None
        if next_cursor and len(rows) > batch_size:
            rows = rows[:batch_size]

        origin_ids = [str(i) for i in range(len(rows))]

        logger.debug("EXIT extract: rows=%d, next_cursor=%s", len(rows), next_cursor)
        return {
            "rows": rows,
            "column_names": list(rows[0].keys()) if rows else [],
            "total_count": total_count,
            "next_cursor": next_cursor,
            "origin_ids": origin_ids,
        }

    # ── Import Flagging ───────────────────────────────────────────────

    @retry_on_transient(max_attempts=3, backoff="exponential")
    async def mark_imported(
        self,
        table_name: str,
        ids: list[str],
    ) -> bool:
        """Set _imported = true on matching documents."""
        logger.debug("ENTER mark_imported: collection=%s, ids_count=%d", table_name, len(ids))
        client = await self._ensure_client()
        db = client[self._db_name]
        collection = db[table_name]

        if not ids:
            logger.debug("No IDs provided, returning True early")
            return True

        # MongoDB _id may be ObjectId, string, or integer
        # Attempt to parse as ObjectId first, fall back to raw _id
        from bson import ObjectId, ObjectIdInvalidId

        object_ids: list[Any] = []
        for iid in ids:
            try:
                object_ids.append(ObjectId(iid))
            except (ObjectIdInvalidId, Exception):
                object_ids.append(iid)
        logger.debug("Converted %d IDs (ObjectId or raw)", len(object_ids))

        logger.debug("Executing update_many: match %d documents", len(object_ids))
        result = await collection.update_many(
            {"_id": {"$in": object_ids}},
            {"$set": {"_imported": True}},
        )
        logger.debug(
            "mark_imported completed: matched=%d, modified=%d",
            result.matched_count, result.modified_count,
        )
        return result.modified_count >= 0 or result.matched_count >= 0

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _infer_type(value: Any) -> str:
        """Infer a cross-database type from a Python value."""
        if isinstance(value, str):
            return "STRING"
        if isinstance(value, bool):
            return "BOOLEAN"
        if isinstance(value, int):
            return "INTEGER"
        if isinstance(value, float):
            return "DOUBLE"
        if isinstance(value, dict):
            return "OBJECT"
        if isinstance(value, list):
            return "ARRAY"
        if value is None:
            return "NULL"
        return "UNKNOWN"

    @staticmethod
    def _flatten_doc(doc: dict[str, Any]) -> dict[str, Any]:
        """Flatten nested document keys with dot notation."""
        flat: dict[str, Any] = {}
        for key, val in doc.items():
            if isinstance(val, dict):
                for sub_key, sub_val in val.items():
                    flat[f"{key}.{sub_key}"] = sub_val
            else:
                flat[key] = val
        return flat