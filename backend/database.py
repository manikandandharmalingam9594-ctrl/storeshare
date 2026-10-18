from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re
import sqlite3
from typing import List, Optional, Dict, Any

MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 Megabytes
CHUNK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

# Resolve paths relative to the project root, not the current working directory
BACKEND_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = BACKEND_DIR.parent
DATA_DIR: Path = PROJECT_ROOT / "data"


def get_db_path() -> Path:
    """
    Get the configured database path.
    Can be overridden via STORESHARE_DB_PATH (e.g., during tests).
    Defaults to data/storeshare.db relative to the project root.
    """
    env_path = os.environ.get("STORESHARE_DB_PATH")
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p
    return DATA_DIR / "storeshare.db"


# Default module-level DB_PATH for backwards-compatibility
DB_PATH: Path = get_db_path()


def normalize_email(email: str) -> str:
    """
    Normalize email address to lowercase and trimmed string.
    """
    return email.strip().lower()


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open and return a connection to the SQLite database.
    Ensures the target directory exists before connecting.
    Enforces foreign key constraints.
    """
    target_path = db_path or get_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """
    Initialize SQLite database and create all required application tables.
    Seeds default project metadata if not already present.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        # 1. Foundation metadata table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO app_metadata (key, value)
            VALUES 
                ('project_name', 'Store-Share'),
                ('phase', 'Day 1 Foundation'),
                ('database_driver', 'SQLite')
            ;
            """
        )

        # 2. Users table for authentication
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('CONSUMER', 'PROVIDER', 'ADMIN')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """
        )

        # 3. Provider nodes table for storage allocation tracking
        cursor.execute("PRAGMA table_info(provider_nodes);")
        existing_cols = [row["name"] for row in cursor.fetchall()]
        if existing_cols and "storage_dir" not in existing_cols:
            cursor.execute("DROP TABLE provider_nodes;")
            existing_cols = []

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL UNIQUE,
                storage_dir TEXT,
                quota_bytes INTEGER NOT NULL DEFAULT 0,
                used_bytes INTEGER NOT NULL DEFAULT 0,
                reserved_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'UNREGISTERED' CHECK (status IN ('UNREGISTERED', 'ONLINE', 'OFFLINE', 'STOPPED')),
                last_heartbeat TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_provider_nodes_provider_id ON provider_nodes(provider_id);
            """
        )

        # Ensure reserved_bytes column exists if table was created in an earlier migration
        if existing_cols and "reserved_bytes" not in existing_cols:
            cursor.execute("ALTER TABLE provider_nodes ADD COLUMN reserved_bytes INTEGER NOT NULL DEFAULT 0;")

        # 4. Files metadata table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                encrypted_size INTEGER NOT NULL,
                iv_hex TEXT NOT NULL,
                sha256_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING_UPLOAD' CHECK (status IN ('PENDING_UPLOAD', 'UPLOADING', 'STORED', 'FAILED', 'DELETED')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_files_owner_id ON files(owner_id);
            """
        )

        # 5. File chunks mapping
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS file_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                chunk_id TEXT NOT NULL UNIQUE,
                provider_node_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                chunk_size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'STORED', 'FAILED')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                FOREIGN KEY (provider_node_id) REFERENCES provider_nodes(id) ON DELETE CASCADE
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_file_chunks_file_id ON file_chunks(file_id);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_file_chunks_node_id ON file_chunks(provider_node_id);
            """
        )

        # 6. Asynchronous Provider Chunk Job Queue (NAT-friendly polling)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_node_id INTEGER NOT NULL,
                chunk_id TEXT NOT NULL,
                file_id INTEGER NOT NULL,
                job_type TEXT NOT NULL CHECK (job_type IN ('STORE_CHUNK', 'RETRIEVE_CHUNK', 'DELETE_CHUNK')),
                status TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'TIMED_OUT')),
                expires_at TIMESTAMP NOT NULL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_node_id) REFERENCES provider_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chunk_jobs_node_status ON chunk_jobs(provider_node_id, status);
            """
        )
        conn.commit()


def get_table_names(db_path: Optional[Path] = None) -> List[str]:
    """
    Return a read-only list of table names in the SQLite database.
    Excludes internal SQLite schema tables.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT name 
            FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name ASC;
            """
        )
        rows = cursor.fetchall()
        return [row["name"] for row in rows]


def get_user_by_email(email: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch a user by normalized email address using parameterized SQL.
    Returns None if user does not exist.
    """
    normalized = normalize_email(email)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, email, password_hash, role, is_active, created_at
            FROM users
            WHERE email = ?;
            """,
            (normalized,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def get_user_by_id(user_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch a user by numeric ID using parameterized SQL.
    Returns None if user does not exist.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, email, password_hash, role, is_active, created_at
            FROM users
            WHERE id = ?;
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def create_user(
    email: str,
    password_hash: str,
    role: str = "CONSUMER",
    is_active: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Insert a new user record using parameterized SQL.
    Validates role constraint.
    """
    normalized = normalize_email(email)
    if role not in ("CONSUMER", "PROVIDER", "ADMIN"):
        raise ValueError(f"Invalid role: {role}. Must be one of CONSUMER, PROVIDER, ADMIN.")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (email, password_hash, role, is_active)
            VALUES (?, ?, ?, ?);
            """,
            (normalized, password_hash, role, 1 if is_active else 0),
        )
        user_id = cursor.lastrowid
        conn.commit()

    created = get_user_by_id(user_id, db_path=db_path)
    if not created:
        raise RuntimeError("Failed to retrieve created user record.")
    return created


def get_provider_node(provider_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieve provider node record by provider user ID.
    Returns None if node has not been initialized.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, status, last_heartbeat, created_at, updated_at
            FROM provider_nodes
            WHERE provider_id = ?;
            """,
            (provider_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def upsert_provider_node(
    provider_id: int,
    quota_bytes: int,
    storage_dir: Optional[str] = None,
    status: str = "UNREGISTERED",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Create or update provider node storage settings.
    Uses parameterized SQL to prevent injection.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO provider_nodes (provider_id, storage_dir, quota_bytes, status, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(provider_id) DO UPDATE SET
                storage_dir = COALESCE(excluded.storage_dir, provider_nodes.storage_dir),
                quota_bytes = excluded.quota_bytes,
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (provider_id, storage_dir, quota_bytes, status),
        )
        conn.commit()

    node = get_provider_node(provider_id, db_path=db_path)
    if not node:
        raise RuntimeError("Failed to retrieve provider node record after upsert.")
    return node


def record_provider_heartbeat(
    provider_id: int,
    used_bytes: int,
    allocated_bytes: int,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Record agent heartbeat: updates last_heartbeat to now and marks status ONLINE.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE provider_nodes
            SET last_heartbeat = CURRENT_TIMESTAMP,
                status = 'ONLINE',
                used_bytes = ?,
                quota_bytes = CASE WHEN quota_bytes = 0 THEN ? ELSE quota_bytes END,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider_id = ?;
            """,
            (used_bytes, allocated_bytes, provider_id),
        )
        conn.commit()

    return get_provider_node(provider_id, db_path=db_path)


def set_provider_node_status(
    provider_id: int,
    status: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update status of provider node (e.g. STOPPED, OFFLINE, ONLINE).
    """
    if status not in ("UNREGISTERED", "ONLINE", "OFFLINE", "STOPPED"):
        raise ValueError(f"Invalid node status: {status}")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE provider_nodes
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider_id = ?;
            """,
            (status, provider_id),
        )
        conn.commit()

    return get_provider_node(provider_id, db_path=db_path)


def get_provider_node_by_id(node_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieve provider node record by primary key ID.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, status, last_heartbeat, created_at, updated_at
            FROM provider_nodes
            WHERE id = ?;
            """,
            (node_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def compute_node_effective_status(
    node: Dict[str, Any],
    heartbeat_timeout_seconds: int = 60,
) -> str:
    """
    Compute real-time node operational status based on heartbeat freshness.
    If coordinator hasn't received a heartbeat within timeout, marks as OFFLINE.
    """
    raw_status = node.get("status", "UNREGISTERED")
    if raw_status in ("STOPPED", "UNREGISTERED"):
        return raw_status

    last_hb = node.get("last_heartbeat")
    if not last_hb:
        return "OFFLINE"

    try:
        if isinstance(last_hb, str):
            hb_time = datetime.fromisoformat(last_hb)
        else:
            hb_time = last_hb

        if hb_time.tzinfo is None:
            hb_time = hb_time.replace(tzinfo=timezone.utc)

        age_seconds = (datetime.now(timezone.utc) - hb_time).total_seconds()
        if age_seconds > heartbeat_timeout_seconds:
            return "OFFLINE"
        return "ONLINE"
    except Exception:
        return "OFFLINE"


def find_eligible_provider_node(
    required_bytes: int,
    db_path: Optional[Path] = None,
    heartbeat_timeout_seconds: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    Find an ONLINE provider node that has sufficient available quota (quota - used - reserved).
    Heartbeat must be fresh (<= heartbeat_timeout_seconds ago).
    Returns the node with the highest available quota.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, status, last_heartbeat, created_at, updated_at
            FROM provider_nodes
            WHERE status != 'STOPPED'
              AND storage_dir IS NOT NULL
              AND last_heartbeat IS NOT NULL
              AND (quota_bytes - used_bytes - reserved_bytes) >= ?
            ORDER BY (quota_bytes - used_bytes - reserved_bytes) DESC;
            """,
            (required_bytes,),
        )
        rows = cursor.fetchall()

    for r in rows:
        node = dict(r)
        if compute_node_effective_status(node, heartbeat_timeout_seconds=heartbeat_timeout_seconds) == "ONLINE":
            return node

    return None


def reserve_provider_quota(
    provider_node_id: int,
    num_bytes: int,
    db_path: Optional[Path] = None,
) -> bool:
    """
    Transactionally reserve storage quota for an upcoming chunk upload.
    Prevents race conditions from exceeding provider quota.
    Returns True if reserved successfully, False if quota exceeded.
    """
    if num_bytes <= 0:
        raise ValueError("Reservation bytes must be greater than 0.")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE provider_nodes
            SET reserved_bytes = reserved_bytes + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (quota_bytes - used_bytes - reserved_bytes) >= ?;
            """,
            (num_bytes, provider_node_id, num_bytes),
        )
        conn.commit()
        return cursor.rowcount > 0


def commit_provider_quota(
    provider_node_id: int,
    num_bytes: int,
    db_path: Optional[Path] = None,
) -> bool:
    """
    Commit reserved quota to used_bytes when chunk storage is physically confirmed by provider.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE provider_nodes
            SET used_bytes = used_bytes + ?,
                reserved_bytes = MAX(0, reserved_bytes - ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (num_bytes, num_bytes, provider_node_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def release_provider_quota(
    provider_node_id: int,
    num_bytes: int,
    db_path: Optional[Path] = None,
) -> bool:
    """
    Release reserved quota if a transfer fails, times out, or cancels.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE provider_nodes
            SET reserved_bytes = MAX(0, reserved_bytes - ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (num_bytes, provider_node_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# ==============================================================================
# Consumer File Metadata & Placement Helpers
# ==============================================================================

def create_file_record(
    owner_id: int,
    filename: str,
    file_size: int,
    encrypted_size: int,
    iv_hex: str,
    sha256_hash: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Create a new consumer file record with status 'PENDING_UPLOAD'.
    Enforces <= 5 MB size limit and cryptographic parameter validations.
    Never accepts or stores decryption keys.
    """
    clean_filename = Path(filename).name.strip()
    if not clean_filename:
        raise ValueError("Filename cannot be empty.")

    if file_size <= 0:
        raise ValueError("File size must be greater than 0 bytes.")

    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
        )

    if encrypted_size <= 0:
        raise ValueError("Encrypted size must be greater than 0 bytes.")

    clean_iv = iv_hex.strip().lower()
    if len(clean_iv) != 24 or not all(c in "0123456789abcdef" for c in clean_iv):
        raise ValueError("Invalid IV: must be a 12-byte (24-character) hex string.")

    clean_hash = sha256_hash.strip().lower()
    if len(clean_hash) != 64 or not all(c in "0123456789abcdef" for c in clean_hash):
        raise ValueError("Invalid SHA-256 hash: must be a 32-byte (64-character) hex string.")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO files (owner_id, filename, file_size, encrypted_size, iv_hex, sha256_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING_UPLOAD');
            """,
            (owner_id, clean_filename, file_size, encrypted_size, clean_iv, clean_hash),
        )
        file_id = cursor.lastrowid
        conn.commit()

    created = get_file_by_id(file_id, db_path=db_path)
    if not created:
        raise RuntimeError("Failed to retrieve created file record.")
    return created


def get_file_by_id(
    file_id: int,
    owner_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve file record by ID. If owner_id is specified, strictly enforces ownership.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if owner_id is not None:
            cursor.execute(
                """
                SELECT id, owner_id, filename, file_size, encrypted_size, iv_hex, sha256_hash, status, created_at, updated_at
                FROM files
                WHERE id = ? AND owner_id = ?;
                """,
                (file_id, owner_id),
            )
        else:
            cursor.execute(
                """
                SELECT id, owner_id, filename, file_size, encrypted_size, iv_hex, sha256_hash, status, created_at, updated_at
                FROM files
                WHERE id = ?;
                """,
                (file_id,),
            )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def list_consumer_files(
    owner_id: int,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    List non-deleted files owned by the specified consumer.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, owner_id, filename, file_size, encrypted_size, iv_hex, sha256_hash, status, created_at, updated_at
            FROM files
            WHERE owner_id = ? AND status != 'DELETED'
            ORDER BY created_at DESC;
            """,
            (owner_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def update_file_status(
    file_id: int,
    status: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update file processing status.
    """
    if status not in ("PENDING_UPLOAD", "UPLOADING", "STORED", "FAILED", "DELETED"):
        raise ValueError(f"Invalid file status: {status}")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE files
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (status, file_id),
        )
        conn.commit()

    return get_file_by_id(file_id, db_path=db_path)


def create_file_chunk(
    file_id: int,
    chunk_id: str,
    provider_node_id: int,
    chunk_size: int,
    chunk_index: int = 0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Create a chunk placement record mapping a file chunk to a provider node.
    """
    clean_chunk_id = chunk_id.strip()
    if not CHUNK_ID_PATTERN.match(clean_chunk_id):
        raise ValueError(f"Invalid chunk ID format: {clean_chunk_id}")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO file_chunks (file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status)
            VALUES (?, ?, ?, ?, ?, 'PENDING');
            """,
            (file_id, clean_chunk_id, provider_node_id, chunk_index, chunk_size),
        )
        conn.commit()

    chunk = get_chunk_by_id(clean_chunk_id, db_path=db_path)
    if not chunk:
        raise RuntimeError("Failed to retrieve created chunk record.")
    return chunk


def get_file_chunks(
    file_id: int,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Get all chunk placement records for a file ordered by chunk_index.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status, created_at
            FROM file_chunks
            WHERE file_id = ?
            ORDER BY chunk_index ASC;
            """,
            (file_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_chunk_by_id(
    chunk_id: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a chunk placement record by chunk ID string.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status, created_at
            FROM file_chunks
            WHERE chunk_id = ?;
            """,
            (chunk_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def update_chunk_status(
    chunk_id: str,
    status: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update chunk placement status.
    """
    if status not in ("PENDING", "STORED", "FAILED"):
        raise ValueError(f"Invalid chunk status: {status}")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE file_chunks
            SET status = ?
            WHERE chunk_id = ?;
            """,
            (status, chunk_id),
        )
        conn.commit()

    return get_chunk_by_id(chunk_id, db_path=db_path)


# ==============================================================================
# Provider Chunk Job Lifecycle Helpers (NAT-friendly polling)
# ==============================================================================

def create_chunk_job(
    provider_node_id: int,
    chunk_id: str,
    file_id: int,
    job_type: str,
    timeout_seconds: int = 60,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Queue an asynchronous chunk transfer job for a provider node.
    """
    if job_type not in ("STORE_CHUNK", "RETRIEVE_CHUNK", "DELETE_CHUNK"):
        raise ValueError(f"Invalid job type: {job_type}")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chunk_jobs (provider_node_id, chunk_id, file_id, job_type, status, expires_at)
            VALUES (?, ?, ?, ?, 'QUEUED', ?);
            """,
            (provider_node_id, chunk_id, file_id, job_type, expires_str),
        )
        job_id = cursor.lastrowid
        conn.commit()

    job = get_chunk_job_by_id(job_id, db_path=db_path)
    if not job:
        raise RuntimeError("Failed to retrieve created chunk job.")
    return job


def get_chunk_job_by_id(
    job_id: int,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a chunk job by ID.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at
            FROM chunk_jobs
            WHERE id = ?;
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def get_active_chunk_job(
    chunk_id: str,
    job_type: str,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve an active (QUEUED or PROCESSING) job for a specific chunk and job type.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at
            FROM chunk_jobs
            WHERE chunk_id = ?
              AND job_type = ?
              AND status IN ('QUEUED', 'PROCESSING')
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY id DESC
            LIMIT 1;
            """,
            (chunk_id, job_type),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def poll_provider_jobs(
    provider_node_id: int,
    limit: int = 5,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Poll queued, non-expired jobs assigned exclusively to the specified provider node.
    Transitions polled jobs to 'PROCESSING' to avoid duplicate dispatch.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        cursor.execute(
            """
            SELECT id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at
            FROM chunk_jobs
            WHERE provider_node_id = ?
              AND status = 'QUEUED'
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY created_at ASC
            LIMIT ?;
            """,
            (provider_node_id, limit),
        )
        jobs = [dict(r) for r in cursor.fetchall()]

        if jobs:
            job_ids = [j["id"] for j in jobs]
            placeholders = ",".join("?" for _ in job_ids)
            cursor.execute(
                f"""
                UPDATE chunk_jobs
                SET status = 'PROCESSING',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders});
                """,
                job_ids,
            )
            for j in jobs:
                j["status"] = "PROCESSING"
        conn.commit()

    return jobs


def complete_chunk_job(
    job_id: int,
    provider_node_id: int,
    db_path: Optional[Path] = None,
) -> bool:
    """
    Acknowledge completion of a chunk transfer job.
    Idempotent: if already COMPLETED, returns True.
    If job belongs to another node or is timed out/failed, returns False.
    """
    job = get_chunk_job_by_id(job_id, db_path=db_path)
    if not job:
        return False

    if job["provider_node_id"] != provider_node_id:
        return False

    if job["status"] == "COMPLETED":
        return True

    if job["status"] in ("FAILED", "TIMED_OUT"):
        return False

    chunk = get_chunk_by_id(job["chunk_id"], db_path=db_path)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        cursor.execute(
            """
            UPDATE chunk_jobs
            SET status = 'COMPLETED',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('QUEUED', 'PROCESSING');
            """,
            (job_id,),
        )
        if cursor.rowcount == 0:
            cursor.execute("SELECT status FROM chunk_jobs WHERE id = ?;", (job_id,))
            current = cursor.fetchone()
            conn.commit()
            if current and current["status"] == "COMPLETED":
                return True
            return False

        if job["job_type"] == "STORE_CHUNK":
            cursor.execute(
                """
                UPDATE file_chunks
                SET status = 'STORED'
                WHERE chunk_id = ?;
                """,
                (job["chunk_id"],),
            )

            # Check if all chunks for this file are now stored
            cursor.execute(
                """
                SELECT count(*) as total, sum(CASE WHEN status = 'STORED' THEN 1 ELSE 0 END) as stored
                FROM file_chunks
                WHERE file_id = ?;
                """,
                (job["file_id"],),
            )
            row = cursor.fetchone()
            if row and row["total"] > 0 and row["total"] == row["stored"]:
                cursor.execute(
                    """
                    UPDATE files
                    SET status = 'STORED',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (job["file_id"],),
                )

        elif job["job_type"] == "DELETE_CHUNK":
            if chunk:
                cursor.execute(
                    """
                    UPDATE provider_nodes
                    SET used_bytes = MAX(0, used_bytes - ?),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (chunk["chunk_size"], provider_node_id),
                )
                cursor.execute(
                    """
                    DELETE FROM file_chunks WHERE chunk_id = ?;
                    """,
                    (job["chunk_id"],),
                )
                cursor.execute(
                    """
                    SELECT count(*) as total FROM file_chunks WHERE file_id = ?;
                    """,
                    (job["file_id"],),
                )
                row = cursor.fetchone()
                if row and row["total"] == 0:
                    cursor.execute(
                        """
                        UPDATE files
                        SET status = 'DELETED',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """,
                        (job["file_id"],),
                    )

        conn.commit()

    # Commit quota: convert reserved_bytes into used_bytes
    if job["job_type"] == "STORE_CHUNK" and chunk:
        commit_provider_quota(provider_node_id, chunk["chunk_size"], db_path=db_path)

    return True


def fail_chunk_job(
    job_id: int,
    provider_node_id: int,
    error_message: str = "Job execution failed",
    db_path: Optional[Path] = None,
) -> bool:
    """
    Mark a chunk transfer job as FAILED and release any reserved quota.
    """
    job = get_chunk_job_by_id(job_id, db_path=db_path)
    if not job:
        return False

    if job["provider_node_id"] != provider_node_id:
        return False

    if job["status"] == "COMPLETED":
        return False

    chunk = get_chunk_by_id(job["chunk_id"], db_path=db_path)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        cursor.execute(
            """
            UPDATE chunk_jobs
            SET status = 'FAILED',
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('QUEUED', 'PROCESSING');
            """,
            (error_message, job_id),
        )
        if cursor.rowcount == 0:
            conn.commit()
            return False

        if job["job_type"] == "STORE_CHUNK":
            cursor.execute(
                """
                UPDATE file_chunks
                SET status = 'FAILED'
                WHERE chunk_id = ?;
                """,
                (job["chunk_id"],),
            )
            cursor.execute(
                """
                UPDATE files
                SET status = 'FAILED',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (job["file_id"],),
            )

        conn.commit()

    # Release quota reservation
    if job["job_type"] == "STORE_CHUNK" and chunk:
        release_provider_quota(provider_node_id, chunk["chunk_size"], db_path=db_path)

    return True


def expire_stale_jobs(db_path: Optional[Path] = None) -> int:
    """
    Reap all expired jobs past their expires_at timestamp.
    Transitions stale QUEUED/PROCESSING jobs to TIMED_OUT and cleans up file status and reserved quota.
    Returns the count of expired jobs.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_node_id, chunk_id, file_id, job_type
            FROM chunk_jobs
            WHERE status IN ('QUEUED', 'PROCESSING')
              AND expires_at < CURRENT_TIMESTAMP;
            """
        )
        stale_jobs = [dict(r) for r in cursor.fetchall()]

        if not stale_jobs:
            return 0

        for j in stale_jobs:
            cursor.execute(
                """
                UPDATE chunk_jobs
                SET status = 'TIMED_OUT',
                    error_message = 'Job timed out before agent acknowledgement.',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (j["id"],),
            )

            if j["job_type"] == "STORE_CHUNK":
                cursor.execute(
                    """
                    UPDATE file_chunks
                    SET status = 'FAILED'
                    WHERE chunk_id = ?;
                    """,
                    (j["chunk_id"],),
                )
                cursor.execute(
                    """
                    UPDATE files
                    SET status = 'FAILED',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (j["file_id"],),
                )

        conn.commit()

    # Release reserved quota for all expired store jobs
    for j in stale_jobs:
        if j["job_type"] == "STORE_CHUNK":
            chunk = get_chunk_by_id(j["chunk_id"], db_path=db_path)
            if chunk:
                release_provider_quota(j["provider_node_id"], chunk["chunk_size"], db_path=db_path)

    return len(stale_jobs)
