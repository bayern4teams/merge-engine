# Files

- [Database Discovery Helpers](db_discovery.md) - UI helpers for database discovery: connection testing, schema discovery, and sample row retrieval for PostgreSQL, MongoDB, and ClickHouse.

- [User Interface](overview.md) - UI architecture for merge-engine: React SPA built with Vite and TypeScript, served by FastAPI static files, with an API client, state management, and connection testing helpers.

- [Recent Connections](recent_connections.md) - Recent-connection persistence for the merge-engine UI: saves connection history to config/recent_connections.json with de-duplication on (host, database, type) and max_size=5 trimming.

