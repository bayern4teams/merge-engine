# Files

- [ClickHouse Connector](clickhouse.md) - ClickHouse source connector using clickhouse-connect. Supports schema discovery, keyset pagination, and scratch-table support for large IN filters (up to 50K+ IDs).
- [Databricks Connector](databricks.md) - Databricks Delta Lake connector with dual role: it serves as a source connector inheriting from BaseConnector for schema discovery and extraction, and as a destination writer supporting load_batch MERGE, add_column, and extend_schema_batch operations.

- [Destination Connectors](destinations.md) - PostgreSQL and ClickHouse destination connectors for writing unified records. Handles schema creation, upserts via INSERT ... ON CONFLICT or INSERT INTO ... SELECT, FK translation, and extraction state tracking.
- [MongoDB Connector](mongodb.md) - MongoDB source connector using PyMongo AsyncMongoClient. Supports collection discovery via document sampling, flexible document extraction, and _id-based pagination.
- [Database Connectors Overview](overview.md) - Connector architecture for merge-engine, covering the BaseConnector abstract interface, the factory pattern via create_connector(), the connector registry, and shared utilities used across all database connectors.

- [PostgreSQL Connector](postgres.md) - PostgreSQL source connector using SQLAlchemy async engine and asyncpg driver. Supports schema discovery via information_schema, keyset (seek) pagination, watermark pushdown for incremental extraction, and type-aware timestamp casting.
- [Shared Connector Utilities](shared_utilities.md) - Shared utilities for database connectors, covering filter normalization in filters.py, keyset pagination cursors in keyset.py, and watermark column detection in watermark.py.
