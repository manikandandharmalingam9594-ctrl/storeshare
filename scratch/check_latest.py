from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT id, email, role, created_at FROM users ORDER BY id DESC LIMIT 3;")
    print(c.fetchall())
