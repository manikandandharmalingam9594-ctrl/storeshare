from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT 
            pn.*,
            u.email AS provider_email,
            (SELECT COUNT(*) FROM file_chunks fc WHERE fc.provider_node_id = pn.id) AS assigned_chunks_count
        FROM provider_nodes pn
        JOIN users u ON u.id = pn.provider_id
        ORDER BY pn.id;
    """)
    rows = [dict(r) for r in c.fetchall()]

for r in rows:
    print(r)
