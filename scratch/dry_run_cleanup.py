from backend.database import get_db_connection
import json

with get_db_connection() as conn:
    c = conn.cursor()

    # Identify the 47 pure disposable accounts
    c.execute("""
        SELECT u.id, u.email, u.role
        FROM users u
        WHERE u.id NOT IN (2, 5, 6)
          AND u.id NOT IN (20, 42, 215, 230, 231)
          AND u.id NOT IN (3, 14, 15, 16, 17, 19, 21, 37, 38, 39, 40)
        ORDER BY u.id;
    """)
    to_delete = [dict(r) for r in c.fetchall()]
    delete_ids = [u['id'] for u in to_delete]

    print(f"Total proposed accounts to delete: {len(to_delete)}")
    
    # Perform dry-run in transaction with ROLLBACK
    try:
        # Check counts before
        c.execute("SELECT COUNT(*) AS cnt FROM users;")
        users_before = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM files;")
        files_before = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM provider_nodes;")
        nodes_before = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM file_chunks;")
        chunks_before = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM chunk_jobs;")
        jobs_before = c.fetchone()['cnt']

        print(f"\n--- BEFORE DRY-RUN DELETION ---")
        print(f"Users: {users_before} | Files: {files_before} | Nodes: {nodes_before} | Chunks: {chunks_before} | Jobs: {jobs_before}")

        # Execute DELETE in transaction
        placeholders = ','.join(['%s'] * len(delete_ids))
        c.execute(f"DELETE FROM users WHERE id IN ({placeholders});", tuple(delete_ids))
        deleted_count = c.rowcount

        # Check counts after
        c.execute("SELECT COUNT(*) AS cnt FROM users;")
        users_after = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM files;")
        files_after = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM provider_nodes;")
        nodes_after = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM file_chunks;")
        chunks_after = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) AS cnt FROM chunk_jobs;")
        jobs_after = c.fetchone()['cnt']

        print(f"\n--- AFTER DRY-RUN DELETION (IN TRANSACTION) ---")
        print(f"Deleted user rows: {deleted_count}")
        print(f"Users: {users_after} (Difference: {users_after - users_before})")
        print(f"Files: {files_after} (Difference: {files_after - files_before})")
        print(f"Nodes: {nodes_after} (Difference: {nodes_after - nodes_before})")
        print(f"Chunks: {chunks_after} (Difference: {chunks_after - chunks_before})")
        print(f"Jobs: {jobs_after} (Difference: {jobs_after - jobs_before})")

        # Verify protected accounts are intact in transaction
        c.execute("SELECT id, email, role FROM users WHERE id IN (2, 5, 6) ORDER BY id;")
        protected_remaining = [dict(r) for r in c.fetchall()]
        print(f"\nProtected accounts verified: {len(protected_remaining)}/3")
        for p in protected_remaining:
            print(f"  ID {p['id']}: {p['email']} ({p['role']})")

    finally:
        # ALWAYS ROLLBACK IN DRY-RUN
        conn.rollback()
        print("\n[TRANSACTION ROLLED BACK] No changes persisted.")

    # Verify counts restored
    c.execute("SELECT COUNT(*) AS cnt FROM users;")
    print(f"Users in DB after rollback: {c.fetchone()['cnt']}")
