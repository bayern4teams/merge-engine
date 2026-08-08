# Files

- [Configuration Loader](config_loader.md) - Configuration loading and validation for merge-engine: load_config() with Pydantic validation, ${ENV_VAR} resolution, platform overrides, and environment variable resolution.

- [UUID Utilities](id_utils.md) - UUID generation and validation utilities: random UUIDs for new records, deterministic UUIDs for reproducible entity IDs, hex/dash format conversion.

- [Logging Configuration](logging.md) - Structured logging setup for merge-engine using structlog with JSON formatting, Windows UTF-8 handling, and per-module logger names.

- [Span Tracing](tracing.md) - Generic span context manager for connector-level tracing using LangChain's runnables config. Not raw OTel — spans are LangChain callback-based.

- [Value Coercion](value_coercion.md) - Value coercion for merge-engine: source-value to destination-type reconciliation with magnitude parsing, date normalization, and type compatibility checks. Used by map_fields and PostgreSQLDestinationConnector.

