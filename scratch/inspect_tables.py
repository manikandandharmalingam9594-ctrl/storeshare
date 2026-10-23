from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name;
    """)
    tables = [r['table_name'] for r in c.fetchall()]
    print("Tables in public schema:", tables)

    for t in tables:
        c.execute(f"""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = '{t}'
            ORDER BY ordinal_position;
        """)
        cols = [f"{r['column_name']} ({r['data_type']})" for r in c.fetchall()]
        print(f"\nTable '{t}': {', '.join(cols)}")
