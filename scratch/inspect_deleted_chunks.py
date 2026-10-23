from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT f.id, f.filename, f.status as file_status, fc.chunk_id, fc.status as chunk_status, fc.provider_node_id
        FROM files f
        LEFT JOIN file_chunks fc ON fc.file_id = f.id
        WHERE f.id IN (37, 36, 32, 5, 4, 3, 2, 1)
        ORDER BY f.id DESC;
    """)
    rows = [dict(r) for r in c.fetchall()]

for r in rows:
    print(r)
