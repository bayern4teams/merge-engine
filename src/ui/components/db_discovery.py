"""
Database schema discovery utilities for the Gradio UI.

Provides functions to test connections and discover available
databases, schemas, and tables for different connector types.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.ui.db_discovery")


def test_and_discover_schemas(
    conn_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str = "",
    schema: str = "",
    table: str = "",
) -> dict[str, Any]:
    """Test a connection and discover available schemas/databases/tables/columns.

    Parameters
    ----------
    conn_type : str
        One of "postgres", "mongodb", "clickhouse".
    host : str
        Hostname or IP.
    port : int
        Port number.
    user : str
        Username.
    password : str
        Password.
    database : str
        Database name (optional for discovery).
    schema : str
        Schema name (optional).
    table : str
        Table name for column discovery (optional).

    Returns
    -------
    dict
        {
            "connected": bool,
            "error": str | None,
            "databases": list[str],
            "schemas": list[str],
            "tables": list[str],
            "table_columns": dict,  # {table_name: [col1, col2, ...]}
            "table_row_counts": dict,  # {table_name: count}
        }
    """
    result: dict[str, Any] = {
        "connected": False,
        "error": None,
        "databases": [],
        "schemas": [],
        "tables": [],
        "table_columns": {},
        "table_row_counts": {},
    }

    try:
        if conn_type in ("postgres", "postgresql"):
            result = _discover_postgres(host, port, user, password, database)
        elif conn_type == "mongodb":
            result = _discover_mongodb(host, port, user, password, database)
        elif conn_type == "clickhouse":
            result = _discover_clickhouse(host, port, user, password, database)
        else:
            result["error"] = f"Unsupported type: {conn_type}"
    except Exception as exc:
        logger.warning("Discovery failed for %s://%s:%s: %s", conn_type, host, port, exc)
        result["error"] = str(exc)

    return result


def _discover_postgres(
    host: str, port: int, user: str, password: str, database: str, schema: str = "", table: str = ""
) -> dict[str, Any]:
    """Discover PostgreSQL databases, schemas, tables, and columns."""
    import psycopg2
    import psycopg2.extras

    result: dict[str, Any] = {
        "connected": False,
        "error": None,
        "databases": [],
        "schemas": [],
        "tables": [],
        "table_columns": {},
        "table_row_counts": {},
    }

    conn = None
    cur = None
    try:
        # Connect to the postgres default DB to list all databases
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname="postgres",  # Connect to default DB
            connect_timeout=10,
        )
        conn.autocommit = True
        cur = conn.cursor()

        # List databases
        cur.execute(
            "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"
        )
        result["databases"] = [row[0] for row in cur.fetchall()]

        # If a database was specified, also discover schemas, tables, and columns
        if database:
            try:
                db_conn = psycopg2.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    dbname=database,
                    connect_timeout=10,
                )
                db_cur = db_conn.cursor()

                # Discover schemas
                db_cur.execute(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY schema_name
                    """
                )
                result["schemas"] = [row[0] for row in db_cur.fetchall()]

                # Discover tables and columns for the specified schema or all schemas
                target_schema = schema if schema else result["schemas"][0] if result["schemas"] else "public"
                db_cur.execute(
                    """
                    SELECT table_name, column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    ORDER BY table_name, ordinal_position
                    """,
                    (target_schema,),
                )
                columns = db_cur.fetchall()

                # Build table_columns dict
                current_table = None
                for table_name, col_name, data_type, is_nullable in columns:
                    if table_name != current_table:
                        current_table = table_name
                        result["tables"].append(table_name)
                        result["table_columns"][table_name] = []
                    result["table_columns"][table_name].append({
                        "name": col_name,
                        "type": data_type,
                        "nullable": is_nullable == "YES",
                    })

                # Get row counts for discovered tables
                for table_name in result["tables"]:
                    try:
                        db_cur.execute(
                            f'SELECT COUNT(*) FROM "{target_schema}"."{table_name}"'
                        )
                        result["table_row_counts"][table_name] = db_cur.fetchone()[0]
                    except Exception:
                        result["table_row_counts"][table_name] = -1

                db_cur.close()
                db_conn.close()
            except Exception as db_exc:
                logger.debug("Could not discover schemas for db '%s': %s", database, db_exc)

        result["connected"] = True

    except psycopg2.OperationalError as exc:
        result["error"] = f"Connection failed: {exc}"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return result


def _discover_mongodb(
    host: str, port: int, user: str, password: str, database: str, schema: str = "", table: str = ""
) -> dict[str, Any]:
    """Discover MongoDB databases, collections, and sample documents."""
    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
    except ImportError:
        return {
            "connected": False,
            "error": "pymongo not installed. Install with: pip install pymongo",
            "databases": [],
            "schemas": [],
            "tables": [],
            "table_columns": {},
            "table_row_counts": {},
        }

    result: dict[str, Any] = {
        "connected": False,
        "error": None,
        "databases": [],
        "schemas": [],
        "tables": [],
        "table_columns": {},
        "table_row_counts": {},
    }

    client = None
    try:
        uri = f"mongodb://{user}:{password}@{host}:{port}/"
        client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=5000)
        # Trigger connection
        client.server_info()

        # List databases
        db_list = client.list_database_names()
        result["databases"] = [db for db in db_list if db not in ("admin", "config", "local")]

        # If a database was specified, list collections and sample fields
        if database:
            try:
                db = client[database]
                collections = db.list_collection_names()
                result["schemas"] = collections
                result["tables"] = collections

                # Sample fields from each collection
                for col_name in collections:
                    try:
                        col = db[col_name]
                        doc = col.find_one()
                        if doc:
                            result["table_columns"][col_name] = list(doc.keys())
                            result["table_row_counts"][col_name] = col.estimated_document_count()
                        else:
                            result["table_columns"][col_name] = []
                            result["table_row_counts"][col_name] = 0
                    except PyMongoError as col_exc:
                        logger.debug("Could not sample collection '%s': %s", col_name, col_exc)
            except PyMongoError as db_exc:
                logger.debug("Could not list collections for db '%s': %s", database, db_exc)

        result["connected"] = True

    except PyMongoError as exc:
        result["error"] = f"Connection failed: {exc}"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if client:
            client.close()

    return result


def _discover_clickhouse(
    host: str, port: int, user: str, password: str, database: str, schema: str = "", table: str = ""
) -> dict[str, Any]:
    """Discover ClickHouse databases, tables, and columns."""
    try:
        import clickhouse_connect
    except ImportError:
        return {
            "connected": False,
            "error": "clickhouse-connect not installed. Install with: pip install clickhouse-connect",
            "databases": [],
            "schemas": [],
            "tables": [],
            "table_columns": {},
            "table_row_counts": {},
        }

    result: dict[str, Any] = {
        "connected": False,
        "error": None,
        "databases": [],
        "schemas": [],
        "tables": [],
        "table_columns": {},
        "table_row_counts": {},
    }

    client = None
    try:
        client = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=user,
            password=password,
            connect_timeout=10,
        )

        # List databases
        db_result = client.query("SELECT name FROM system.databases ORDER BY name")
        result["databases"] = db_result.result_columns[0]

        # If a database was specified, list tables with columns and row counts
        if database:
            try:
                # Get tables with their columns
                table_result = client.query(
                    """
                    SELECT name, engine_full
                    FROM system.tables
                    WHERE database = :db
                    ORDER BY name
                    """,
                    parameters={"db": database},
                )
                result["tables"] = [row[0] for row in table_result.result_columns[0]]

                # Get columns for each table
                for tbl_name in result["tables"]:
                    try:
                        col_result = client.query(
                            """
                            SELECT name, type, default_kind
                            FROM system.columns
                            WHERE database = :db AND table = :tbl
                            ORDER BY position
                            """,
                            parameters={"db": database, "tbl": tbl_name},
                        )
                        columns = col_result.result_columns[0]
                        result["table_columns"][tbl_name] = [
                            {"name": c[0], "type": c[1], "nullable": c[2] != " mater"}
                            for c in columns
                        ]
                    except Exception:
                        result["table_columns"][tbl_name] = []

                    # Get row count
                    try:
                        count_result = client.query(
                            "SELECT count() FROM {:}.{:}".format(database, tbl_name),
                        )
                        result["table_row_counts"][tbl_name] = count_result.result_rows[0][0]
                    except Exception:
                        result["table_row_counts"][tbl_name] = -1

            except Exception as db_exc:
                logger.debug("Could not list tables for db '%s': %s", database, db_exc)

        result["connected"] = True

    except Exception as exc:
        result["error"] = f"Connection failed: {exc}"
    finally:
        if client:
            try:
                client.raw_close()
            except Exception:
                pass

    return result
