"""
Import sample_databases (origin1 and origin2) into ClickHouse for merge-engine.

Command Line Options:
  --fresh       Wipe databases and progress state to start fresh
  --resume      Resume from previous progress state (default)
  --database    Select database to import: 'origin1', 'origin2', or 'all' (default: 'all')
  --table       Select specific table to import (e.g., 'Company')
"""

import os
import sys
import json
import glob
import re
import argparse
from tqdm import tqdm
import clickhouse_connect

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PROGRESS_FILE = os.path.join("sample_databases", ".import_progress.json")

def log(msg):
    print(msg, flush=True)

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_progress(progress: dict):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def wipe_progress():
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        log("Wiped local import progress file.")

def mark_file_completed(filename: str):
    prog = load_progress()
    prog[filename] = {"completed": True, "offset": -1}
    save_progress(prog)

def update_file_offset(filename: str, offset: int, rows_inserted: int):
    prog = load_progress()
    prog[filename] = {"completed": False, "offset": offset, "rows": rows_inserted}
    save_progress(prog)

def is_file_completed(filename: str) -> bool:
    prog = load_progress()
    return prog.get(filename, {}).get("completed", False)

def get_file_resume_offset(filename: str) -> int:
    prog = load_progress()
    off = prog.get(filename, {}).get("offset", 0)
    return off if off > 0 else 0

def get_client():
    return clickhouse_connect.get_client(
        host='localhost',
        port=8190,
        username='default',
        password='pass'
    )

def fix_quotes(text: str) -> str:
    # 1. Protect double backslashes \\
    t = text.replace(r"\\", "__ESCAPED_BACKSLASH__")
    
    # 2. Standardize \'\' and \' to ''
    t = t.replace(r"\'\'", "''").replace(r"\'", "''")

    # 3. Convert single quotes inside words (e.g. We've, customer's, L'amiante) to ''
    t = re.sub(r"(?<=[a-zA-Z0-9])'(?=[a-zA-Z0-9])", "''", t)

    # 4. Restore double backslashes
    return t.replace("__ESCAPED_BACKSLASH__", r"\\")

def extract_tuples(text: str):
    tuples = []
    start = -1
    in_quote = False
    i = 0
    n = len(text)
    
    while i < n:
        char = text[i]
        if char == "'":
            if in_quote and i + 1 < n and text[i+1] == "'":
                i += 2
                continue
            in_quote = not in_quote
            i += 1
            continue
            
        if not in_quote:
            if char == '(' and start == -1:
                start = i
            elif char == ')' and start != -1:
                next_i = i + 1
                while next_i < n and text[next_i] in ' \t\r\n':
                    next_i += 1
                if next_i < n and text[next_i] in (',', ';'):
                    tuples.append(text[start:i+1])
                    start = -1
                    i = next_i
        i += 1

    if start != -1:
        remaining = text[start:]
    else:
        remaining = text[i:]
        
    return tuples, remaining

def setup_databases(client, fresh: bool = False):
    if fresh:
        log("Fresh start requested: Dropping databases origin1 and origin2 if they exist...")
        client.command("DROP DATABASE IF EXISTS origin1")
        client.command("DROP DATABASE IF EXISTS origin2")
        wipe_progress()

    log("Setting up databases origin1 and origin2...")
    client.command("CREATE DATABASE IF NOT EXISTS origin1")
    client.command("CREATE DATABASE IF NOT EXISTS origin2")
    log("Databases origin1 and origin2 ready.")

def create_origin2_schemas(client):
    log("Creating origin2 schemas...")
    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.Company (
      companyID String,
      name String,
      description Nullable(String),
      website Nullable(String),
      domain Nullable(String),
      employeeCount Nullable(String),
      phone Nullable(String),
      revenue Nullable(String),
      rank Nullable(UInt64),
      alexaRank Nullable(UInt64),
      revenueRange Nullable(String),
      ticker Nullable(String),
      type Nullable(String),
      created_at Nullable(DateTime)
    ) ENGINE = ReplacingMergeTree() ORDER BY companyID;
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.CompanyIndustry (
      companyID String,
      classificationID String,
      isPrimary String,
      created_at DateTime
    ) ENGINE = ReplacingMergeTree() ORDER BY (companyID, classificationID);
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.Experience (
      experienceID String,
      companyID String,
      tags Nullable(String),
      title Nullable(String),
      role Nullable(String),
      created_at DateTime
    ) ENGINE = ReplacingMergeTree() ORDER BY (companyID, experienceID);
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.Industry (
      classificationID String,
      name String,
      surfaceForm Nullable(String),
      created_at DateTime
    ) ENGINE = ReplacingMergeTree() ORDER BY classificationID;
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.Location (
      locationID String,
      street Nullable(String),
      city Nullable(String),
      state Nullable(String),
      zip Nullable(String),
      country Nullable(String),
      companyID Nullable(UInt64),
      created_at DateTime
    ) ENGINE = ReplacingMergeTree() ORDER BY locationID;
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.PersonContact (
      contactString Nullable(String),
      personID String,
      created_at DateTime
    ) ENGINE = ReplacingMergeTree() ORDER BY (personID, assumeNotNull(contactString));
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS origin2.Person (
      personID String,
      fullName String,
      firstName Nullable(String),
      lastName Nullable(String),
      middleName Nullable(String),
      created_at DateTime
    ) ENGINE = ReplacingMergeTree() ORDER BY personID;
    """)
    log("origin2 schemas ready.")

def parse_origin1_by_starters(filepath):
    statements = []
    current_buffer = []
    starters = ("CREATE TABLE", "INSERT INTO", "DROP TABLE", "ALTER TABLE", "USE ", "CREATE DATABASE")

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('--'):
                continue
            
            is_starter = False
            for st in starters:
                if stripped.upper().startswith(st):
                    is_starter = True
                    break
            
            if is_starter and current_buffer:
                stmt_text = "".join(current_buffer).strip()
                if stmt_text:
                    statements.append(fix_quotes(stmt_text))
                current_buffer = []
            
            current_buffer.append(line)
        
        if current_buffer:
            stmt_text = "".join(current_buffer).strip()
            if stmt_text:
                statements.append(fix_quotes(stmt_text))

    return statements

def import_origin1(client, filepath, force_fresh=False):
    filename = os.path.basename(filepath)
    if not force_fresh and is_file_completed(filename):
        log(f"Skipping already completed origin1 file: {filename}")
        return

    log(f"\nImporting origin1 dump ({filename})...")
    client.command("USE origin1")
    statements = parse_origin1_by_starters(filepath)
    log(f"Parsed {len(statements)} statements for origin1.")

    errors = 0
    with tqdm(total=len(statements), desc="Importing origin1 statements") as pbar:
        for i, stmt in enumerate(statements, 1):
            try:
                client.command(stmt)
            except Exception as e:
                errors += 1
                log(f"\nError in statement {i}: {e}")
            pbar.update(1)

    if errors == 0:
        mark_file_completed(filename)
        log(f"Finished origin1 import cleanly with 0 errors.")
    else:
        log(f"Completed origin1 import with {errors} errors.")

def stream_import_sql_file(client, filepath, database=None, batch_tuple_count=2000, force_fresh=False):
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)

    if not force_fresh and is_file_completed(filename):
        log(f"Skipping already completed file: {filename}")
        return

    if database:
        client.command(f"USE {database}")

    log(f"\nStream importing {filename} ({file_size / (1024*1024):.2f} MB)...")
    resume_offset = 0 if force_fresh else get_file_resume_offset(filename)

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        first_line = f.readline()

        # Large single-INSERT file with progress bar and resume
        header = first_line.strip()
        if "VALUES" in header:
            header = header[:header.index("VALUES") + 6]
            f.seek(0)
            f.readline()

        if resume_offset > 0:
            log(f"Resuming {filename} from byte offset {resume_offset}...")
            f.seek(resume_offset)

        chunk_buffer = ""
        batch_tuples = []
        total_inserted = 0

        with tqdm(total=file_size, initial=f.tell(), unit='B', unit_scale=True, desc=f"Streaming {filename}") as pbar:
            while True:
                current_tell = f.tell()
                chunk = f.read(4 * 1024 * 1024) # 4 MB chunk
                if not chunk:
                    break
                pbar.update(len(chunk.encode('utf-8', errors='replace')))

                chunk_fixed = fix_quotes(chunk)
                chunk_buffer += chunk_fixed
                
                tups, chunk_buffer = extract_tuples(chunk_buffer)
                batch_tuples.extend(tups)

                if len(batch_tuples) >= batch_tuple_count:
                    query = f"{header} {','.join(batch_tuples)};"
                    try:
                        client.command(query)
                        total_inserted += len(batch_tuples)
                        update_file_offset(filename, current_tell, total_inserted)
                    except Exception as e:
                        log(f"Error executing batch in {filename}: {e}")
                    batch_tuples = []

            if chunk_buffer.strip():
                tups, _ = extract_tuples(chunk_buffer)
                batch_tuples.extend(tups)

            if batch_tuples:
                query = f"{header} {','.join(batch_tuples)};"
                try:
                    client.command(query)
                    total_inserted += len(batch_tuples)
                except Exception as e:
                    log(f"Error executing final batch in {filename}: {e}")

        mark_file_completed(filename)
        log(f"Finished streaming {filename} (Total inserted: {total_inserted} rows).")

def report_table_counts(client):
    log("\n================ LIVE VERIFICATION RESULTS ================")
    for db in ["origin1", "origin2"]:
        try:
            res = client.query(f"SHOW TABLES FROM {db}")
            tables = [row[0] for row in res.result_rows]
            log(f"\nDatabase: {db} ({len(tables)} tables)")
            for tbl in sorted(tables):
                tbl = tbl.strip()
                if tbl:
                    cnt_res = client.query(f"SELECT count() FROM {db}.{tbl}")
                    cnt = cnt_res.result_rows[0][0]
                    log(f"  {db}.{tbl}: {cnt} rows")
        except Exception as e:
            log(f"Could not fetch tables for {db}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Import sample_databases into ClickHouse")
    parser.add_argument("--fresh", action="store_true", help="Start fresh by dropping databases (or specific table) and progress state")
    parser.add_argument("--resume", action="store_true", help="Resume from saved progress state (default)")
    parser.add_argument("--database", "-d", choices=["origin1", "origin2", "all"], default="all", help="Database to import (default: all)")
    parser.add_argument("--table", "-t", type=str, default=None, help="Filter specific table file to import")
    args = parser.parse_args()

    client = get_client()

    if args.fresh and args.table:
        target_tbl = args.table.replace("_modified", "").replace(".sql", "").strip()
        log(f"Single-table fresh start requested for table: {target_tbl}")
        # Reset progress for this table file only
        progress = load_progress()
        for key in list(progress.keys()):
            if target_tbl.lower() in key.lower():
                progress.pop(key, None)
        save_progress(progress)
        
        # Truncate table in target DB if it exists
        dbs_to_check = ["origin2", "origin1"] if args.database == "all" else [args.database]
        for db in dbs_to_check:
            try:
                client.command(f"TRUNCATE TABLE IF EXISTS {db}.{target_tbl}")
                log(f"Truncated table {db}.{target_tbl}")
            except Exception as e:
                pass
        setup_databases(client, fresh=False)
    else:
        setup_databases(client, fresh=args.fresh)

    create_origin2_schemas(client)

    # 1. Import origin1 first if selected
    if args.database in ["origin1", "all"]:
        origin1_files = sorted(glob.glob("D:/transfer/merging/sample_databases/origin1/*.sql"))
        for fpath in origin1_files:
            if args.table:
                target_tbl = args.table.lower().replace("_modified", "").replace(".sql", "").strip()
                base_tbl = os.path.basename(fpath).lower().replace("_modified", "").replace(".sql", "").strip()
                if target_tbl != base_tbl:
                    continue
            stream_import_sql_file(client, fpath, database="origin1", force_fresh=args.fresh)

    # 2. Import origin2 if selected
    if args.database in ["origin2", "all"]:
        origin2_files = sorted(glob.glob("D:/transfer/merging/sample_databases/origin2_sample/*.sql"))
        for fpath in origin2_files:
            if args.table:
                target_tbl = args.table.lower().replace("_modified", "").replace(".sql", "").strip()
                base_tbl = os.path.basename(fpath).lower().replace("_modified", "").replace(".sql", "").strip()
                if target_tbl != base_tbl:
                    continue
            stream_import_sql_file(client, fpath, database="origin2", force_fresh=args.fresh)

    report_table_counts(client)

if __name__ == "__main__":
    main()
