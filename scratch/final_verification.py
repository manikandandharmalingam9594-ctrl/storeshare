from backend.database import (
    get_db_connection,
    get_user_by_email,
    get_provider_node,
    get_provider_reconciliation_records,
)
from pathlib import Path

print("=== FINAL STORE-SHARE VERIFICATION ===")

# 1. Protected Consumer
c_user = get_user_by_email("manikandandharmalingam9594@gmail.com")
assert c_user is not None, "Consumer user not found!"
assert c_user["role"] == "CONSUMER", f"Expected role CONSUMER, got {c_user['role']}"
assert c_user["is_active"] is True, "Consumer user is not active!"
print(f"1. Protected Consumer Verified: ID {c_user['id']} | {c_user['email']} | Role={c_user['role']} | Active={c_user['is_active']}")

# 2. Protected Provider
p_user = get_user_by_email("24d126@psgitech.ac.in")
assert p_user is not None, "Provider user not found!"
assert p_user["role"] == "PROVIDER", f"Expected role PROVIDER, got {p_user['role']}"
assert p_user["is_active"] is True, "Provider user is not active!"
p_node = get_provider_node(p_user["id"])
assert p_node is not None, "Provider Node #2 not found!"
assert p_node["id"] == 2, f"Expected node ID 2, got {p_node['id']}"
assert p_node["storage_dir"] == r"C:\storeshare-storage", f"Unexpected storage_dir: {p_node['storage_dir']}"
print(f"2. Protected Provider Verified: ID {p_user['id']} | {p_user['email']} | Role={p_user['role']} | Node #{p_node['id']} ({p_node['storage_dir']}, Quota={p_node['quota_bytes'] // (1024**3)} GB)")

# 3. Protected Admin
a_user = get_user_by_email("admin@storeshare.io")
assert a_user is not None, "Admin user not found!"
assert a_user["role"] == "ADMIN", f"Expected role ADMIN, got {a_user['role']}"
assert a_user["is_active"] is True, "Admin user is not active!"
print(f"3. Protected Admin Verified: ID {a_user['id']} | {a_user['email']} | Role={a_user['role']} | Active={a_user['is_active']}")

# 4. Check files of consumer Manikandan
with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT id, filename, file_size, status FROM files WHERE owner_id = %s ORDER BY id;", (c_user["id"],))
    c_files = [dict(r) for r in c.fetchall()]

print(f"\n4. Consumer Manikandan Files ({len(c_files)} total, {[f['filename'] for f in c_files if f['status'] != 'DELETED']} active):")
for f in c_files:
    print(f"   File #{f['id']}: {f['filename']} ({f['file_size']} bytes) [{f['status']}]")

# 5. Check Node #2 physical chunk
n2_chunk = Path(r"C:\storeshare-storage\chunks\chunk_50f7d36cde32473bb672c23afe4c5dd9")
assert n2_chunk.is_file(), f"Physical chunk missing on Node #2: {n2_chunk}"
print(f"\n5. Node #2 Physical Chunk Verified: {n2_chunk} ({n2_chunk.stat().st_size} bytes)")

# 6. Check total users remaining in DB
with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM users;")
    total_u = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM files;")
    total_f = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM provider_nodes;")
    total_pn = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM file_chunks;")
    total_fc = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) AS cnt FROM chunk_jobs;")
    total_cj = c.fetchone()['cnt']

print(f"\n6. Database Snapshot:")
print(f"   Users: {total_u}")
print(f"   Files: {total_f}")
print(f"   Provider Nodes: {total_pn}")
print(f"   File Chunks: {total_fc}")
print(f"   Chunk Jobs: {total_cj}")

print("\nALL POST-CLEANUP VERIFICATIONS SUCCESSFUL.")
