import os
from pathlib import Path
import hashlib
from backend.database import get_db_connection

backup_file = Path("c:/Projects/storeshare/backups/storeshare_pre_cleanup_backup_20261023.sql")
if not backup_file.is_file() or backup_file.stat().st_size == 0:
    raise RuntimeError(f"ABORT: Verified backup file not found or empty at {backup_file}")

print(f"Verified backup verified at: {backup_file} ({backup_file.stat().st_size} bytes)")

with get_db_connection() as conn:
    c = conn.cursor()

    # Verify protected accounts
    c.execute("SELECT id, email, role, is_active FROM users WHERE id IN (2, 5, 6) ORDER BY id;")
    protected = [dict(r) for r in c.fetchall()]
    if len(protected) != 3:
        raise RuntimeError(f"ABORT: Protected accounts missing: expected 3, found {len(protected)}")
    
    print("\nProtected accounts verified:")
    for p in protected:
        print(f"  ID {p['id']}: {p['email']} ({p['role']}) Active={p['is_active']}")

    # Identify the 47 pure disposable test/demo accounts
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

    if len(delete_ids) != 47:
        raise RuntimeError(f"ABORT: Expected 47 disposable accounts, found {len(delete_ids)}")

    # Safety assertion: None of the protected or retained IDs can be in delete_ids
    forbidden_ids = {2, 5, 6, 20, 42, 215, 230, 231, 3, 14, 15, 16, 17, 19, 21, 37, 38, 39, 40}
    intersection = set(delete_ids).intersection(forbidden_ids)
    if intersection:
        raise RuntimeError(f"CRITICAL SAFETY VIOLATION: Deletion set intersects with protected/retained accounts: {intersection}")

    print(f"\nBeginning safe deletion transaction for {len(delete_ids)} disposable accounts...")

    # Count before
    c.execute("SELECT COUNT(*) AS cnt FROM users;")
    u_before = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM files;")
    f_before = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM provider_nodes;")
    pn_before = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM file_chunks;")
    fc_before = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM chunk_jobs;")
    cj_before = c.fetchone()['cnt']

    # Execute DELETE inside transaction
    placeholders = ','.join(['%s'] * len(delete_ids))
    c.execute(f"DELETE FROM users WHERE id IN ({placeholders});", tuple(delete_ids))
    deleted_rows = c.rowcount

    # Count after
    c.execute("SELECT COUNT(*) AS cnt FROM users;")
    u_after = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM files;")
    f_after = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM provider_nodes;")
    pn_after = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM file_chunks;")
    fc_after = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM chunk_jobs;")
    cj_after = c.fetchone()['cnt']

    # Referential integrity assertions
    assert f_before == f_after, f"Files count altered! Before={f_before}, After={f_after}"
    assert pn_before == pn_after, f"Provider nodes count altered! Before={pn_before}, After={pn_after}"
    assert fc_before == fc_after, f"File chunks count altered! Before={fc_before}, After={fc_after}"
    assert cj_before == cj_after, f"Chunk jobs count altered! Before={cj_before}, After={cj_after}"
    assert u_before - u_after == 47, f"Unexpected user deletion count: {u_before - u_after}"

    # Commit transaction
    conn.commit()
    print("Transaction COMMITTED successfully.")

print(f"\n--- POST-CLEANUP VERIFICATION ---")
print(f"Users remaining: {u_after} (deleted exactly {deleted_rows} disposable accounts)")
print(f"Files preserved: {f_after} (100% intact)")
print(f"Provider nodes preserved: {pn_after} (100% intact)")
print(f"File chunks preserved: {fc_after} (100% intact)")
print(f"Chunk jobs preserved: {cj_after} (100% intact)")

# Verify physical disk chunk files
physical_chunks = [
    Path(r"C:\storeshare-storage\chunks\chunk_50f7d36cde32473bb672c23afe4c5dd9"),
    Path(r"C:\Projects\storeshare\data\provider_storage\chunks\chunk_52dbc0f02cba446291833832ff7bd265"),
    Path(r"C:\Projects\storeshare\data\provider_storage\chunks\chunk_1e3db41a2ffa46da9d53ab1a4712315e"),
    Path(r"C:\Projects\storeshare\data\provider_storage\chunks\chunk_6ef1c34b75d048629d6890da5f95e5f6"),
    Path(r"C:\Projects\storeshare\data\provider_storage\chunks\chunk_7fb4ecf730f14180a48e725a4c9ee778"),
]
print("\nPhysical encrypted chunks verification:")
for p in physical_chunks:
    exists = p.is_file()
    size = p.stat().st_size if exists else 0
    print(f"  {p.name}: Exists={exists}, Size={size} bytes")
    assert exists, f"Physical chunk missing: {p}"

print("\nALL SAFETY CHECKS PASSED.")
