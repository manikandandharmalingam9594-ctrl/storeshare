"""
Coordinator transient chunk spool management.

Stores ephemeral ciphertext during transfer handoffs between consumer and provider.
Transient chunks are bounded in size (<= 5 MB), validated for path safety,
and purged immediately on job completion, failure, timeout, or server restart.
Zero permanent file storage on the coordinator.
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional
import uuid

from backend.database import CHUNK_ID_PATTERN, DATA_DIR, MAX_FILE_SIZE_BYTES

logger = logging.getLogger("store-share-coordinator.spool")
SPOOL_DIR: Path = DATA_DIR / "spool"


def init_spool_dir() -> Path:
    """Ensure spool directory exists."""
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    return SPOOL_DIR


def save_spool_chunk(chunk_id: str, data: bytes) -> Path:
    """
    Save ephemeral ciphertext chunk payload to the coordinator spool.
    Enforces maximum size and atomic write.
    """
    if not chunk_id or not CHUNK_ID_PATTERN.match(chunk_id):
        raise ValueError(f"Invalid chunk ID format: '{chunk_id}'")

    if len(data) == 0:
        raise ValueError("Cannot spool empty chunk payload.")

    if len(data) > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"Chunk payload size ({len(data)} bytes) exceeds maximum allowable limit of {MAX_FILE_SIZE_BYTES} bytes."
        )

    init_spool_dir()
    resolved_dir = SPOOL_DIR.resolve()
    target_path = (SPOOL_DIR / f"{chunk_id}.spool").resolve()

    # Path traversal protection
    try:
        target_path.relative_to(resolved_dir)
    except ValueError:
        raise ValueError(f"Path traversal detected in chunk ID '{chunk_id}'")

    temp_path = SPOOL_DIR / f".tmp_{uuid.uuid4().hex}"
    try:
        with open(temp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target_path)
        logger.debug(f"Spool chunk saved: {chunk_id} ({len(data)} bytes)")
        return target_path
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"Failed to spool chunk {chunk_id}: {exc}") from exc


def get_spool_chunk(chunk_id: str) -> Optional[bytes]:
    """
    Read ephemeral chunk payload from the coordinator spool.
    Returns None if chunk does not exist or has already been purged.
    """
    if not chunk_id or not CHUNK_ID_PATTERN.match(chunk_id):
        return None

    resolved_dir = SPOOL_DIR.resolve()
    target_path = (SPOOL_DIR / f"{chunk_id}.spool").resolve()

    try:
        target_path.relative_to(resolved_dir)
    except ValueError:
        return None

    if not target_path.is_file():
        return None

    try:
        return target_path.read_bytes()
    except Exception as exc:
        logger.warning(f"Error reading spool chunk {chunk_id}: {exc}")
        return None


def remove_spool_chunk(chunk_id: str) -> bool:
    """
    Purge ephemeral chunk payload from the coordinator spool.
    """
    if not chunk_id or not CHUNK_ID_PATTERN.match(chunk_id):
        return False

    resolved_dir = SPOOL_DIR.resolve()
    target_path = (SPOOL_DIR / f"{chunk_id}.spool").resolve()

    try:
        target_path.relative_to(resolved_dir)
    except ValueError:
        return False

    if target_path.is_file():
        try:
            target_path.unlink()
            logger.debug(f"Purged spool chunk: {chunk_id}")
            return True
        except OSError as exc:
            logger.warning(f"Failed to remove spool chunk {chunk_id}: {exc}")
    return False


def cleanup_all_spool() -> int:
    """
    Purge all orphaned temporary files and spool chunks from the coordinator spool directory.
    Called on server startup / shutdown to enforce zero persistent ciphertext on coordinator.
    """
    init_spool_dir()
    cleaned = 0
    for entry in SPOOL_DIR.iterdir():
        if entry.is_file():
            try:
                entry.unlink()
                cleaned += 1
            except OSError:
                pass
    if cleaned > 0:
        logger.info(f"Cleaned up {cleaned} transient spool files on coordinator.")
    return cleaned
