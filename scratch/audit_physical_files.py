import hashlib
from pathlib import Path
from backend.database import get_db_connection

with get_db_connection() as conn:
    c = conn.cursor()
    c.execute('''
        SELECT 
            f.id AS file_id,
            f.filename,
            f.owner_id,
            uo.email AS owner_email,
            f.file_size,
            f.encrypted_size,
            f.sha256_hash AS expected_sha256,
            f.status AS file_status,
            f.created_at AS file_created_at,
            fc.chunk_id,
            fc.chunk_size,
            fc.status AS chunk_status,
            fc.provider_node_id,
            pn.provider_id,
            up.email AS provider_email,
            pn.storage_dir,
            pn.computer_name,
            pn.status AS node_status,
            pn.last_heartbeat
        FROM files f
        JOIN users uo ON uo.id = f.owner_id
        LEFT JOIN file_chunks fc ON fc.file_id = f.id
        LEFT JOIN provider_nodes pn ON pn.id = fc.provider_node_id
        LEFT JOIN users up ON up.id = pn.provider_id
        WHERE f.status != 'DELETED'
        ORDER BY f.id DESC;
    ''')
    rows = c.fetchall()

print(f"=== TRACING {len(rows)} ACTIVE FILES/CHUNKS ===")
for r in rows:
    row = dict(r)
    file_id = row['file_id']
    filename = row['filename']
    chunk_id = row['chunk_id']
    node_id = row['provider_node_id']
    prov_email = row['provider_email']
    storage_dir = row['storage_dir']
    expected_sha = row['expected_sha256']
    expected_size = row['chunk_size']
    file_status = row['file_status']

    if not chunk_id or not storage_dir:
        print(f"File #{file_id} ({filename}): No chunk or storage dir assigned. Status: {file_status}")
        continue

    expected_file_path = Path(storage_dir) / "chunks" / chunk_id
    file_exists = expected_file_path.is_file()

    actual_size = None
    actual_sha = None
    sha_match = False

    if file_exists:
        data = expected_file_path.read_bytes()
        actual_size = len(data)
        actual_sha = hashlib.sha256(data).hexdigest()
        sha_match = (actual_sha.lower() == expected_sha.lower())

    print(f"""
File ID: {file_id}
Filename: {filename}
Owner: {row['owner_email']} (ID: {row['owner_id']})
Chunk ID: {chunk_id}
Assigned Node ID: {node_id} (Provider: {prov_email}, Host: {row['computer_name']}, Node Status: {row['node_status']})
Configured Directory: {storage_dir}
Physical Path: {expected_file_path}
Physical File Exists: {file_exists}
Size: Actual={actual_size} bytes | Expected={expected_size} bytes
SHA-256 Match: {sha_match}
Actual SHA:   {actual_sha}
Expected SHA: {expected_sha}
Conclusion: {'VERIFIED ON DISK' if (file_exists and sha_match) else ('CHECKSUM MISMATCH / CORRUPTED' if file_exists else 'NOT FOUND ON DISK')}
------------------------------------------------------------""")
