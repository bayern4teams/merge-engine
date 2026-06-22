"""
Database setup script for merge-engine integration testing.

Creates and populates:
- 2 PostgreSQL origin databases
- 1 MongoDB origin database  
- 1 PostgreSQL destination database

Usage:
    python -m testsfixtures.setup_test_databases [--drop]

Environment variables:
    PG_ORIGIN_1_HOST       Default: localhost
    PG_ORIGIN_1_PORT       Default: 5432
    PG_ORIGIN_1_DB         Default: test_origin_postgres_1
    PG_ORIGIN_1_USER       Default: test_user
    PG_ORIGIN_1_PASSWORD   Default: test_password

    PG_ORIGIN_2_HOST       Default: localhost
    PG_ORIGIN_2_PORT       Default: 5433
    PG_ORIGIN_2_DB         Default: test_origin_postgres_2
    PG_ORIGIN_2_USER       Default: test_user
    PG_ORIGIN_2_PASSWORD   Default: test_password

    MONGO_HOST             Default: localhost
    MONGO_PORT             Default: 27017
    MONGO_DB               Default: test_origin_mongodb

    PG_DEST_HOST           Default: localhost
    PG_DEST_PORT           Default: 5434
    PG_DEST_DB             Default: test_destination_postgres
    PG_DEST_USER           Default: test_user
    PG_DEST_PASSWORD       Default: test_password
"""

import asyncio
import json
import os
import random
import sys
import uuid
from datetime import datetime
from typing import Any

import asyncpg
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def get_config() -> dict:
    """Read database configuration from environment variables."""
    return {
        "pg_origin_1": {
            "host": os.getenv("PG_ORIGIN_1_HOST", "localhost"),
            "port": int(os.getenv("PG_ORIGIN_1_PORT", "5432")),
            "database": os.getenv("PG_ORIGIN_1_DB", "test_origin_postgres_1"),
            "user": os.getenv("PG_ORIGIN_1_USER", "test_user"),
            "password": os.getenv("PG_ORIGIN_1_PASSWORD", "test_password"),
        },
        "pg_origin_2": {
            "host": os.getenv("PG_ORIGIN_2_HOST", "localhost"),
            "port": int(os.getenv("PG_ORIGIN_2_PORT", "5433")),
            "database": os.getenv("PG_ORIGIN_2_DB", "test_origin_postgres_2"),
            "user": os.getenv("PG_ORIGIN_2_USER", "test_user"),
            "password": os.getenv("PG_ORIGIN_2_PASSWORD", "test_password"),
        },
        "mongodb": {
            "host": os.getenv("MONGO_HOST", "localhost"),
            "port": int(os.getenv("MONGO_PORT", "27017")),
            "database": os.getenv("MONGO_DB", "test_origin_mongodb"),
        },
        "pg_destination": {
            "host": os.getenv("PG_DEST_HOST", "localhost"),
            "port": int(os.getenv("PG_DEST_PORT", "5434")),
            "database": os.getenv("PG_DEST_DB", "test_destination_postgres"),
            "user": os.getenv("PG_DEST_USER", "test_user"),
            "password": os.getenv("PG_DEST_PASSWORD", "test_password"),
        },
    }


# ---------------------------------------------------------------------------
# Data generation (inline to avoid circular imports)
# ---------------------------------------------------------------------------

random.seed(42)


def _uid() -> str:
    return str(uuid.UUID(int=random.getrandbits(128), version=4))


def _address() -> dict:
    streets = ["Main St", "Main Street", "Oak Avenue", "Elm Boulevard", "Broadway", "Park Road"]
    cities = ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia"]
    states = ["NY", "CA", "IL", "TX", "AZ", "PA"]
    n = random.randint(100, 9999)
    return {
        "street": f"{n} {random.choice(streets)}",
        "city": random.choice(cities),
        "state": random.choice(states),
        "zip": f"{random.randint(10000, 99999)}",
        "country": "US",
    }


FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Jennifer", "Lisa", "Elizabeth", "Sarah"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
COMPANY_NAMES = ["Acme Corp", "Global Inc", "United LLC", "Premier Solutions", "Apex Technologies"]
PRODUCT_NAMES = ["Widget A", "Gadget X", "Tool Pro", "Device Plus", "System Ultra"]


def _gen_companies(n: int) -> list[dict]:
    rows = []
    for _ in range(n):
        name = random.choice(COMPANY_NAMES)
        rows.append({
            "id": _uid(),
            "name": f"{name} {random.randint(100, 999)}",
            "legal_name": f"{name} Legal Entity",
            "phone": f"+1-{random.randint(200,999)}-{random.randint(200,999)}-{random.randint(1000,9999)}",
            "email": f"info@{name.lower().replace(' ', '')}{random.randint(1,99)}.com",
            "website": f"https://{name.lower().replace(' ', '')}{random.randint(1,99)}.com",
            "industry": random.choice(["Technology", "Healthcare", "Finance"]),
            "employee_count": random.randint(10, 5000),
            "annual_revenue": round(random.uniform(100000, 100000000), 2),
            "street": _address()["street"],
            "city": _address()["city"],
            "state": _address()["state"],
            "zip_code": _address()["zip"],
            "country": "US",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "record_status": "active",
            "source_id": random.choice(["origin_postgres_1", "origin_mongodb_1"]),
        })
    return rows


def _gen_contacts(n: int) -> list[dict]:
    rows = []
    for _ in range(n):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        rows.append({
            "id": _uid(),
            "first_name": first,
            "last_name": last,
            "middle_name": random.choice(["A", None, None]),
            "nickname": random.choice(["Bob", "Rob", "Jim", "Mike", None, None]),
            "full_name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}{random.randint(1,999)}@gmail.com",
            "phone": f"+1-{random.randint(200,999)}-{random.randint(200,999)}-{random.randint(1000,9999)}",
            "title": random.choice(["Engineer", "Manager", "Director", "VP", "CTO"]),
            "company_id": _uid(),
            "street": _address()["street"],
            "city": _address()["city"],
            "state": _address()["state"],
            "zip_code": _address()["zip"],
            "country": "US",
            "linkedin": f"https://linkedin.com/in/{first.lower()}-{last.lower()}",
            "date_of_birth": None,
            "gender": random.choice(["M", "F", None]),
            "notes": random.choice([None, "", "VIP customer"]),
            "source_id": random.choice(["origin_postgres_1", "origin_mongodb_1"]),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "record_status": "active",
        })
    return rows


def _gen_products(n: int) -> list[dict]:
    rows = []
    for _ in range(n):
        name = random.choice(PRODUCT_NAMES)
        rows.append({
            "id": _uid(),
            "name": f"{name} {random.randint(1, 99)}",
            "description": f"High-quality {name.lower()} product",
            "sku": f"SKU-{random.choice('ABCDE')}-{random.randint(1000, 9999)}",
            "category": random.choice(["Electronics", "Hardware", "Software"]),
            "price": round(random.uniform(9.99, 999.99), 2),
            "cost": round(random.uniform(5.0, 500.0), 2),
            "weight": round(random.uniform(0.1, 50.0), 2),
            "units": random.choice(["pcs", "kg", "lbs"]),
            "stock_quantity": random.randint(0, 10000),
            "reorder_level": random.randint(10, 100),
            "supplier": random.choice(COMPANY_NAMES),
            "specifications": json.dumps({
                "color": random.choice(["Red", "Blue", "Black"]),
                "size": random.choice(["S", "M", "L", "XL"]),
            }),
            "source_id": random.choice(["origin_postgres_1", "origin_mongodb_1"]),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "record_status": "active",
        })
    return rows


def _gen_orders(n: int) -> list[dict]:
    rows = []
    for _ in range(n):
        num_items = random.randint(1, 3)
        items = []
        for _ in range(num_items):
            items.append({
                "product_id": _uid(),
                "product_name": random.choice(PRODUCT_NAMES),
                "sku": f"SKU-{random.choice('ABCDE')}-{random.randint(1000, 9999)}",
                "quantity": random.randint(1, 10),
                "unit_price": round(random.uniform(10, 500), 2),
                "total": round(random.uniform(10, 5000), 2),
            })
        total = sum(i["total"] for i in items)
        rows.append({
            "id": _uid(),
            "order_number": f"ORD-{random.randint(100000, 999999)}",
            "contact_id": _uid(),
            "company_id": _uid(),
            "items": json.dumps(items),
            "subtotal": round(total, 2),
            "tax": round(total * 0.08, 2),
            "shipping": round(random.uniform(5, 50), 2),
            "total": round(total * 1.08 + random.uniform(5, 50), 2),
            "currency": "USD",
            "status": random.choice(["pending", "confirmed", "shipped", "delivered", "cancelled"]),
            "shipping_street": _address()["street"],
            "shipping_city": _address()["city"],
            "shipping_state": _address()["state"],
            "shipping_zip": _address()["zip"],
            "billing_street": _address()["street"],
            "billing_city": _address()["city"],
            "billing_state": _address()["state"],
            "billing_zip": _address()["zip"],
            "notes": random.choice([None, "", "Rush delivery"]),
            "placed_at": datetime.now().isoformat(),
            "shipped_at": None,
            "delivered_at": None,
            "source_id": random.choice(["origin_postgres_1", "origin_mongodb_1"]),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "record_status": "active",
        })
    return rows


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------

async def _ensure_user_exists(pool: asyncpg.Pool) -> None:
    """Create the test user if it doesn't exist."""
    cfg = get_config()
    user = cfg["pg_origin_1"]["user"]
    password = cfg["pg_origin_1"]["password"]
    try:
        await pool.execute(f"CREATE USER {user} WITH PASSWORD '{password}';")
    except Exception:
        pass  # user already exists


async def _create_schema(conn: asyncpg.Connection, origin_type: str) -> None:
    """Create tables for an origin or destination database."""
    tables = {
        "companies": """
            CREATE TABLE IF NOT EXISTS companies (
                id UUID PRIMARY KEY,
                name VARCHAR(255),
                legal_name VARCHAR(255),
                phone VARCHAR(50),
                email VARCHAR(255),
                website VARCHAR(500),
                industry VARCHAR(100),
                employee_count INTEGER,
                annual_revenue NUMERIC(15,2),
                street VARCHAR(255),
                city VARCHAR(100),
                state VARCHAR(100),
                zip_code VARCHAR(20),
                country VARCHAR(100),
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                record_status VARCHAR(50),
                source_id VARCHAR(100)
            )
        """,
        "contacts": """
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                middle_name VARCHAR(100),
                nickname VARCHAR(100),
                full_name VARCHAR(255),
                email VARCHAR(255),
                phone VARCHAR(50),
                title VARCHAR(200),
                company_id UUID,
                street VARCHAR(255),
                city VARCHAR(100),
                state VARCHAR(100),
                zip_code VARCHAR(20),
                country VARCHAR(100),
                linkedin VARCHAR(500),
                date_of_birth DATE,
                gender CHAR(1),
                notes TEXT,
                source_id VARCHAR(100),
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                record_status VARCHAR(50)
            )
        """,
        "products": """
            CREATE TABLE IF NOT EXISTS products (
                id UUID PRIMARY KEY,
                name VARCHAR(255),
                description TEXT,
                sku VARCHAR(100),
                category VARCHAR(100),
                price NUMERIC(10,2),
                cost NUMERIC(10,2),
                weight NUMERIC(8,2),
                units VARCHAR(20),
                stock_quantity INTEGER,
                reorder_level INTEGER,
                supplier VARCHAR(255),
                specifications JSONB,
                source_id VARCHAR(100),
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                record_status VARCHAR(50)
            )
        """,
        "orders": """
            CREATE TABLE IF NOT EXISTS orders (
                id UUID PRIMARY KEY,
                order_number VARCHAR(50),
                contact_id UUID,
                company_id UUID,
                items JSONB,
                subtotal NUMERIC(12,2),
                tax NUMERIC(12,2),
                shipping NUMERIC(12,2),
                total NUMERIC(12,2),
                currency VARCHAR(10),
                status VARCHAR(50),
                shipping_street VARCHAR(255),
                shipping_city VARCHAR(100),
                shipping_state VARCHAR(100),
                shipping_zip VARCHAR(20),
                billing_street VARCHAR(255),
                billing_city VARCHAR(100),
                billing_state VARCHAR(100),
                billing_zip VARCHAR(20),
                notes TEXT,
                placed_at TIMESTAMPTZ,
                shipped_at TIMESTAMPTZ,
                delivered_at TIMESTAMPTZ,
                source_id VARCHAR(100),
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                record_status VARCHAR(50)
            )
        """,
    }

    for table_name, ddl in tables.items():
        await conn.execute(ddl)


async def _drop_tables(conn: asyncpg.Connection) -> None:
    """Drop all tables (for clean setup)."""
    for table in ["orders", "products", "contacts", "companies"]:
        try:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        except Exception:
            pass


async def _insert_companies(conn: asyncpg.Connection, companies: list[dict]) -> None:
    stmt = """
        INSERT INTO companies (id, name, legal_name, phone, email, website, industry,
            employee_count, annual_revenue, street, city, state, zip_code, country,
            created_at, updated_at, record_status, source_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
    """
    for c in companies:
        addr = c.get("address", {})
        await conn.execute(
            stmt,
            c["id"], c["name"], c.get("legal_name"), c.get("phone"), c.get("email"),
            c.get("website"), c.get("industry"), c.get("employee_count"),
            c.get("annual_revenue"),
            addr.get("street", ""), addr.get("city", ""), addr.get("state", ""),
            addr.get("zip", ""), addr.get("country", "US"),
            c.get("created_at"), c.get("updated_at"), c.get("record_status"),
            c.get("source_id"),
        )


async def _insert_contacts(conn: asyncpg.Connection, contacts: list[dict]) -> None:
    stmt = """
        INSERT INTO contacts (id, first_name, last_name, middle_name, nickname, full_name,
            email, phone, title, company_id, street, city, state, zip_code, country,
            linkedin, date_of_birth, gender, notes, source_id, created_at, updated_at,
            record_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                $16, $17, $18, $19, $20, $21, $22, $23)
    """
    for c in contacts:
        addr = c.get("address", {})
        await conn.execute(
            stmt,
            c["id"], c.get("first_name"), c.get("last_name"), c.get("middle_name"),
            c.get("nickname"), c.get("full_name"), c.get("email"), c.get("phone"),
            c.get("title"), c.get("company_id"),
            addr.get("street", ""), addr.get("city", ""), addr.get("state", ""),
            addr.get("zip", ""), addr.get("country", "US"),
            c.get("linkedin"), c.get("date_of_birth"), c.get("gender"), c.get("notes"),
            c.get("source_id"), c.get("created_at"), c.get("updated_at"),
            c.get("record_status"),
        )


async def _insert_products(conn: asyncpg.Connection, products: list[dict]) -> None:
    stmt = """
        INSERT INTO products (id, name, description, sku, category, price, cost,
            weight, units, stock_quantity, reorder_level, supplier, specifications,
            source_id, created_at, updated_at, record_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
    """
    for p in products:
        spec = p.get("specifications", {})
        if isinstance(spec, dict):
            spec = json.dumps(spec)
        await conn.execute(
            stmt,
            p["id"], p.get("name"), p.get("description"), p.get("sku"),
            p.get("category"), p.get("price"), p.get("cost"), p.get("weight"),
            p.get("units"), p.get("stock_quantity"), p.get("reorder_level"),
            p.get("supplier"), spec, p.get("source_id"),
            p.get("created_at"), p.get("updated_at"), p.get("record_status"),
        )


async def _insert_orders(conn: asyncpg.Connection, orders: list[dict]) -> None:
    stmt = """
        INSERT INTO orders (id, order_number, contact_id, company_id, items,
            subtotal, tax, shipping, total, currency, status,
            shipping_street, shipping_city, shipping_state, shipping_zip,
            billing_street, billing_city, billing_state, billing_zip,
            notes, placed_at, shipped_at, delivered_at,
            source_id, created_at, updated_at, record_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23,
                $24, $25, $26, $27)
    """
    for o in orders:
        items = o.get("items", "[]")
        if isinstance(items, list):
            items = json.dumps(items)
        sa = o.get("shipping_address", {})
        ba = o.get("billing_address", {})
        await conn.execute(
            stmt,
            o["id"], o.get("order_number"), o.get("contact_id"), o.get("company_id"),
            items, o.get("subtotal"), o.get("tax"), o.get("shipping"), o.get("total"),
            o.get("currency"), o.get("status"),
            sa.get("street", "") if isinstance(sa, dict) else "",
            sa.get("city", "") if isinstance(sa, dict) else "",
            sa.get("state", "") if isinstance(sa, dict) else "",
            sa.get("zip", "") if isinstance(sa, dict) else "",
            ba.get("street", "") if isinstance(ba, dict) else "",
            ba.get("city", "") if isinstance(ba, dict) else "",
            ba.get("state", "") if isinstance(ba, dict) else "",
            ba.get("zip", "") if isinstance(ba, dict) else "",
            o.get("notes"), o.get("placed_at"), o.get("shipped_at"), o.get("delivered_at"),
            o.get("source_id"), o.get("created_at"), o.get("updated_at"),
            o.get("record_status"),
        )


async def _populate_database(config: dict, companies: list, contacts: list,
                              products: list, orders: list, drop: bool = False) -> None:
    """Connect to a PostgreSQL database, create schema, and insert data."""
    conn = await asyncpg.connect(
        host=config["host"],
        port=config["port"],
        database=config["database"],
        user=config["user"],
        password=config["password"],
    )
    try:
        if drop:
            await _drop_tables(conn)
        await _create_schema(conn, "origin")
        await conn.execute("COMMIT")

        if companies:
            await _insert_companies(conn, companies)
        if contacts:
            await _insert_contacts(conn, contacts)
        if products:
            await _insert_products(conn, products)
        if orders:
            await _insert_orders(conn, orders)

        await conn.execute("COMMIT")
        print(f"  Populated {config['host']}:{config['port']}/{config['database']}")
        print(f"    companies={len(companies)}, contacts={len(contacts)}, "
              f"products={len(products)}, orders={len(orders)}")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------


def _populate_mongodb(config: dict, companies: list, contacts: list,
                       products: list, drop: bool = False) -> None:
    """Connect to MongoDB, create collections, and insert data."""
    client = MongoClient(
        f"mongodb://{config['host']}:{config['port']}",
        serverSelectionTimeoutMS=5000,
    )
    db = client[config["database"]]

    if drop:
        for col in ["companies", "contacts", "products"]:
            db[col].drop_many()

    for doc in companies:
        doc["_id"] = uuid.UUID(doc["id"])
        db["companies"].replace_one(
            {"_id": doc["_id"]},
            {
                "_id": doc["_id"],
                "businessInfo": {
                    "displayName": doc.get("name"),
                    "legalName": doc.get("legal_name"),
                    "industry": doc.get("industry"),
                    "employeeCount": doc.get("employee_count"),
                    "annualRevenue": doc.get("annual_revenue"),
                },
                "contactInfo": {
                    "phone": doc.get("phone"),
                    "email": doc.get("email"),
                    "website": doc.get("website"),
                },
                "address": doc.get("address", {}),
                "meta": {
                    "createdAt": doc.get("created_at"),
                    "updatedAt": doc.get("updated_at"),
                    "status": doc.get("record_status"),
                    "source": "mongodb_origin",
                },
            },
            upsert=True,
        )

    for doc in contacts:
        doc["_id"] = uuid.UUID(doc["id"])
        addr = doc.get("address", {})
        db["contacts"].replace_one(
            {"_id": doc["_id"]},
            {
                "_id": doc["_id"],
                "personalInfo": {
                    "firstName": doc.get("first_name"),
                    "lastName": doc.get("last_name"),
                    "middleName": doc.get("middle_name"),
                    "nickName": doc.get("nickname"),
                },
                "contactInfo": {
                    "primaryEmail": doc.get("email"),
                    "primaryPhone": doc.get("phone"),
                },
                "professionalInfo": {
                    "title": doc.get("title"),
                    "companyRef": doc.get("company_id"),
                },
                "location": addr,
                "social": {"linkedin": doc.get("linkedin")},
                "meta": {
                    "createdAt": doc.get("created_at"),
                    "updatedAt": doc.get("updated_at"),
                    "status": doc.get("record_status"),
                    "source": "mongodb_origin",
                },
            },
            upsert=True,
        )

    for doc in products:
        doc["_id"] = uuid.UUID(doc["id"])
        spec = doc.get("specifications", {})
        if isinstance(spec, str):
            spec = json.loads(spec)
        db["products"].replace_one(
            {"_id": doc["_id"]},
            {
                "_id": doc["_id"],
                "productInfo": {
                    "name": doc.get("name"),
                    "description": doc.get("description"),
                    "sku": doc.get("sku"),
                    "category": doc.get("category"),
                },
                "pricing": {
                    "retailPrice": doc.get("price"),
                    "cost": doc.get("cost"),
                    "currency": "USD",
                },
                "inventory": {
                    "weight": doc.get("weight"),
                    "units": doc.get("units"),
                    "stockQuantity": doc.get("stock_quantity"),
                    "reorderLevel": doc.get("reorder_level"),
                    "supplier": doc.get("supplier"),
                },
                "specifications": spec,
                "meta": {
                    "createdAt": doc.get("created_at"),
                    "updatedAt": doc.get("updated_at"),
                    "status": doc.get("record_status"),
                    "source": "mongodb_origin",
                },
            },
            upsert=True,
        )

    count_c = db["companies"].count_documents({})
    count_co = db["contacts"].count_documents({})
    count_p = db["products"].count_documents({})
    print(f"  MongoDB {config['host']}:{config['port']}/{config['database']}")
    print(f"    companies={count_c}, contacts={count_co}, products={count_p}")

    client.close()


# ---------------------------------------------------------------------------
# Destination setup (empty schema only)
# ---------------------------------------------------------------------------


async def _setup_destination(config: dict, drop: bool = False) -> None:
    """Create the destination database with merge-ready schema."""
    conn = await asyncpg.connect(
        host=config["host"],
        port=config["port"],
        database=config["database"],
        user=config["user"],
        password=config["password"],
    )
    try:
        if drop:
            await _drop_tables(conn)
        await _create_schema(conn, "destination")
        await conn.execute("COMMIT")
        print(f"  Destination schema created at {config['host']}:{config['port']}/{config['database']}")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(drop: bool = False) -> None:
    """Set up all test databases."""
    cfg = get_config()
    print("Setting up merge-engine test databases...")
    print(f"  Drop existing data: {drop}")
    print()

    # Generate data
    n_companies = 50
    n_contacts = 100
    n_products = 50
    n_orders = 50

    companies = _gen_companies(n_companies)
    contacts = _gen_contacts(n_contacts)
    products = _gen_products(n_products)
    orders = _gen_orders(n_orders)

    # Split data across origins
    split = int(0.6 * len(companies))
    pg1_companies, mongo_companies = companies[:split], companies[split:]
    split = int(0.6 * len(contacts))
    pg1_contacts, mongo_contacts = contacts[:split], contacts[split:]
    split = int(0.6 * len(products))
    pg1_products, mongo_products = products[:split], products[split:]

    # Populate PostgreSQL origin 1
    print("1. PostgreSQL Origin 1...")
    await _populate_database(cfg["pg_origin_1"], pg1_companies, pg1_contacts, pg1_products, orders[:25], drop)

    # Populate PostgreSQL origin 2 (second set)
    print("2. PostgreSQL Origin 2...")
    await _populate_database(cfg["pg_origin_2"], [], [], [], orders[25:], drop)

    # Populate MongoDB
    print("3. MongoDB Origin...")
    _populate_mongodb(cfg["mongodb"], mongo_companies, mongo_contacts, mongo_products, drop)

    # Setup destination
    print("4. PostgreSQL Destination...")
    await _setup_destination(cfg["pg_destination"], drop)

    print("\nDone! All test databases are ready.")
    print("\nTo use with merge-engine, set these environment variables:")
    print(f"  MERGE_CONFIG_ORIGIN_1_POSTGRES_HOST={cfg['pg_origin_1']['host']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_POSTGRES_PORT={cfg['pg_origin_1']['port']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_POSTGRES_DATABASE={cfg['pg_origin_1']['database']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_POSTGRES_USER={cfg['pg_origin_1']['user']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_POSTGRES_PASSWORD={cfg['pg_origin_1']['password']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_MONGODB_HOST={cfg['mongodb']['host']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_MONGODB_PORT={cfg['mongodb']['port']}")
    print(f"  MERGE_CONFIG_ORIGIN_1_MONGODB_DATABASE={cfg['mongodb']['database']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Setup merge-engine test databases")
    parser.add_argument("--drop", action="store_true", help="Drop existing tables first")
    args = parser.parse_args()

    asyncio.run(main(drop=args.drop))