from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE email LIKE %s;", ('fe_test_%',))
    print(f"Deleted {c.rowcount} test users created during test execution.")
    conn.commit()
