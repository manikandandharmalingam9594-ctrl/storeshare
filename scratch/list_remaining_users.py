from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT id, email, role, is_active FROM users ORDER BY id;")
    users = [dict(r) for r in c.fetchall()]

print(f"Total remaining users: {len(users)}\n")
for u in users:
    print(f"ID {u['id']:3d} | {u['role']:8s} | Active={u['is_active']} | {u['email']}")
