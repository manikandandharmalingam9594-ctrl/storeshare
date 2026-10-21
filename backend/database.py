from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Union

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg
    from psycopg.rows import dict_row
    PSYCOPG_AVAILABLE = True
except ImportError:
    psycopg = None
    dict_row = None
    PSYCOPG_AVAILABLE = False

logger = logging.getLogger("store-share-database")

MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 Megabytes
CHUNK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

# Resolve paths relative to the project root, not the current working directory
BACKEND_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = BACKEND_DIR.parent
DATA_DIR: Path = PROJECT_ROOT / "data"


def get_database_url() -> Optional[str]:
    """
    Get configured PostgreSQL connection URL if set (via STORESHARE_DATABASE_URL).
    """
    url = os.environ.get("STORESHARE_DATABASE_URL")
    if url and url.strip():
        return url.strip()
    return None


def is_postgres(target: Optional[Union[Path, str]] = None) -> bool:
    """
    Check if the target or default database is PostgreSQL.
    Returns True if target string starts with postgres:// or postgresql://,
    or if target is None and STORESHARE_DATABASE_URL is configured.
    """
    if target is not None:
        target_str = str(target).strip().lower()
        return target_str.startswith("postgres://") or target_str.startswith("postgresql://")
    return get_database_url() is not None


def get_db_path() -> Path:
    """
    Get the configured SQLite database path.
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


def get_database_info(target: Optional[Union[Path, str]] = None) -> Dict[str, Any]:
    """
    Return human-readable metadata describing the active database engine and target,
    with sensitive credentials securely masked.
    """
    if is_postgres(target):
        raw_url = str(target) if target else (get_database_url() or "")
        masked = re.sub(r":([^:@]+)@", ":****@", raw_url)
        return {
            "engine": "PostgreSQL",
            "driver": "psycopg",
            "target": masked,
        }
    else:
        path = Path(target) if (target and not is_postgres(target)) else get_db_path()
        return {
            "engine": "SQLite",
            "driver": "sqlite3",
            "target": str(path),
        }


def normalize_email(email: str) -> str:
    """
    Normalize email address to lowercase and trimmed string.
    """
    return email.strip().lower()


class DatabaseCursor:
    """
    Unified database cursor wrapper providing cross-dialect query adaptation,
    parameter translation (? -> %s), scalar function adaptation (MAX -> GREATEST),
    and consistent generated ID capture across PostgreSQL and SQLite.
    """

    def __init__(self, raw_cursor, is_pg: bool):
        self._cursor = raw_cursor
        self.is_pg = is_pg
        self.lastrowid = getattr(raw_cursor, "lastrowid", None)

    def execute(self, sql: str, params: Optional[Union[tuple, list, dict]] = None):
        adapted_sql = sql
        if self.is_pg:
            # Bypass SQLite file-level lock commands on PostgreSQL
            if "BEGIN IMMEDIATE" in adapted_sql:
                return self

            # Adapt parameter placeholders from SQLite '?' to PostgreSQL '%s'
            if "?" in adapted_sql:
                adapted_sql = adapted_sql.replace("?", "%s")

            # Adapt scalar clamping from SQLite MAX(0, ...) to PostgreSQL GREATEST(0, ...)
            if "MAX(0," in adapted_sql or "MAX(0 ," in adapted_sql:
                adapted_sql = re.sub(r"MAX\s*\(\s*0\s*,", "GREATEST(0,", adapted_sql, flags=re.IGNORECASE)

            # Auto-capture generated primary key via RETURNING id strictly for INSERT queries on tables with 'id'
            is_insert = adapted_sql.strip().upper().startswith("INSERT INTO")
            has_returning = "RETURNING" in adapted_sql.upper()
            has_id_pk = "APP_METADATA" not in adapted_sql.upper()
            auto_returning_for_insert = False

            if is_insert and not has_returning and has_id_pk:
                adapted_sql = adapted_sql.rstrip().rstrip(";") + " RETURNING id;"
                auto_returning_for_insert = True

            self._cursor.execute(adapted_sql, params or ())

            if is_insert and (auto_returning_for_insert or has_returning):
                try:
                    row = self._cursor.fetchone()
                    if row is not None:
                        if isinstance(row, dict):
                            self.lastrowid = row.get("id")
                        elif hasattr(row, "__getitem__"):
                            self.lastrowid = row[0]
                except Exception:
                    pass
        else:
            self._cursor.execute(adapted_sql, params or ())
            self.lastrowid = self._cursor.lastrowid

        return self

    def fetchone(self) -> Optional[Dict[str, Any]]:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self) -> List[Dict[str, Any]]:
        rows = self._cursor.fetchall()
        return [dict(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def close(self) -> None:
        if hasattr(self._cursor, "close"):
            self._cursor.close()


class DatabaseConnection:
    """
    Unified database connection context manager supporting both PostgreSQL
    (via psycopg with dict_row) and SQLite (via sqlite3 with Row).
    """

    def __init__(self, raw_conn, is_pg: bool):
        self.conn = raw_conn
        self.is_pg = is_pg

    def cursor(self) -> DatabaseCursor:
        raw_cur = self.conn.cursor()
        return DatabaseCursor(raw_cur, self.is_pg)

    def execute(self, sql: str, params: Optional[Union[tuple, list, dict]] = None) -> DatabaseCursor:
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self.rollback()
            except Exception:
                pass
        else:
            try:
                self.commit()
            except Exception:
                pass
        self.close()


@contextmanager
def get_db_connection(db_path: Optional[Union[Path, str]] = None):
    """
    Open and yield a unified database connection supporting both PostgreSQL and SQLite.
    Ensures safe commit on clean exit, rollback on error, and reliable connection closure.
    """
    if is_postgres(db_path):
        if not PSYCOPG_AVAILABLE:
            raise RuntimeError(
                "PostgreSQL driver 'psycopg' is not available. Please install 'psycopg[binary]'."
            )
        url = str(db_path) if db_path else get_database_url()
        raw_conn = psycopg.connect(url, row_factory=dict_row)
        wrapper = DatabaseConnection(raw_conn, is_pg=True)
        try:
            yield wrapper
        finally:
            wrapper.close()
    else:
        target_path = Path(db_path) if (db_path and not is_postgres(db_path)) else get_db_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        raw_conn = sqlite3.connect(str(target_path))
        raw_conn.execute("PRAGMA foreign_keys = ON;")
        raw_conn.row_factory = sqlite3.Row
        wrapper = DatabaseConnection(raw_conn, is_pg=False)
        try:
            yield wrapper
        finally:
            wrapper.close()


# ==============================================================================
# Database Initialization & DDL Schemas
# ==============================================================================

def _init_postgres_db(conn: DatabaseConnection) -> None:
    """
    Initialize PostgreSQL tables, constraints, foreign keys, and indexes.
    """
    cursor = conn.cursor()
    # 1. Foundation metadata table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key VARCHAR(128) PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        INSERT INTO app_metadata (key, value)
        VALUES 
            ('project_name', 'Store-Share'),
            ('phase', 'Day 1 Foundation'),
            ('database_driver', 'PostgreSQL')
        ON CONFLICT (key) DO NOTHING;
        """
    )

    # 2. Users table for authentication
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(32) NOT NULL CHECK (role IN ('CONSUMER', 'PROVIDER', 'ADMIN')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        """
    )

    # 3. Provider nodes table for storage allocation tracking
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_nodes (
            id BIGSERIAL PRIMARY KEY,
            provider_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            storage_dir TEXT,
            quota_bytes BIGINT NOT NULL DEFAULT 0,
            used_bytes BIGINT NOT NULL DEFAULT 0,
            reserved_bytes BIGINT NOT NULL DEFAULT 0,
            free_disk_bytes BIGINT NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL DEFAULT 'UNREGISTERED' CHECK (status IN ('UNREGISTERED', 'ONLINE', 'OFFLINE', 'STOPPED', 'PAUSED')),
            last_heartbeat TIMESTAMPTZ,
            computer_name VARCHAR(255),
            last_inventory TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_provider_nodes_provider_id ON provider_nodes(provider_id);
        """
    )

    # 4. Files metadata table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id BIGSERIAL PRIMARY KEY,
            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename VARCHAR(512) NOT NULL,
            file_size BIGINT NOT NULL,
            encrypted_size BIGINT NOT NULL,
            iv_hex VARCHAR(64) NOT NULL,
            sha256_hash VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING_UPLOAD' CHECK (status IN ('PENDING_UPLOAD', 'UPLOADING', 'STORED', 'FAILED', 'DELETED')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
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
            id BIGSERIAL PRIMARY KEY,
            file_id BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            chunk_id VARCHAR(128) NOT NULL UNIQUE,
            provider_node_id BIGINT NOT NULL REFERENCES provider_nodes(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            chunk_size BIGINT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'STORED', 'FAILED')),
            sha256_hash VARCHAR(64),
            last_verified_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
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
            id BIGSERIAL PRIMARY KEY,
            provider_node_id BIGINT NOT NULL REFERENCES provider_nodes(id) ON DELETE CASCADE,
            chunk_id VARCHAR(128) NOT NULL,
            file_id BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            job_type VARCHAR(32) NOT NULL CHECK (job_type IN ('STORE_CHUNK', 'RETRIEVE_CHUNK', 'DELETE_CHUNK')),
            status VARCHAR(32) NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'TIMED_OUT')),
            expires_at TIMESTAMPTZ NOT NULL,
            error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunk_jobs_node_status ON chunk_jobs(provider_node_id, status);
        """
    )

    # Safe schema migration for existing tables
    cursor.execute("ALTER TABLE provider_nodes ADD COLUMN IF NOT EXISTS computer_name VARCHAR(255);")
    cursor.execute("ALTER TABLE provider_nodes ADD COLUMN IF NOT EXISTS free_disk_bytes BIGINT NOT NULL DEFAULT 0;")
    cursor.execute("ALTER TABLE provider_nodes ADD COLUMN IF NOT EXISTS last_inventory TEXT;")
    cursor.execute("ALTER TABLE file_chunks ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;")
    cursor.execute("ALTER TABLE file_chunks ADD COLUMN IF NOT EXISTS sha256_hash VARCHAR(64);")
    try:
        cursor.execute("ALTER TABLE provider_nodes DROP CONSTRAINT IF EXISTS provider_nodes_status_check;")
        cursor.execute("ALTER TABLE provider_nodes ADD CONSTRAINT provider_nodes_status_check CHECK (status IN ('UNREGISTERED', 'ONLINE', 'OFFLINE', 'STOPPED', 'PAUSED'));")
    except Exception as exc:
        logger.warning(f"Could not update provider_nodes status check constraint: {exc}")

    conn.commit()


def _init_sqlite_db(conn: DatabaseConnection) -> None:
    """
    Initialize SQLite tables, constraints, foreign keys, and indexes.
    """
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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL UNIQUE,
            storage_dir TEXT,
            quota_bytes INTEGER NOT NULL DEFAULT 0,
            used_bytes INTEGER NOT NULL DEFAULT 0,
            reserved_bytes INTEGER NOT NULL DEFAULT 0,
            free_disk_bytes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'UNREGISTERED' CHECK (status IN ('UNREGISTERED', 'ONLINE', 'OFFLINE', 'STOPPED', 'PAUSED')),
            last_heartbeat TIMESTAMP,
            computer_name TEXT,
            last_inventory TEXT,
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
            sha256_hash TEXT,
            last_verified_at TIMESTAMP,
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

    # Safe schema migration for existing SQLite tables
    cursor.execute("PRAGMA table_info(provider_nodes);")
    existing_pn_cols = [c["name"] for c in cursor.fetchall()]
    if "computer_name" not in existing_pn_cols:
        cursor.execute("ALTER TABLE provider_nodes ADD COLUMN computer_name TEXT;")
    if "free_disk_bytes" not in existing_pn_cols:
        cursor.execute("ALTER TABLE provider_nodes ADD COLUMN free_disk_bytes INTEGER NOT NULL DEFAULT 0;")
    if "last_inventory" not in existing_pn_cols:
        cursor.execute("ALTER TABLE provider_nodes ADD COLUMN last_inventory TEXT;")

    cursor.execute("PRAGMA table_info(file_chunks);")
    existing_fc_cols = [c["name"] for c in cursor.fetchall()]
    if "last_verified_at" not in existing_fc_cols:
        cursor.execute("ALTER TABLE file_chunks ADD COLUMN last_verified_at TIMESTAMP;")
    if "sha256_hash" not in existing_fc_cols:
        cursor.execute("ALTER TABLE file_chunks ADD COLUMN sha256_hash TEXT;")

    conn.commit()


def init_db(db_path: Optional[Union[Path, str]] = None) -> None:
    """
    Initialize database and create all required application tables and indexes.
    Automatically executes the appropriate dialect DDL for PostgreSQL or SQLite.
    """
    with get_db_connection(db_path) as conn:
        if conn.is_pg:
            _init_postgres_db(conn)
        else:
            _init_sqlite_db(conn)


def get_table_names(db_path: Optional[Union[Path, str]] = None) -> List[str]:
    """
    Return a read-only list of table names in the database.
    Compatible with both PostgreSQL and SQLite.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if conn.is_pg:
            cursor.execute(
                """
                SELECT table_name AS name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                ORDER BY table_name ASC;
                """
            )
        else:
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


# ==============================================================================
# User Accounts & Authentication Helpers
# ==============================================================================

def get_user_by_email(email: str, db_path: Optional[Union[Path, str]] = None) -> Optional[Dict[str, Any]]:
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


def get_user_by_id(user_id: int, db_path: Optional[Union[Path, str]] = None) -> Optional[Dict[str, Any]]:
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
    db_path: Optional[Union[Path, str]] = None,
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
            (normalized, password_hash, role, bool(is_active)),
        )
        user_id = cursor.lastrowid
        conn.commit()

    created = get_user_by_id(user_id, db_path=db_path)
    if not created:
        raise RuntimeError("Failed to retrieve created user record.")
    return created


# ==============================================================================
# Provider Node Telemetry & Allocation Helpers
# ==============================================================================

def get_provider_node(provider_id: int, db_path: Optional[Union[Path, str]] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieve provider node record by provider user ID.
    Returns None if node has not been initialized.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, free_disk_bytes, status, last_heartbeat, computer_name, last_inventory, created_at, updated_at
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
    computer_name: Optional[str] = None,
    free_disk_bytes: int = 0,
    last_inventory: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Create or update provider node storage settings.
    Uses parameterized SQL and cross-engine ON CONFLICT upsert.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO provider_nodes (provider_id, storage_dir, quota_bytes, status, computer_name, free_disk_bytes, last_inventory, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (provider_id) DO UPDATE SET
                storage_dir = COALESCE(EXCLUDED.storage_dir, provider_nodes.storage_dir),
                quota_bytes = EXCLUDED.quota_bytes,
                status = EXCLUDED.status,
                computer_name = COALESCE(EXCLUDED.computer_name, provider_nodes.computer_name),
                free_disk_bytes = CASE WHEN EXCLUDED.free_disk_bytes > 0 THEN EXCLUDED.free_disk_bytes ELSE provider_nodes.free_disk_bytes END,
                last_inventory = COALESCE(EXCLUDED.last_inventory, provider_nodes.last_inventory),
                updated_at = CURRENT_TIMESTAMP;
            """,
            (provider_id, storage_dir, quota_bytes, status, computer_name, free_disk_bytes, last_inventory),
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
    computer_name: Optional[str] = None,
    free_disk_bytes: int = 0,
    inventory_json: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Record agent heartbeat: updates last_heartbeat, telemetry, and ensures ONLINE status unless PAUSED.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if computer_name:
            cursor.execute(
                """
                UPDATE provider_nodes
                SET last_heartbeat = CURRENT_TIMESTAMP,
                    status = CASE WHEN status = 'PAUSED' THEN 'PAUSED' ELSE 'ONLINE' END,
                    used_bytes = ?,
                    quota_bytes = CASE WHEN quota_bytes = 0 THEN ? ELSE quota_bytes END,
                    computer_name = ?,
                    free_disk_bytes = ?,
                    last_inventory = COALESCE(?, last_inventory),
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider_id = ?;
                """,
                (used_bytes, allocated_bytes, computer_name, free_disk_bytes, inventory_json, provider_id),
            )
        else:
            cursor.execute(
                """
                UPDATE provider_nodes
                SET last_heartbeat = CURRENT_TIMESTAMP,
                    status = CASE WHEN status = 'PAUSED' THEN 'PAUSED' ELSE 'ONLINE' END,
                    used_bytes = ?,
                    quota_bytes = CASE WHEN quota_bytes = 0 THEN ? ELSE quota_bytes END,
                    free_disk_bytes = ?,
                    last_inventory = COALESCE(?, last_inventory),
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider_id = ?;
                """,
                (used_bytes, allocated_bytes, free_disk_bytes, inventory_json, provider_id),
            )
        conn.commit()

    return get_provider_node(provider_id, db_path=db_path)


def set_provider_node_status(
    provider_id: int,
    status: str,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update status of provider node (e.g. STOPPED, PAUSED, OFFLINE, ONLINE).
    """
    if status not in ("UNREGISTERED", "ONLINE", "OFFLINE", "STOPPED", "PAUSED"):
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


def get_provider_node_by_id(node_id: int, db_path: Optional[Union[Path, str]] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieve provider node record by primary key ID.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, free_disk_bytes, status, last_heartbeat, computer_name, last_inventory, created_at, updated_at
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
    Preserves STOPPED and PAUSED states cleanly.
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
        if raw_status == "PAUSED":
            return "PAUSED"
        return "ONLINE"
    except Exception:
        return "OFFLINE"


def find_eligible_provider_node(
    required_bytes: int,
    db_path: Optional[Union[Path, str]] = None,
    heartbeat_timeout_seconds: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    Find an ONLINE provider node that has sufficient available quota (quota - used - reserved).
    Heartbeat must be fresh (<= heartbeat_timeout_seconds ago).
    Excludes STOPPED and PAUSED nodes.
    Returns the node with the highest available quota.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, free_disk_bytes, status, last_heartbeat, created_at, updated_at
            FROM provider_nodes
            WHERE status NOT IN ('STOPPED', 'PAUSED')
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Get all chunk placement records for a file ordered by chunk_index.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status, last_verified_at, created_at
            FROM file_chunks
            WHERE file_id = ?
            ORDER BY chunk_index ASC;
            """,
            (file_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_chunk_by_id(
    chunk_id: str,
    db_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a chunk placement record by chunk ID string.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status, last_verified_at, created_at
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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Queue an asynchronous chunk transfer job for a provider node.
    """
    if job_type not in ("STORE_CHUNK", "RETRIEVE_CHUNK", "DELETE_CHUNK"):
        raise ValueError(f"Invalid job type: {job_type}")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    expires_val = expires_at if is_postgres(db_path) else expires_at.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chunk_jobs (provider_node_id, chunk_id, file_id, job_type, status, expires_at)
            VALUES (?, ?, ?, ?, 'QUEUED', ?);
            """,
            (provider_node_id, chunk_id, file_id, job_type, expires_val),
        )
        job_id = cursor.lastrowid
        conn.commit()

    job = get_chunk_job_by_id(job_id, db_path=db_path)
    if not job:
        raise RuntimeError("Failed to retrieve created chunk job.")
    return job


def get_chunk_job_by_id(
    job_id: int,
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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
    exclude_job_types: Optional[List[str]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Poll queued, non-expired jobs assigned exclusively to the specified provider node.
    Transitions polled jobs to 'PROCESSING' to avoid duplicate dispatch.

    In PostgreSQL: Uses atomic UPDATE with 'FOR UPDATE SKIP LOCKED' to guarantee
    safe, non-blocking concurrent polling across multiple agent workers.
    In SQLite: Uses 'BEGIN IMMEDIATE;' transaction lock.
    """
    exclude_clause = ""
    exclude_params: tuple = ()
    if exclude_job_types:
        placeholders_ex = ",".join("?" for _ in exclude_job_types)
        exclude_clause = f"AND job_type NOT IN ({placeholders_ex})"
        exclude_params = tuple(exclude_job_types)

    if is_postgres(db_path):
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            pg_exclude_clause = ""
            if exclude_job_types:
                pg_placeholders = ",".join("%s" for _ in exclude_job_types)
                pg_exclude_clause = f"AND job_type NOT IN ({pg_placeholders})"
            query = f"""
            UPDATE chunk_jobs
            SET status = 'PROCESSING',
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id
                FROM chunk_jobs
                WHERE provider_node_id = %s
                  AND status = 'QUEUED'
                  AND expires_at > CURRENT_TIMESTAMP
                  {pg_exclude_clause}
                ORDER BY created_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at;
            """
            cursor.execute(query, (provider_node_id, *exclude_params, limit))
            jobs = cursor.fetchall()
            conn.commit()
            return jobs
    else:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE;")
            cursor.execute(
                f"""
                SELECT id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at
                FROM chunk_jobs
                WHERE provider_node_id = ?
                  AND status = 'QUEUED'
                  AND expires_at > CURRENT_TIMESTAMP
                  {exclude_clause}
                ORDER BY created_at ASC
                LIMIT ?;
                """,
                (provider_node_id, *exclude_params, limit),
            )
            jobs = cursor.fetchall()

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
    db_path: Optional[Union[Path, str]] = None,
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
    db_path: Optional[Union[Path, str]] = None,
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

    if job["status"] == "FAILED":
        return True

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
            cursor.execute("SELECT status FROM chunk_jobs WHERE id = ?;", (job_id,))
            current = cursor.fetchone()
            conn.commit()
            if current and current["status"] == "FAILED":
                return True
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


def expire_stale_jobs(db_path: Optional[Union[Path, str]] = None) -> int:
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
        stale_jobs = cursor.fetchall()

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


def cancel_pending_chunk_jobs_for_file(
    file_id: int,
    db_path: Optional[Union[Path, str]] = None,
) -> int:
    """
    Cancel all queued or processing chunk jobs for a file.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE chunk_jobs
            SET status = 'FAILED',
                error_message = 'Job cancelled due to file deletion',
                updated_at = CURRENT_TIMESTAMP
            WHERE file_id = ? AND status IN ('QUEUED', 'PROCESSING');
            """,
            (file_id,),
        )
        count = cursor.rowcount
        conn.commit()
        return count


def delete_file_chunks_for_file(
    file_id: int,
    db_path: Optional[Union[Path, str]] = None,
) -> int:
    """
    Remove all chunk placement records for a file.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM file_chunks WHERE file_id = ?;
            """,
            (file_id,),
        )
        count = cursor.rowcount
        conn.commit()
        return count


def update_chunk_verification(
    provider_node_id: int,
    chunk_ids: List[str],
    db_path: Optional[Union[Path, str]] = None,
) -> int:
    """
    Update last_verified_at timestamp for chunks confirmed physically present on provider host.
    Uses parameterized query compatible with PostgreSQL and SQLite.
    """
    if not chunk_ids:
        return 0

    valid_chunk_ids = [cid for cid in chunk_ids if CHUNK_ID_PATTERN.match(cid)]
    if not valid_chunk_ids:
        return 0

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        placeholders = ",".join(["?"] * len(valid_chunk_ids))
        cursor.execute(
            f"""
            UPDATE file_chunks
            SET last_verified_at = CURRENT_TIMESTAMP
            WHERE provider_node_id = ? AND chunk_id IN ({placeholders});
            """,
            [provider_node_id] + list(valid_chunk_ids),
        )
        count = cursor.rowcount
        conn.commit()
        return count


def get_provider_job_telemetry(
    provider_node_id: int,
    limit: int = 20,
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Retrieve telemetry statistics and recent execution history for a provider node.
    Returns counts for active, completed, and failed jobs, along with recent job logs.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                COUNT(CASE WHEN status IN ('QUEUED', 'PROCESSING') THEN 1 END) AS active_jobs,
                COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) AS completed_jobs,
                COUNT(CASE WHEN status IN ('FAILED', 'TIMED_OUT') THEN 1 END) AS failed_jobs
            FROM chunk_jobs
            WHERE provider_node_id = ?;
            """,
            (provider_node_id,),
        )
        counts = cursor.fetchone() or {}

        cursor.execute(
            """
            SELECT id, provider_node_id, chunk_id, file_id, job_type, status, error_message, created_at, updated_at
            FROM chunk_jobs
            WHERE provider_node_id = ?
            ORDER BY id DESC
            LIMIT ?;
            """,
            (provider_node_id, limit),
        )
        recent_rows = cursor.fetchall()

    recent_jobs = [
        {
            "id": r["id"],
            "provider_node_id": r["provider_node_id"],
            "chunk_id": r["chunk_id"],
            "file_id": r["file_id"],
            "job_type": r["job_type"],
            "status": r["status"],
            "error_message": r.get("error_message"),
            "created_at": str(r["created_at"]) if r.get("created_at") else None,
            "updated_at": str(r["updated_at"]) if r.get("updated_at") else None,
        }
        for r in recent_rows
    ]

    return {
        "active_jobs": counts.get("active_jobs", 0) if isinstance(counts, dict) else (counts[0] if counts else 0),
        "completed_jobs": counts.get("completed_jobs", 0) if isinstance(counts, dict) else (counts[1] if counts else 0),
        "failed_jobs": counts.get("failed_jobs", 0) if isinstance(counts, dict) else (counts[2] if counts else 0),
        "recent_jobs": recent_jobs,
    }


def get_provider_reconciliation_records(
    provider_node_id: int,
    db_path: Optional[Union[Path, str]] = None,
    heartbeat_timeout_seconds: int = 60,
    verification_freshness_seconds: int = 90,
) -> Dict[str, Any]:
    """
    Reconciles physical chunk storage on provider host with coordinator metadata.
    Compares:
    1. Expected chunks registered to this provider node.
    2. Actual physical chunk files reported by provider agent (IDs, sizes, SHA-256 hashes).

    Categorizes every chunk into one of 5 distinct states:
    - VERIFIED: physically exists on provider disk, byte size and SHA-256 hash match metadata.
    - MISSING: expected by coordinator, but chunk file is absent on provider disk.
    - CORRUPTED: exists on disk, but byte size or cryptographic checksum does not match expected metadata.
    - UNEXPECTED: exists on provider disk, but coordinator has no record of it belonging to this node.
    - UNVERIFIED: assigned in coordinator, but provider node is offline or physical presence unverified.
    """
    node = get_provider_node_by_id(provider_node_id, db_path=db_path)
    if not node:
        raise ValueError(f"Provider node #{provider_node_id} not found.")

    effective_status = compute_node_effective_status(node, heartbeat_timeout_seconds=heartbeat_timeout_seconds)
    is_online = (effective_status in ("ONLINE", "PAUSED"))
    now_utc = datetime.now(timezone.utc)

    # 1. Fetch expected chunks for this provider node from database
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT fc.id, fc.file_id, fc.chunk_id, fc.provider_node_id, fc.chunk_index, fc.chunk_size,
                   fc.status AS chunk_status, fc.last_verified_at,
                   f.filename, f.file_size, f.encrypted_size, f.sha256_hash AS file_sha256, f.status AS file_status
            FROM file_chunks fc
            JOIN files f ON f.id = fc.file_id
            WHERE fc.provider_node_id = ?
              AND f.status != 'DELETED'
            ORDER BY fc.id ASC;
            """,
            (provider_node_id,),
        )
        expected_rows = cursor.fetchall()

    # 2. Parse latest physical inventory reported by provider agent
    physical_items: Dict[str, Dict[str, Any]] = {}
    raw_inventory = node.get("last_inventory")
    if raw_inventory:
        try:
            parsed = json.loads(raw_inventory)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "chunk_id" in item:
                        physical_items[item["chunk_id"]] = item
                    elif isinstance(item, str):
                        physical_items[item] = {"chunk_id": item}
        except Exception as exc:
            logger.warning(f"Could not parse last_inventory for node #{provider_node_id}: {exc}")

    expected_map = {row["chunk_id"]: dict(row) for row in expected_rows}
    verified_chunk_ids_to_update: List[str] = []
    items: List[Dict[str, Any]] = []

    # Process all expected chunks
    for cid, exp in expected_map.items():
        exp_size = exp["chunk_size"]
        exp_hash = exp.get("file_sha256")
        last_v_str = str(exp["last_verified_at"]) if exp.get("last_verified_at") else None

        if not is_online:
            # Node is offline
            v_dt = None
            if exp.get("last_verified_at"):
                try:
                    ts = exp["last_verified_at"]
                    if isinstance(ts, str):
                        v_dt = datetime.fromisoformat(ts.replace(" ", "T"))
                    elif isinstance(ts, datetime):
                        v_dt = ts
                    if v_dt and v_dt.tzinfo is None:
                        v_dt = v_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            if v_dt and (now_utc - v_dt).total_seconds() <= verification_freshness_seconds:
                state = "VERIFIED"
                detail = "Physical chunk verified present prior to provider heartbeat timeout."
            else:
                state = "UNVERIFIED"
                detail = "Provider node is offline; physical presence and integrity unverified."

            items.append({
                "chunk_id": cid,
                "file_id": exp["file_id"],
                "filename": exp["filename"],
                "status": state,
                "expected_size_bytes": exp_size,
                "actual_size_bytes": None,
                "expected_checksum": exp_hash,
                "actual_checksum": None,
                "last_verified_at": last_v_str,
                "status_detail": detail,
            })
            continue

        # Node is online
        if cid not in physical_items:
            items.append({
                "chunk_id": cid,
                "file_id": exp["file_id"],
                "filename": exp["filename"],
                "status": "MISSING",
                "expected_size_bytes": exp_size,
                "actual_size_bytes": None,
                "expected_checksum": exp_hash,
                "actual_checksum": None,
                "last_verified_at": last_v_str,
                "status_detail": "Expected by coordinator metadata, but chunk file is absent on provider disk.",
            })
        else:
            phys = physical_items[cid]
            act_size = phys.get("size_bytes")
            act_hash = phys.get("sha256_hash")

            is_corrupted = False
            reasons = []

            if act_size is not None and act_size != exp_size:
                is_corrupted = True
                reasons.append(f"Size mismatch: expected {exp_size} bytes, found {act_size} bytes on disk")

            if exp_hash and act_hash and (exp_hash.strip().lower() != act_hash.strip().lower()):
                is_corrupted = True
                reasons.append(f"Checksum mismatch: expected SHA-256 '{exp_hash[:16]}...', found '{act_hash[:16]}...'")

            if is_corrupted:
                items.append({
                    "chunk_id": cid,
                    "file_id": exp["file_id"],
                    "filename": exp["filename"],
                    "status": "CORRUPTED",
                    "expected_size_bytes": exp_size,
                    "actual_size_bytes": act_size,
                    "expected_checksum": exp_hash,
                    "actual_checksum": act_hash,
                    "last_verified_at": last_v_str,
                    "status_detail": "; ".join(reasons),
                })
            else:
                verified_chunk_ids_to_update.append(cid)
                items.append({
                    "chunk_id": cid,
                    "file_id": exp["file_id"],
                    "filename": exp["filename"],
                    "status": "VERIFIED",
                    "expected_size_bytes": exp_size,
                    "actual_size_bytes": act_size if act_size is not None else exp_size,
                    "expected_checksum": exp_hash,
                    "actual_checksum": act_hash or exp_hash,
                    "last_verified_at": str(now_utc),
                    "status_detail": "Physically verified present on disk with verified size and SHA-256 integrity.",
                })

    # Process unexpected chunks (files physically present on disk not belonging to this node)
    for cid, phys in physical_items.items():
        if cid not in expected_map:
            act_size = phys.get("size_bytes")
            act_hash = phys.get("sha256_hash")
            items.append({
                "chunk_id": cid,
                "file_id": None,
                "filename": None,
                "status": "UNEXPECTED",
                "expected_size_bytes": None,
                "actual_size_bytes": act_size,
                "expected_checksum": None,
                "actual_checksum": act_hash,
                "last_verified_at": None,
                "status_detail": "Chunk file physically exists in provider directory, but is not mapped in coordinator metadata.",
            })

    # Update verified timestamps in database
    if verified_chunk_ids_to_update:
        update_chunk_verification(provider_node_id, verified_chunk_ids_to_update, db_path=db_path)

    verified_count = sum(1 for it in items if it["status"] == "VERIFIED")
    missing_count = sum(1 for it in items if it["status"] == "MISSING")
    corrupted_count = sum(1 for it in items if it["status"] == "CORRUPTED")
    unexpected_count = sum(1 for it in items if it["status"] == "UNEXPECTED")
    unverified_count = sum(1 for it in items if it["status"] == "UNVERIFIED")

    return {
        "node_id": provider_node_id,
        "status": effective_status,
        "total_expected": len(expected_map),
        "total_physical": len(physical_items),
        "verified_count": verified_count,
        "missing_count": missing_count,
        "corrupted_count": corrupted_count,
        "unexpected_count": unexpected_count,
        "unverified_count": unverified_count,
        "reconciled_at": now_utc.isoformat(),
        "items": items,
    }


def get_admin_file_storage_records(
    db_path: Optional[Union[Path, str]] = None,
    heartbeat_timeout_seconds: int = 60,
    verification_freshness_seconds: int = 90,
) -> List[Dict[str, Any]]:
    """
    Comprehensive admin storage tracking query.
    Traces every file from consumer to its physical provider node(s), host computer name,
    online connectivity, latest chunk jobs (STORE, RETRIEVE, DELETE), and physical disk verification state.
    """
    now_utc = datetime.now(timezone.utc)

    def _parse_ts_utc(ts: Any) -> Optional[datetime]:
        if ts is None:
            return None
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(timezone.utc)
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace(" ", "T") if "T" not in ts else ts)
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                return None
        return None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Fetch all files
        cursor.execute(
            """
            SELECT id, owner_id, filename, file_size, encrypted_size, status, created_at, updated_at
            FROM files
            ORDER BY id DESC;
            """
        )
        files = cursor.fetchall()

        # 2. Fetch all users
        cursor.execute(
            """
            SELECT id, email, role, is_active
            FROM users;
            """
        )
        user_map = {u["id"]: u for u in cursor.fetchall()}

        # 3. Fetch all provider nodes
        cursor.execute(
            """
            SELECT id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, status, last_heartbeat, computer_name, created_at, updated_at
            FROM provider_nodes;
            """
        )
        node_map = {n["id"]: n for n in cursor.fetchall()}

        # 4. Fetch all file chunks
        cursor.execute(
            """
            SELECT id, file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status, last_verified_at, created_at
            FROM file_chunks
            ORDER BY file_id DESC, chunk_index ASC;
            """
        )
        all_chunks = cursor.fetchall()
        chunks_by_file: Dict[int, List[Dict[str, Any]]] = {}
        for c in all_chunks:
            chunks_by_file.setdefault(c["file_id"], []).append(c)

        # 5. Fetch all chunk jobs
        cursor.execute(
            """
            SELECT id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at
            FROM chunk_jobs
            ORDER BY id ASC;
            """
        )
        all_jobs = cursor.fetchall()
        jobs_by_chunk: Dict[str, List[Dict[str, Any]]] = {}
        jobs_by_file: Dict[int, List[Dict[str, Any]]] = {}
        for j in all_jobs:
            jobs_by_chunk.setdefault(j["chunk_id"], []).append(j)
            if j.get("file_id"):
                jobs_by_file.setdefault(j["file_id"], []).append(j)

    results: List[Dict[str, Any]] = []

    for f in files:
        consumer = user_map.get(f["owner_id"], {})
        f_chunks = chunks_by_file.get(f["id"], [])

        processed_chunks: List[Dict[str, Any]] = []

        for c in f_chunks:
            node = node_map.get(c["provider_node_id"])
            p_user = user_map.get(node["provider_id"], {}) if node else {}

            # Determine provider node connectivity & status
            if node:
                effective_status = compute_node_effective_status(node, heartbeat_timeout_seconds=heartbeat_timeout_seconds)
                is_online = (effective_status == "ONLINE")
                comp_name = node.get("computer_name")
                p_quota = node.get("quota_bytes")
                p_used = node.get("used_bytes")
                last_hb_str = str(node["last_heartbeat"]) if node.get("last_heartbeat") else None
            else:
                effective_status = "UNKNOWN"
                is_online = False
                comp_name = None
                p_quota = None
                p_used = None
                last_hb_str = None

            # Get jobs for this chunk
            c_jobs = jobs_by_chunk.get(c["chunk_id"], [])
            store_jobs = [j for j in c_jobs if j["job_type"] == "STORE_CHUNK"]
            retrieve_jobs = [j for j in c_jobs if j["job_type"] == "RETRIEVE_CHUNK"]
            delete_jobs = [j for j in c_jobs if j["job_type"] == "DELETE_CHUNK"]

            latest_store = store_jobs[-1] if store_jobs else None
            latest_retrieve = retrieve_jobs[-1] if retrieve_jobs else None
            latest_delete = delete_jobs[-1] if delete_jobs else None

            # Determine chunk physical storage verification state
            if f["status"] == "DELETED" or (latest_delete and latest_delete["status"] == "COMPLETED"):
                storage_status = "DELETED"
                status_detail = "Chunk marked as deleted or delete job completed."
            elif (latest_store and latest_store["status"] in ("FAILED", "TIMED_OUT")) or c["status"] == "FAILED":
                storage_status = "STORAGE_FAILED"
                status_detail = f"Chunk storage transfer failed: {latest_store.get('error_message') if latest_store else 'Failed'}"
            elif c["status"] == "PENDING" or (latest_store and latest_store["status"] in ("QUEUED", "PROCESSING")):
                storage_status = "DATABASE_ASSIGNED"
                status_detail = "Database assignment created; transfer in progress."
            else:
                # STORE_CHUNK completed or chunk status is STORED
                if not is_online:
                    storage_status = "PROVIDER_OFFLINE"
                    status_detail = "Provider node is offline; physical presence unverified."
                else:
                    verified_dt = _parse_ts_utc(c.get("last_verified_at"))
                    if verified_dt:
                        age_sec = (now_utc - verified_dt).total_seconds()
                        if age_sec <= verification_freshness_seconds:
                            storage_status = "VERIFIED_PHYSICAL"
                            status_detail = "Physically verified present on provider storage disk."
                        else:
                            storage_status = "PHYSICALLY_MISSING"
                            status_detail = "Provider is online, but chunk is missing from provider agent inventory."
                    else:
                        storage_status = "PHYSICALLY_MISSING"
                        status_detail = "Provider is online, but chunk was not reported in provider agent inventory."

            processed_chunks.append({
                "chunk_id": c["chunk_id"],
                "chunk_index": c["chunk_index"],
                "chunk_size": c["chunk_size"],
                "chunk_status": c["status"],
                "last_verified_at": str(c["last_verified_at"]) if c.get("last_verified_at") else None,
                "provider_node_id": c["provider_node_id"],
                "provider_email": p_user.get("email"),
                "computer_name": comp_name,
                "provider_status": effective_status,
                "provider_online": is_online,
                "last_heartbeat": last_hb_str,
                "quota_bytes": p_quota,
                "used_bytes": p_used,
                "storage_status": storage_status,
                "storage_status_detail": status_detail,
                "latest_store_job": {
                    "id": latest_store["id"],
                    "status": latest_store["status"],
                    "created_at": str(latest_store["created_at"]),
                    "updated_at": str(latest_store["updated_at"]) if latest_store.get("updated_at") else None,
                    "error_message": latest_store.get("error_message"),
                } if latest_store else None,
                "latest_retrieve_job": {
                    "id": latest_retrieve["id"],
                    "status": latest_retrieve["status"],
                    "created_at": str(latest_retrieve["created_at"]),
                    "updated_at": str(latest_retrieve["updated_at"]) if latest_retrieve.get("updated_at") else None,
                    "error_message": latest_retrieve.get("error_message"),
                } if latest_retrieve else None,
                "latest_delete_job": {
                    "id": latest_delete["id"],
                    "status": latest_delete["status"],
                    "created_at": str(latest_delete["created_at"]),
                    "updated_at": str(latest_delete["updated_at"]) if latest_delete.get("updated_at") else None,
                    "error_message": latest_delete.get("error_message"),
                } if latest_delete else None,
                "all_jobs": [
                    {
                        "id": j["id"],
                        "job_type": j["job_type"],
                        "status": j["status"],
                        "created_at": str(j["created_at"]),
                        "updated_at": str(j["updated_at"]) if j.get("updated_at") else None,
                        "error_message": j.get("error_message"),
                    }
                    for j in c_jobs
                ],
            })

        # Determine file-level summary storage status
        if f["status"] == "DELETED":
            file_storage_status = "DELETED"
            file_status_detail = "File has been deleted."
        elif not processed_chunks:
            file_storage_status = "DATABASE_ASSIGNED"
            file_status_detail = "No chunks mapped yet."
        elif any(ck["storage_status"] == "STORAGE_FAILED" for ck in processed_chunks):
            file_storage_status = "STORAGE_FAILED"
            file_status_detail = "One or more chunks failed storage transfer."
        elif any(ck["storage_status"] == "PHYSICALLY_MISSING" for ck in processed_chunks):
            file_storage_status = "PHYSICALLY_MISSING"
            file_status_detail = "One or more chunks missing from provider storage disk."
        elif any(ck["storage_status"] == "PROVIDER_OFFLINE" for ck in processed_chunks):
            file_storage_status = "PROVIDER_OFFLINE"
            file_status_detail = "Provider node is offline; physical presence unverified."
        elif any(ck["storage_status"] == "DATABASE_ASSIGNED" for ck in processed_chunks):
            file_storage_status = "DATABASE_ASSIGNED"
            file_status_detail = "Chunk transfer in progress."
        elif all(ck["storage_status"] == "VERIFIED_PHYSICAL" for ck in processed_chunks):
            file_storage_status = "VERIFIED_PHYSICAL"
            file_status_detail = "All chunks verified present on physical provider disk."
        else:
            file_storage_status = "STORED"
            file_status_detail = "Chunks stored."

        # If chunks were deleted/unlinked but job audit history exists in chunk_jobs:
        if not processed_chunks and f["id"] in jobs_by_file:
            f_jobs = jobs_by_file[f["id"]]
            store_jobs = [j for j in f_jobs if j["job_type"] == "STORE_CHUNK"]
            retrieve_jobs = [j for j in f_jobs if j["job_type"] == "RETRIEVE_CHUNK"]
            delete_jobs = [j for j in f_jobs if j["job_type"] == "DELETE_CHUNK"]
            latest_store = store_jobs[-1] if store_jobs else None
            latest_retrieve = retrieve_jobs[-1] if retrieve_jobs else None
            latest_delete = delete_jobs[-1] if delete_jobs else None
            ref_job = latest_delete or latest_store or f_jobs[-1]
            ref_node = node_map.get(ref_job["provider_node_id"]) if ref_job else None
            ref_user = user_map.get(ref_node["provider_id"], {}) if ref_node else {}
            ref_status = compute_node_effective_status(ref_node, heartbeat_timeout_seconds=heartbeat_timeout_seconds) if ref_node else "UNKNOWN"
            ref_online = (ref_status == "ONLINE")

            processed_chunks.append({
                "chunk_id": ref_job["chunk_id"],
                "chunk_index": 0,
                "chunk_size": f["encrypted_size"],
                "chunk_status": "DELETED" if f["status"] == "DELETED" else "UNKNOWN",
                "last_verified_at": None,
                "provider_node_id": ref_job["provider_node_id"],
                "provider_email": ref_user.get("email"),
                "computer_name": ref_node.get("computer_name") if ref_node else None,
                "provider_status": ref_status,
                "provider_online": ref_online,
                "last_heartbeat": str(ref_node["last_heartbeat"]) if ref_node and ref_node.get("last_heartbeat") else None,
                "quota_bytes": ref_node.get("quota_bytes") if ref_node else None,
                "used_bytes": ref_node.get("used_bytes") if ref_node else None,
                "storage_status": file_storage_status,
                "storage_status_detail": file_status_detail,
                "latest_store_job": {
                    "id": latest_store["id"],
                    "status": latest_store["status"],
                    "created_at": str(latest_store["created_at"]),
                    "updated_at": str(latest_store["updated_at"]) if latest_store.get("updated_at") else None,
                    "error_message": latest_store.get("error_message"),
                } if latest_store else None,
                "latest_retrieve_job": {
                    "id": latest_retrieve["id"],
                    "status": latest_retrieve["status"],
                    "created_at": str(latest_retrieve["created_at"]),
                    "updated_at": str(latest_retrieve["updated_at"]) if latest_retrieve.get("updated_at") else None,
                    "error_message": latest_retrieve.get("error_message"),
                } if latest_retrieve else None,
                "latest_delete_job": {
                    "id": latest_delete["id"],
                    "status": latest_delete["status"],
                    "created_at": str(latest_delete["created_at"]),
                    "updated_at": str(latest_delete["updated_at"]) if latest_delete.get("updated_at") else None,
                    "error_message": latest_delete.get("error_message"),
                } if latest_delete else None,
                "all_jobs": [
                    {
                        "id": j["id"],
                        "job_type": j["job_type"],
                        "status": j["status"],
                        "created_at": str(j["created_at"]),
                        "updated_at": str(j["updated_at"]) if j.get("updated_at") else None,
                        "error_message": j.get("error_message"),
                    }
                    for j in f_jobs
                ],
            })

        primary = processed_chunks[0] if processed_chunks else None

        results.append({
            "file_id": f["id"],
            "filename": f["filename"],
            "consumer_id": f["owner_id"],
            "consumer_email": consumer.get("email"),
            "file_size": f["file_size"],
            "encrypted_size": f["encrypted_size"],
            "lifecycle_status": f["status"],
            "storage_status": file_storage_status,
            "storage_status_detail": file_status_detail,
            "created_at": str(f["created_at"]) if f.get("created_at") else None,
            "updated_at": str(f["updated_at"]) if f.get("updated_at") else None,
            # Top-level primary chunk telemetry (convenience for flat representations)
            "chunk_id": primary["chunk_id"] if primary else None,
            "chunk_index": primary["chunk_index"] if primary else None,
            "provider_node_id": primary["provider_node_id"] if primary else None,
            "provider_email": primary["provider_email"] if primary else None,
            "computer_name": primary["computer_name"] if primary else None,
            "provider_status": primary["provider_status"] if primary else None,
            "provider_online": primary["provider_online"] if primary else False,
            "last_heartbeat": primary["last_heartbeat"] if primary else None,
            "quota_bytes": primary["quota_bytes"] if primary else None,
            "used_bytes": primary["used_bytes"] if primary else None,
            "last_verified_at": primary["last_verified_at"] if primary else None,
            "latest_store_job": primary["latest_store_job"] if primary else None,
            "latest_retrieve_job": primary["latest_retrieve_job"] if primary else None,
            "latest_delete_job": primary["latest_delete_job"] if primary else None,
            # Complete list of all chunk placement mappings & job history
            "chunks": processed_chunks,
        })

    return results
