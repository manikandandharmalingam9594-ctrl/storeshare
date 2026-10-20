import os
import sys
import sqlite3
import hashlib
import json
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import psycopg
from psycopg.rows import dict_row
from backend.database import get_database_url, get_db_path

def inspect_databases():
    print("=" * 60)
    print("Pre-Migration Database Inspection")
    print("=" * 60)

    # 1. Inspect SQLite
    sqlite_path = get_db_path()
    print(f"\n1. Source SQLite Database: {sqlite_path}")
    assert sqlite_path.exists(), f"SQLite database not found at {sqlite_path}"
    
    conn_sqlite = sqlite3.connect(str(sqlite_path))
    conn_sqlite.row_factory = sqlite3.Row
    cur_sqlite = conn_sqlite.cursor()

    cur_sqlite.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
    sqlite_tables = [row[0] for row in cur_sqlite.fetchall()]
    print(f"   Tables in SQLite ({len(sqlite_tables)}): {sqlite_tables}")

    sqlite_counts = {}
    sqlite_checksums = {}
    sqlite_data = {}

    for tbl in sqlite_tables:
        cur_sqlite.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur_sqlite.fetchone()[0]
        sqlite_counts[tbl] = cnt
        
        cur_sqlite.execute(f"SELECT * FROM {tbl} ORDER BY 1")
        rows = [dict(r) for r in cur_sqlite.fetchall()]
        sqlite_data[tbl] = rows
        
        # Serialize to compute a stable digest
        serialized = json.dumps(rows, default=str, sort_keys=True)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        sqlite_checksums[tbl] = digest
        print(f"   Table '{tbl}': {cnt} rows | SHA256: {digest[:16]}...")

    # 2. Inspect PostgreSQL
    pg_url = get_database_url()
    print(f"\n2. Target PostgreSQL Database: {pg_url.split('@')[-1] if pg_url else 'None'}")
    if not pg_url:
        print("ERROR: STORESHARE_DATABASE_URL not set.")
        sys.exit(1)

    with psycopg.connect(pg_url, row_factory=dict_row) as conn_pg:
        cur_pg = conn_pg.cursor()
        cur_pg.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        pg_tables = [row["table_name"] for row in cur_pg.fetchall()]
        print(f"   Tables in PostgreSQL ({len(pg_tables)}): {pg_tables}")

        pg_counts = {}
        for tbl in pg_tables:
            cur_pg.execute(f"SELECT COUNT(*) as count FROM {tbl}")
            cnt = cur_pg.fetchone()["count"]
            pg_counts[tbl] = cnt
            print(f"   Table '{tbl}': {cnt} rows")

        # Check if any PostgreSQL table is not empty
        non_empty = {tbl: cnt for tbl, cnt in pg_counts.items() if cnt > 0}
        if non_empty:
            print("\n   [WARNING] Existing rows detected in PostgreSQL target tables:")
            for tbl, cnt in non_empty.items():
                print(f"     - {tbl}: {cnt} rows")
                cur_pg.execute(f"SELECT * FROM {tbl} LIMIT 5")
                sample_rows = cur_pg.fetchall()
                print(f"       Sample from {tbl}: {sample_rows}")
        else:
            print("\n   [INFO] All PostgreSQL target tables are currently EMPTY.")

if __name__ == "__main__":
    inspect_databases()
