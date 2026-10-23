from backend.database import get_db_connection
import json

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT 
            u.id,
            u.email,
            u.role,
            u.is_active,
            u.created_at,
            (SELECT COUNT(*) FROM files f WHERE f.owner_id = u.id) AS files_total,
            (SELECT COUNT(*) FROM files f WHERE f.owner_id = u.id AND f.status != 'DELETED') AS files_active,
            (SELECT COUNT(*) FROM provider_nodes pn WHERE pn.provider_id = u.id) AS nodes_total,
            (SELECT pn.id FROM provider_nodes pn WHERE pn.provider_id = u.id LIMIT 1) AS node_id,
            (SELECT COUNT(*) FROM file_chunks fc JOIN provider_nodes pn ON pn.id = fc.provider_node_id WHERE pn.provider_id = u.id) AS chunks_stored,
            (SELECT COUNT(*) FROM chunk_jobs cj JOIN provider_nodes pn ON pn.id = cj.provider_node_id WHERE pn.provider_id = u.id) AS jobs_count
        FROM users u
        ORDER BY u.id;
    """)
    users = [dict(r) for r in c.fetchall()]

protected_ids = {2, 5, 6}

category_protected = []
category_retained_dependency = []
category_disposable_clean = []
category_disposable_with_node_or_deleted_files = []

for u in users:
    uid = u['id']
    if uid in protected_ids:
        category_protected.append(u)
    elif u['files_active'] > 0 or u['chunks_stored'] > 0:
        category_retained_dependency.append(u)
    elif u['nodes_total'] > 0 or u['files_total'] > 0:
        category_disposable_with_node_or_deleted_files.append(u)
    else:
        category_disposable_clean.append(u)

print(f"=== USERS BREAKDOWN (TOTAL: {len(users)}) ===")
print(f"1. Protected Accounts: {len(category_protected)}")
for u in category_protected:
    print(f"   ID {u['id']:3d} | {u['role']:8s} | {u['email']}")

print(f"\n2. Retained Due to Active File / Chunk Dependencies: {len(category_retained_dependency)}")
for u in category_retained_dependency:
    print(f"   ID {u['id']:3d} | {u['role']:8s} | {u['email']} | ActiveFiles={u['files_active']} | ChunksStored={u['chunks_stored']} | NodeID={u['node_id']}")

print(f"\n3. Disposable Accounts With Unused Nodes or Inactive/Deleted Files: {len(category_disposable_with_node_or_deleted_files)}")
for u in category_disposable_with_node_or_deleted_files:
    print(f"   ID {u['id']:3d} | {u['role']:8s} | {u['email']} | FilesTotal={u['files_total']} (All Deleted) | Nodes={u['nodes_total']} (Node #{u['node_id']}, Chunks=0) | Jobs={u['jobs_count']}")

print(f"\n4. Disposable Pure Test/Demo Accounts (0 Files, 0 Nodes, 0 Chunks, 0 Jobs): {len(category_disposable_clean)}")
for u in category_disposable_clean:
    print(f"   ID {u['id']:3d} | {u['role']:8s} | {u['email']}")
