import os
import sys
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import psycopg
from psycopg.rows import dict_row
from backend.database import get_database_url, get_db_path

def analyze_conflicts():
    sqlite_path = get_db_path()
    pg_url = get_database_url()

    conn_sqlite = sqlite3.connect(str(sqlite_path))
    conn_sqlite.row_factory = sqlite3.Row
    cur_sqlite = conn_sqlite.cursor()

    conn_pg = psycopg.connect(pg_url, row_factory=dict_row)
    cur_pg = conn_pg.cursor()

    tables = ["app_metadata", "users", "provider_nodes", "files", "file_chunks", "chunk_jobs"]

    print("Detailed Conflict Analysis between SQLite (Source) and PostgreSQL (Target):")
    print("-" * 70)

    for tbl in tables:
        cur_sqlite.execute(f"SELECT * FROM {tbl}")
        sqlite_rows = [dict(r) for r in cur_sqlite.fetchall()]
        
        cur_pg.execute(f"SELECT * FROM {tbl}")
        pg_rows = [dict(r) for r in cur_pg.fetchall()]

        print(f"\nTable: {tbl}")
        print(f"  SQLite row count:     {len(sqlite_rows)}")
        print(f"  PostgreSQL row count: {len(pg_rows)}")

        if tbl == "users":
            sqlite_emails = {r["email"]: r["id"] for r in sqlite_rows}
            pg_emails = {r["email"]: r["id"] for r in pg_rows}
            overlapping_emails = set(sqlite_emails.keys()) & set(pg_emails.keys())
            print(f"  Conflicting/overlapping emails ({len(overlapping_emails)}):")
            for em in list(overlapping_emails)[:10]:
                print(f"    - Email: {em} | SQLite ID: {sqlite_emails[em]} vs PG ID: {pg_emails[em]}")

            sqlite_ids = {r["id"] for r in sqlite_rows}
            pg_ids = {r["id"] for r in pg_rows}
            overlapping_ids = sqlite_ids & pg_ids
            print(f"  Conflicting/overlapping User IDs ({len(overlapping_ids)}): {sorted(list(overlapping_ids))[:10]}...")

        elif tbl == "provider_nodes":
            sqlite_ids = {r["id"] for r in sqlite_rows}
            pg_ids = {r["id"] for r in pg_rows}
            overlapping_ids = sqlite_ids & pg_ids
            print(f"  Conflicting/overlapping Node IDs ({len(overlapping_ids)}): {sorted(list(overlapping_ids))[:10]}...")

        elif tbl == "files":
            sqlite_ids = {r["id"] for r in sqlite_rows}
            pg_ids = {r["id"] for r in pg_rows}
            overlapping_ids = sqlite_ids & pg_ids
            print(f"  Conflicting/overlapping File IDs ({len(overlapping_ids)}): {sorted(list(overlapping_ids))[:10]}...")

        elif tbl == "chunk_jobs":
            sqlite_ids = {r["id"] for r in sqlite_rows}
            pg_ids = {r["id"] for r in pg_rows}
            overlapping_ids = sqlite_ids & pg_ids
            print(f"  Conflicting/overlapping Job IDs ({len(overlapping_ids)}): {sorted(list(overlapping_ids))[:10]}...")

        elif tbl == "app_metadata":
            sqlite_keys = {r["key"] for r in sqlite_rows}
            pg_keys = {r["key"] for r in pg_rows}
            overlapping_keys = sqlite_keys & pg_keys
            print(f"  Conflicting/overlapping Metadata Keys: {overlapping_keys}")

if __name__ == "__main__":
    analyze_conflicts()
