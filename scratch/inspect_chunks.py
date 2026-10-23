from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT 
            f.id as file_id,
            f.filename,
            f.status as file_status,
            fc.*
        FROM files f
        LEFT JOIN file_chunks fc ON fc.file_id = f.id
        WHERE f.status != 'DELETED'
        ORDER BY f.id DESC;
    """)
    rows = [dict(r) for r in c.fetchall()]

for r in rows:
    print(r)
