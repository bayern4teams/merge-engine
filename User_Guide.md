# merge-engine — User Guide

This guide gets you from "empty machine" to "a merge running end-to-end
against real databases in Docker." It assumes no prior context: Docker
Desktop and Python are the only things assumed installed.

Contents:
1. [What this tool does](#1-what-this-tool-does)
2. [Prerequisites](#2-prerequisites)
3. [Install the app](#3-install-the-app)
4. [Set up your own databases in Docker](#4-set-up-your-own-databases-in-docker) — one compose file per database type
5. [Configure merge-engine](#5-configure-merge-engine)
6. [Run it](#6-run-it)
7. [Walk through a merge, step by step](#7-walk-through-a-merge-step-by-step)
8. [Test with example databases](#8-test-with-example-databases) — small / scale / edge-case datasets, with download steps
9. [Troubleshooting](#9-troubleshooting)

---

## 1. What this tool does

merge-engine takes data that lives in two or more separate databases (any mix
of PostgreSQL, ClickHouse, MongoDB), figures out which rows in each origin
represent the *same real-world entity* (a person, a company, a device — even
when their IDs, column names, and schemas don't match), merges them, and
writes the unified result into one destination database (PostgreSQL or
ClickHouse). An LLM assists with schema understanding and ambiguous decisions;
a human approves the pipeline at four checkpoints before anything is written.

## 2. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Docker Desktop | any recent | `docker --version` |
| Python | 3.11+ | `python --version` |
| Node.js | 18+ (only if you rebuild the frontend) | `node --version` |
| An LLM API key | any OpenAI-compatible endpoint | — |

All commands below are written for **PowerShell on Windows** (this project's
primary environment) with a Bash equivalent noted where it differs.

## 3. Install the app

```bash
git clone <your-repo-url> merging
cd merging
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Copy the environment template and fill in your LLM key:

```bash
copy .env.example .env
```

Open `.env` and set:

```
LLM_API_KEY=<your key>
```

## 4. Set up your own databases in Docker

Pick the section(s) for the database types you want to use. You need **at
least one origin** and **exactly one destination** (destination type may be
PostgreSQL or ClickHouse — MongoDB is origin-only in this project).
Each section is a **complete, standalone `docker-compose.yml`** — save each
one in its own folder (e.g. `docker/postgres/`, `docker/clickhouse/`,
`docker/mongodb/`) so they don't collide, and run `docker compose up -d`
inside that folder.

### 4.1 PostgreSQL (origin or destination)

`docker/postgres/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:17
    container_name: merge-engine-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: merge_origin
    ports:
      - "5433:5432"        # host:container — 5433 avoids clashing with a local Postgres install
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  pg_data:
```

Start it:

```bash
cd docker/postgres
docker compose up -d
docker compose ps          # wait until "healthy"
```

Connect and confirm:

```bash
docker exec -it merge-engine-postgres psql -U postgres -d merge_origin -c "SELECT 1;"
```

Connection details for the merge-engine UI: `host=localhost, port=5433,
user=postgres, password=postgres, database=merge_origin`.

### 4.2 ClickHouse (origin or destination)

`docker/clickhouse/docker-compose.yml`:

```yaml
services:
  clickhouse:
    image: clickhouse/clickhouse-server:latest
    container_name: merge-engine-clickhouse
    restart: unless-stopped
    environment:
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: clickhouse
      CLICKHOUSE_DB: merge_origin
    ports:
      - "8124:8123"   # HTTP interface — 8124 avoids clashing with another local ClickHouse
      - "9001:9000"   # native protocol
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
    volumes:
      - ch_data:/var/lib/clickhouse

volumes:
  ch_data:
```

Start it:

```bash
cd docker/clickhouse
docker compose up -d
```

Connect and confirm:

```bash
curl "http://localhost:8124/ping"
curl -u clickhouse:clickhouse "http://localhost:8124/?query=SELECT%201"
```

Connection details for the UI: `host=localhost, port=8124, user=clickhouse,
password=clickhouse, database=merge_origin`.

### 4.3 MongoDB (origin only)

`docker/mongodb/docker-compose.yml`:

```yaml
services:
  mongodb:
    image: mongo:7
    container_name: merge-engine-mongo
    restart: unless-stopped
    environment:
      MONGO_INITDB_ROOT_USERNAME: root
      MONGO_INITDB_ROOT_PASSWORD: rootpass
      MONGO_INITDB_DATABASE: merge_origin
    ports:
      - "27018:27017"     # 27018 avoids clashing with a local Mongo install
    volumes:
      - mongo_data:/data/db

volumes:
  mongo_data:
```

Start it:

```bash
cd docker/mongodb
docker compose up -d
```

Connect and confirm:

```bash
docker exec -it merge-engine-mongo mongosh -u root -p rootpass --authenticationDatabase admin --eval "db.runCommand({ping:1})"
```

Connection string for the UI: `mongodb://root:rootpass@localhost:27018/merge_origin?authSource=admin`.

### 4.4 Running two or three at once

Docker Compose files are independent by default — start as many of the three
above as you need, each in its own folder, and they'll coexist as long as
their host ports don't collide (already arranged above: 5433, 8124/9001,
27018). Check nothing else on your machine already holds those ports:

```bash
netstat -ano | findstr "5433 8124 9001 27018"
```

If a port is taken, change the **host** side of the mapping (the left number
in `"5433:5432"`) — never the container side.

## 5. Configure merge-engine

`config/config.yaml` holds pipeline-level settings only (LLM model, retry
policy, batch sizes). **Origin and destination connections are never stored
in a file** — you enter them in the UI at runtime, and merge-engine
remembers recent connections in `config/recent_connections.json` (gitignored,
since it's local to your machine).

Open `config/config.yaml` and check the `llm:` block matches your API
endpoint — the shipped default targets an OpenAI-compatible DashScope
endpoint; point `base_url`/`model` at whatever provider your `LLM_API_KEY` is
for.

## 6. Run it

```bash
python -m src.main --mode both --port 7860
```

This starts the FastAPI backend (serving the built React UI) on
`http://localhost:7860`. Open that URL in a browser.

If you changed frontend source under `src/ui/frontend/src`, rebuild it first:

```bash
cd src/ui/frontend
npm install
npm run build
cd ../../..
```

`main.py` serves whatever is in `src/ui/frontend/dist` — it does not rebuild
automatically.

## 7. Walk through a merge, step by step

1. **Setup screen** — add your origin connection(s) (host/port/user/password/
   database); merge-engine tests the connection and discovers its schema
   live. Add a second origin if merging across sources. Then set exactly one
   destination the same way.
2. **Describe the merge** in plain English, e.g. *"Merge customer records
   from both databases into one unified customers table."*
3. **Start Merge.** The pipeline runs through: preflight checks → parse your
   request → discover schemas → **pause: confirm which tables map to which
   entities** → extract data → resolve duplicate entities → **pause: review
   match/cluster decisions** → unify records → map fields to the destination
   schema → **pause: review the field mapping** → **pause: final approval**
   → load into the destination.
4. At each pause (HITL gate) you can **Approve**, **Edit** the proposed
   result and continue, **Retry** the step with a free-text correction fed
   back to the LLM, or **Abort** — a reject at any gate writes nothing to the
   destination.
5. **Save a profile** (optional, on the Setup screen before starting) to pin
   the entity/mapping/matching-rule decisions this run made, so re-running
   the same merge later doesn't re-derive them from the LLM (which can vary
   run to run) and instead replays exactly what you approved.
6. When it completes, check `GET /api/workflow/{id}/cost` (or the cost panel
   in the UI) for the LLM token spend, and the on-screen reconciliation
   report for row counts per entity.

## 8. Test with example databases

Three groups of publicly available sample datasets, chosen to exercise this
project's actual failure modes: **low-volume** (fast iteration on the HITL
flow itself), **scale** (the keyset-pagination/DuckDB-spool path, meant for
10M+ rows), and **edge cases** (deliberately messy data that stresses entity
resolution). Each group lists a recommended origin/destination pairing.

### 8.1 Low-volume group — fast functional testing

**Origins:** Pagila (PostgreSQL, ~1,000–16,000 rows/table) and Sakila-for-Mongo
or `sample_mflix` (MongoDB, ~50,000 rows). **Destination:** a fresh empty
PostgreSQL or ClickHouse database from section 4.

**Pagila (PostgreSQL sample DVD-rental DB)**

```bash
git clone https://github.com/devrimgunduz/pagila.git
cd pagila
docker exec -i merge-engine-postgres psql -U postgres -d merge_origin < pagila-schema.sql
docker exec -i merge-engine-postgres psql -U postgres -d merge_origin < pagila-data.sql
```

Verify: `docker exec -it merge-engine-postgres psql -U postgres -d merge_origin -c "SELECT count(*) FROM customer;"` → 599 rows.

**MongoDB `sample_mflix` (Atlas sample dataset, works self-hosted too)**

```bash
curl -L -o sampledata.archive https://atlas-education.s3.amazonaws.com/sampledata.archive
docker cp sampledata.archive merge-engine-mongo:/tmp/sampledata.archive
docker exec -it merge-engine-mongo mongorestore --username root --password rootpass --authenticationDatabase admin --nsInclude "sample_mflix.*" /tmp/sampledata.archive
```

Verify: `docker exec -it merge-engine-mongo mongosh -u root -p rootpass --authenticationDatabase admin --eval "db.getSiblingDB('sample_mflix').movies.countDocuments()"` → ~23,000.

This pair is what this project's own Phase J/K/L verification ran against —
a genuine duplicate in Pagila `staff` and repeated titles in `sample_mflix`
give you real (not synthetic) entity-resolution cases within seconds of
extraction.

### 8.2 Scale group — millions of rows, proves the pagination/spool path

**Origin:** OpenCelliD `cell_towers` (ClickHouse-friendly CSV, 40M+ rows).
**Destination:** PostgreSQL (to also exercise the atomic-load path against a
row count that large).

```bash
# 1. Get an OpenCelliD API token (free, https://www.opencellid.org — sign up, then
#    Downloads -> "Full cell tower dataset")
curl -L -o cell_towers.csv.gz "https://opencellid.org/downloads/?token=<YOUR_TOKEN>&type=full&file=cell_towers.csv.gz"
gunzip cell_towers.csv.gz

# 2. Create the table in your ClickHouse container (section 4.2)
docker exec -it merge-engine-clickhouse clickhouse-client -u clickhouse --password clickhouse --query "
CREATE DATABASE IF NOT EXISTS merge_origin;
CREATE TABLE merge_origin.cell_towers (
  radio String, mcc UInt16, net UInt16, area UInt16, cell UInt64,
  unit Int16, lon Float64, lat Float64, range UInt32, samples UInt32,
  changeable UInt8, created UInt32, updated UInt32, averageSignal UInt8
) ENGINE = MergeTree ORDER BY (radio, mcc, net, created);
"

# 3. Load the CSV directly (skip header row)
docker cp cell_towers.csv merge-engine-clickhouse:/tmp/cell_towers.csv
docker exec -it merge-engine-clickhouse clickhouse-client -u clickhouse --password clickhouse --query "
INSERT INTO merge_origin.cell_towers FORMAT CSVWithNames" < cell_towers.csv
```

Verify: `docker exec -it merge-engine-clickhouse clickhouse-client -u clickhouse --password clickhouse --query "SELECT count() FROM merge_origin.cell_towers"` — this project's own scale run measured **43,276,150 rows**, extracted at ~16,000 rows/s with 380 MB peak memory (see `TODO.md`, Phase N.5). Your exact row count will depend on the dataset's current size.

A smaller, faster alternative for a first scale smoke test (~1M rows) is
`generate_series` directly in Postgres:

```sql
CREATE TABLE big_origin AS
SELECT g AS id, 'device-' || (g % 500000) AS device_key, now() - (g || ' seconds')::interval AS created_at
FROM generate_series(1, 1000000) g;
```

### 8.3 Edge-case group — deliberately messy data for entity resolution

**Origins:** two copies of the same conceptual data with intentionally
mismatched schemas and IDs, to reproduce this project's own hardest bugs
(non-unique keys, near-duplicate names, missing unique identifiers).

If you want an external edge-case source: the [Febrl-based synthetic
deduplication datasets](https://github.com/J535D165/recordlinkage) (Python
`recordlinkage` package ships generator functions) produce records with
controlled corruption (typos, transposed digits, missing fields) and known
ground-truth duplicate pairs — useful for measuring precision/recall the same
way this project's `tests/eval/` harness does.

### 8.4 Summary table

| Group | Origins | Destination | Row scale | What it proves |
|---|---|---|---|---|
| Low-volume | Pagila (Postgres) + sample_mflix (Mongo) | fresh Postgres/ClickHouse | ~1.5K–23K | HITL flow, field mapping, basic entity resolution |
| Scale | cell_towers (ClickHouse) | Postgres | 1M–43M | Keyset pagination, DuckDB spool, atomic load, memory bound |
| Edge case | `create_test_databases.py` seed (Postgres + ClickHouse) | Postgres | ~150–450 | Schema mismatch, cross-source ID mismatch, deliberate duplicates |
| Real-world sample stack | `origin1` + `origin2` (ClickHouse) | `global_warehouse` (Postgres, empty schema) | 35M+ origin rows, 0 destination rows | Real-shape data merged into a hand-authored destination schema, not a synthetic one |

### 8.5 Real-world sample databases stack (`sample_databases/`)

One `docker compose` file brings up both real ClickHouse origins and a
PostgreSQL destination with the "Global Warehouse" schema
(`sample_databases/destination/ER_DIAGRAM.md`, 25 tables) pre-created but
**empty — no mock data is ever inserted into it**.

```bash
docker compose -f sample_databases/docker-compose.yml up -d
```

This starts two containers:
- **`Sample_Database`** (ClickHouse, ports `8190`:HTTP / `9005`:native,
  `default`/`pass`) — databases `origin1` (13 tables, ~51K rows, imported from
  `sample_databases/origin1/`) and `origin2` (7 tables, ~35.2M rows, imported
  from `sample_databases/origin2_sample/*.sql`) via
  `scripts/import_sample_databases.py`.
- **`Sample_Database_Destination`** (PostgreSQL 17, port `55201`,
  `postgres`/`pass`, database `global_warehouse`) — all 25 Global Warehouse
  tables and their foreign keys, created from
  `sample_databases/destination/sql/01_company.sql` … `07_technographic.sql`
  on first boot. **This destination is intentionally empty** (0 rows in every
  table) — it exists to receive real merge output, not to ship pre-seeded data.
  (Port changed from `55432` to `55201` on 2026-07-30: Windows/Hyper-V's
  dynamic TCP excluded-port-range came to cover 55357-55456 after a host
  sleep/resume, so Docker Desktop could no longer bind 55432. Fixing that at
  the OS level needs `net stop/start winnat` with admin rights; remapping the
  container's published port was the workaround. Re-check with `netsh
  interface ipv4 show excludedportrange protocol=tcp` if connections start
  refusing again.)

Connection details for the merge-engine UI:
- Origins: `host=localhost, port=8190, user=default, password=pass,
  database=origin1` (and `origin2`).
- Destination: `host=localhost, port=55201, user=postgres, password=pass,
  database=global_warehouse`.

Verify directly:

```bash
docker exec Sample_Database_Destination psql -U postgres -d global_warehouse -c "\dt"
```

## 9. Troubleshooting

- **"port already in use" on startup** — another `python -m src.main`
  process is still holding the port. On Windows: `netstat -ano | findstr :7860`
  to find the PID, then `Stop-Process -Id <pid> -Force`. Confirm the PID
  changed before assuming your fix is live — restarting on top of an old
  process that failed to exit is a real trap this project hit once.
- **A parked run's data disappeared after a restart** — fixed in this
  project (spool cleanup now requires 6 hours of file age before deleting
  anything), but if you're running an older checkout, don't start a second
  merge while an earlier one is sitting at a HITL gate right after an app
  restart.
- **Windows console shows "--- Logging error ---" instead of real logs** —
  fixed by UTF-8 console reconfiguration in `src/utils/logging_config.py`;
  update if you're on an older checkout.
- **ClickHouse load is not atomic** — known, documented limitation (only
  PostgreSQL destinations get the full stage-then-swap transactional load).
