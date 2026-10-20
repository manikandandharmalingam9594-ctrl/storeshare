import os
import sys
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import psycopg
from psycopg.rows import dict_row
from backend.database import get_database_url, get_database_info

def run_preflight():
    print("=" * 60)
    print("Stage C: Preflight Checks")
    print("=" * 60)

    # 1. Database Configuration & Masked URL
    db_info = get_database_info()
    print(f"Engine: {db_info['engine']}")
    print(f"Driver: {db_info['driver']}")
    print(f"Target: {db_info['target']}")
    
    # Assert password is NOT exposed
    assert "storeshare123" not in db_info["target"], "ERROR: Credentials exposed!"
    assert "****" in db_info["target"], "ERROR: Password should be masked with ****"
    print(" [PASS] Connection URL configured and credentials properly masked.")

    # 2. Check PostgreSQL target connection and baseline counts
    pg_url = get_database_url()
    expected_baseline = {
        "app_metadata": 3,
        "users": 43,
        "provider_nodes": 9,
        "files": 5,
        "file_chunks": 0,
        "chunk_jobs": 15
    }

    with psycopg.connect(pg_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            actual_baseline = {}
            for tbl in expected_baseline:
                cur.execute(f"SELECT COUNT(*) as cnt FROM {tbl}")
                actual_baseline[tbl] = cur.fetchone()["cnt"]

            print("\nBaseline PostgreSQL Row Counts:")
            for tbl, exp in expected_baseline.items():
                act = actual_baseline[tbl]
                print(f"  - {tbl:15s}: Expected {exp:2d} | Actual {act:2d}")
                assert act == exp, f"Row count mismatch on {tbl}: Expected {exp}, got {act}"

    print("\n [PASS] PostgreSQL contains exact expected migrated baseline records.")
    print("=" * 60)

if __name__ == "__main__":
    run_preflight()
