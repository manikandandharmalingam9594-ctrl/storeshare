from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT id, email, role, is_active, created_at FROM users ORDER BY id;")
    users = [dict(r) for r in c.fetchall()]

print(f"Total users: {len(users)}")
print("\n--- ALL USERS ---")
for u in users:
    print(f"ID: {u['id']:3d} | Role: {u['role']:10s} | Active: {u['is_active']} | Email: {u['email']}")

print("\n--- ADMIN ACCOUNTS ---")
admins = [u for u in users if u['role'] == 'ADMIN']
for a in admins:
    print(f"ID: {a['id']:3d} | Email: {a['email']} | Active: {a['is_active']}")
