---
type: connector
title: MongoDB Connector
description: MongoDB source connector using PyMongo AsyncMongoClient. Supports collection discovery via document sampling, flexible document extraction, and _id-based pagination.
tags: [connector, mongodb, nosql, document-database]
---

# MongoDB Connector

## Purpose

MongoDB source connector for schema discovery, data extraction, and import flagging. Uses PyMongo's async client for database operations.

## File

`src/mcp_server/connectors/mongodb.py` (~17KB)

## Connection

```python
from pymongo import AsyncMongoClient

client = AsyncMongoClient(host, port=27017, username=user, password=password, tls=True)
db = client[database]
```

## Schema Discovery

Discovers collections by sampling documents from each collection:

1. Lists all collections via `list_collections()`
2. Samples the first N documents from each collection
3. Infers column names from the document keys
4. Infers column types from the sampled values
5. Generates sample values for UI preview

```python
sample_docs = await db[collection].find().limit(5).to_list(5)
columns = infer_columns(sample_docs)
```

## Extraction

Extracts documents with optional filters:

```python
cursor = db[collection].find(filter).skip(offset).limit(limit)
rows = await cursor.to_list(length=limit)
```

Uses `_id`-based pagination (skip-based) since MongoDB doesn't support keyset pagination natively.

## Import Flag

Documents in MongoDB are flagged with `_imported: true/false` for tracking extraction status.

## Test Coverage

- `tests/unit/test_mongodb_connector.py` — comprehensive connector tests (27KB)
