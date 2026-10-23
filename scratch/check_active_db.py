from backend.database import get_db_connection, get_database_info, is_postgres
import json

info = get_database_info()
print("DATABASE INFO:", json.dumps(info, indent=2))

with get_db_connection() as conn:
    c = conn.cursor()
    if is_postgres():
        c.execute("SELECT current_database(), current_user, inet_server_addr(), inet_server_port();")
        print("Server info:", c.fetchall())
    
    c.execute("""
        SELECT *
        FROM users
        ORDER BY id;
    """)
    users = [dict(r) for r in c.fetchall()]

print(f"\nTotal users in active database: {len(users)}")
for u in users:
    # Do not print password_hash
    clean_u = {k: v for k, v in u.items() if k != 'password_hash'}
    print(clean_u)
