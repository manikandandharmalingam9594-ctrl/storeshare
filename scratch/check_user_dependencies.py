from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    # Check all tables with foreign keys to users
    c.execute("""
        SELECT
            tc.table_name, kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name,
            rc.delete_rule
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.referential_constraints AS rc
          ON tc.constraint_name = rc.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE ccu.table_name = 'users';
    """)
    fk_constraints = [dict(r) for r in c.fetchall()]

print("--- FOREIGN KEYS REFERENCING USERS ---")
for fk in fk_constraints:
    print(fk)

# Now check each user's dependencies
with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT 
            u.id,
            u.email,
            u.role,
            (SELECT COUNT(*) FROM files f WHERE f.owner_id = u.id) AS files_owned_count,
            (SELECT COUNT(*) FROM files f WHERE f.owner_id = u.id AND f.status != 'DELETED') AS active_files_count,
            (SELECT COUNT(*) FROM provider_nodes pn WHERE pn.provider_id = u.id) AS provider_nodes_count,
            (SELECT COUNT(*) FROM file_chunks fc JOIN provider_nodes pn ON pn.id = fc.provider_node_id WHERE pn.provider_id = u.id) AS chunks_stored_count,
            (SELECT COUNT(*) FROM chunk_jobs cj JOIN provider_nodes pn ON pn.id = cj.provider_node_id WHERE pn.provider_id = u.id) AS jobs_count
        FROM users u
        ORDER BY u.id;
    """)
    user_deps = [dict(r) for r in c.fetchall()]

print("\n--- USER DEPENDENCIES ---")
for ud in user_deps:
    if ud['files_owned_count'] > 0 or ud['provider_nodes_count'] > 0:
        print(f"USER {ud['id']:3d} ({ud['email']}) [{ud['role']}]: Files={ud['files_owned_count']} (Active={ud['active_files_count']}) | Nodes={ud['provider_nodes_count']} | Chunks={ud['chunks_stored_count']} | Jobs={ud['jobs_count']}")
