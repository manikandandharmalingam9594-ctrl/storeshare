from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT id, filename, status, created_at FROM files ORDER BY id DESC LIMIT 15;
    """)
    rows = [dict(r) for r in c.fetchall()]

for r in rows:
    print(r)
