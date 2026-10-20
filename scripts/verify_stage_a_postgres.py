import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from backend.database import (
    get_database_url,
    get_database_info,
    get_db_connection,
    get_table_names,
    poll_provider_jobs,
    is_postgres,
    DatabaseConnection,
)

def run_stage_a_verification():
    print("=" * 60)
    print("Stage A: PostgreSQL Database Access Verification")
    print("=" * 60)

    pg_url = get_database_url()
    print(f"Configured URL present: {bool(pg_url)}")
    if not pg_url:
        print("ERROR: STORESHARE_DATABASE_URL is not set.")
        sys.exit(1)

    info = get_database_info(pg_url)
    print(f"Database Engine: {info['engine']}")
    print(f"Driver: {info['driver']}")
    print(f"Masked Target: {info['target']}")
    assert info["engine"] == "PostgreSQL"
    assert "password" not in info["target"]
    assert "****" in info["target"]
    print(" [PASS] Connection URL masking and metadata introspection verified.")

    # 1. Test real connection
    print("\n1. Testing psycopg connection to PostgreSQL...")
    with get_db_connection(pg_url) as conn:
        assert isinstance(conn, DatabaseConnection)
        assert conn.is_pg is True
        cur = conn.cursor()
        cur.execute("SELECT version();")
        row = cur.fetchone()
        version_str = list(row.values())[0] if row else "Unknown"
        print(f"   Connected successfully! PostgreSQL version: {version_str[:50]}...")
    print(" [PASS] Direct psycopg connection established.")

    # 2. Table introspection via information_schema
    print("\n2. Testing table introspection via information_schema...")
    tables = get_table_names(pg_url)
    print(f"   Discovered tables in PostgreSQL: {tables}")
    print(" [PASS] Introspection query works against PostgreSQL catalog.")

    # 3. Test placeholder translation (? -> %s) and row wrapping
    print("\n3. Testing SQL placeholder translation (? -> %s)...")
    with get_db_connection(pg_url) as conn:
        cur = conn.cursor()
        cur.execute("SELECT ? AS param1, ? AS param2", ("hello", 42))
        res = cur.fetchone()
        assert res["param1"] == "hello"
        assert res["param2"] == 42
    print(" [PASS] Query placeholder ? was dynamically adapted to %s and executed successfully.")

    # 4. Test FOR UPDATE SKIP LOCKED job polling
    print("\n4. Testing FOR UPDATE SKIP LOCKED query execution...")
    with get_db_connection(pg_url) as conn:
        # poll_provider_jobs uses FOR UPDATE SKIP LOCKED when running against PostgreSQL
        jobs = poll_provider_jobs(provider_node_id=999999, limit=5, db_path=pg_url)
        print(f"   Polling result for dummy node: {jobs} (expected empty list)")
        assert isinstance(jobs, list)
    print(" [PASS] FOR UPDATE SKIP LOCKED row locking syntax accepted by PostgreSQL engine.")

    # 5. Test connection shortcut .execute()
    print("\n5. Testing connection .execute() method compatibility...")
    with get_db_connection(pg_url) as conn:
        cur = conn.execute("SELECT current_database() as db_name;")
        db_name = cur.fetchone()["db_name"]
        print(f"   Current active database name: {db_name}")
        assert db_name == "storeshare"
    print(" [PASS] Connection .execute() wrapper operates consistently.")

    print("\n" + "=" * 60)
    print("All Stage A PostgreSQL access tests PASSED successfully!")
    print("=" * 60)

if __name__ == "__main__":
    run_stage_a_verification()
