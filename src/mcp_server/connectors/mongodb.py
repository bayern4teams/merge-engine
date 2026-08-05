"""
MongoDB connector for schema discovery, data extraction, and import flagging.

Uses PyMongo for async driver access.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from pymongo import AsyncMongoClient
from pymongo.server_api import ServerApi

from src.mcp_server.connectors.base import BaseConnector
from src.mcp_server.connectors.filters import normalize_filter
from src.mcp_server.connectors.keyset import decode_cursor, encode_cursor
from src.orchestrator.utils.retry import retry_on_transient

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

        # Support both flat connection dict and nested origin config shape
        conn = self.config
        if "connection" in conn and isinstance(conn["connection"], dict):
            conn = conn["connection"]
        uri = conn.get("uri") or ""
        database = conn.get("database") or ""

        if not uri:
            host = conn.get("host") or ""
            if not host:
                logger.debug("No URI and no host provided, raising ValueError")
                raise ValueError("MongoDB connection requires either a uri or a host")
            port = conn.get("port", 27017)
            user = conn.get("user") or ""
            password = conn.get("password") or ""
            credentials = f"{quote_plus(user)}:{quote_plus(password)}@" if user else ""
            uri = f"mongodb://{credentials}{host}:{port}/"
            logger.debug("Built MongoDB URI from host/port/user: host=%s, port=%s", host, port)

        if database:
            self._db_name = database
        elif not self._db_name or self._db_name == "admin":
            # Parse dbname from mongodb://host:port/dbname
            parts = uri.split("/")
            if len(parts) >= 4 and parts[3]:
                self._db_name = parts[3].split("?")[0].split("&")[0]
                logger.debug("Parsed database from URI: %s", self._db_name)

        logger.debug(
            "Creating MongoDB async client: uri=%s:%s, db=%s",
            uri[:50] + "..." if len(uri) > 50 else uri,
            "...",
            self._db_name,
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
            logger.debug(
                "Found %d collections in database %s", len(collection_names), self._db_name
            )
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
                    tables.append(
                        {
                            "name": coll_name,
                            "columns": [],
                            "foreign_keys": [],
                            "sample_row": {},
                        }
                    )
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

                tables.append(
                    {
                        "name": coll_name,
                        "columns": columns,
                        "foreign_keys": [],  # MongoDB doesn't have native FKs
                        "sample_row": sample_row,
                    }
                )
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
        watermark: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract documents by seeking forward through ``_id``.

        ``_id`` is unique and ordered, so it is already the ideal keyset —
        each batch asks for the documents after the last one seen, which an
        index answers in constant time.

        Note: watermark-based incremental extraction is not yet implemented
        for MongoDB; the parameter is accepted for interface compatibility
        but ignored (full re-scan every run).
        """
        logger.debug(
            "ENTER extract: collection=%s, columns=%s, batch_size=%d, cursor=%s",
            table_name,
            columns,
            batch_size,
            cursor,
        )
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

        # Custom filters: equals (back-compat), ilike (case-insensitive
        # substring via $regex, for a subject-name match) and in ($in, for a
        # link column restricted to a set of subject ids).
        if filter and not has_imported:
            for cond in normalize_filter(filter):
                col, op, val = cond["column"], cond["op"], cond["value"]
                if op == "ilike":
                    query_filter[col] = {"$regex": re.escape(str(val)), "$options": "i"}
                elif op == "in":
                    query_filter[col] = {"$in": val}
                else:
                    query_filter[col] = val
                logger.debug("Adding custom filter: %s %s %s", col, op, val)

        # Column projection. `_id` is always fetched even when the caller did
        # not ask for it — it is the pagination key, and excluding it left
        # every batch with no cursor to continue from.
        projection: dict[str, Any] | None = None
        drop_id_from_output = False
        if columns and columns != ["*"]:
            projection = {col: 1 for col in columns}
            if "_id" not in projection:
                projection["_id"] = 1
                drop_id_from_output = True
            logger.debug("Using column projection: %s", columns)

        # ── Position, and the once-per-collection count ─────────────
        position = decode_cursor(cursor)
        total_count = position["total_count"]
        if total_count is None:
            logger.debug("Counting documents matching filter (first batch only)")
            total_count = await collection.count_documents(query_filter)
            logger.debug("Total matching documents: %d", total_count)

        last_seen = (position["keyset"] or [None])[0]
        if last_seen is not None:
            # The cursor travels as text; an ObjectId collection needs it
            # converted back or `$gt` silently matches nothing.
            query_filter["_id"] = {"$gt": self._as_id(last_seen)}
            logger.debug("Seeking documents after _id %s", last_seen)

        # One extra document is fetched purely to learn whether another page
        # exists; it is never returned.
        logger.debug("Fetching with limit=%d", batch_size + 1)
        cursor_obj = collection.find(query_filter, projection).sort("_id", 1).limit(batch_size + 1)
        fetched: list[dict[str, Any]] = []
        async for doc in cursor_obj:
            fetched.append(doc)

        has_more = len(fetched) > batch_size
        page = fetched[:batch_size]
        last_id = str(page[-1].get("_id", "")) if page else None

        rows = []
        for doc in page:
            flat = self._flatten_doc(doc)
            if drop_id_from_output:
                flat.pop("_id", None)
            rows.append(flat)

        next_cursor = (
            encode_cursor(keyset_values=[last_id], total_count=total_count)
            if has_more and last_id
            else None
        )
        origin_ids = [str(i) for i in range(len(rows))]

        logger.debug("EXIT extract: rows=%d, has_more=%s", len(rows), has_more)
        return {
            "rows": rows,
            "column_names": list(rows[0].keys()) if rows else [],
            "total_count": total_count,
            "next_cursor": next_cursor,
            "origin_ids": origin_ids,
        }

    @staticmethod
    def _as_id(value: str) -> Any:
        """Turn a cursor string back into whatever `_id` type the collection uses."""
        try:
            from bson import ObjectId

            if ObjectId.is_valid(value):
                return ObjectId(value)
        except Exception:
            pass
        return value

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
            result.matched_count,
            result.modified_count,
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
    def _to_serializable(val: Any) -> Any:
        """Coerce BSON-only types (ObjectId, Decimal128, Binary…) to plain values.

        LangGraph checkpoints graph state with msgpack, which rejects BSON types.
        """
        if isinstance(val, (str, int, float, bool, type(None), datetime)):
            return val
        if isinstance(val, list):
            return [MongoDBConnector._to_serializable(v) for v in val]
        if isinstance(val, dict):
            return {k: MongoDBConnector._to_serializable(v) for k, v in val.items()}
        return str(val)

    @staticmethod
    def _flatten_doc(doc: dict[str, Any]) -> dict[str, Any]:
        """Flatten nested document keys with dot notation."""
        flat: dict[str, Any] = {}
        for key, val in doc.items():
            if isinstance(val, dict):
                for sub_key, sub_val in val.items():
                    flat[f"{key}.{sub_key}"] = MongoDBConnector._to_serializable(sub_val)
            else:
                flat[key] = MongoDBConnector._to_serializable(val)
        return flat
