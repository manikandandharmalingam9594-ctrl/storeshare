"""
Local Storage Manager for Store-Share Provider Agent.

Responsibilities:
1. Validates and manages the local storage directory and enforces quota limits.
2. Checks filesystem free space (via shutil.disk_usage) and ensures allocated quota is feasible.
3. Strictly isolates all storage operations within the configured directory.
4. Prevents path traversal (e.g. '../', absolute escapes) and symlink-based escapes.
5. Implements atomic writes (write-to-temp + fsync + atomic rename) and thread-safe quota reservations.
6. Stores NO plaintext consumer files or encryption keys (operates solely on opaque binary chunks).
"""

import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("store-share-agent.storage")

# Valid chunk ID: alphanumeric string with hyphens or underscores (e.g. hex sha256 or uuid)
CHUNK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


class StorageError(Exception):
    """Base exception for storage manager errors."""
    pass


class QuotaExceededError(StorageError):
    """Raised when an operation would exceed configured storage quota or free space."""
    pass


class SecurityViolationError(StorageError):
    """Raised when a path traversal, symlink escape, or illegal access is attempted."""
    pass


class StorageManager:
    """
    Manages local storage allocation and atomic chunk operations for a provider node.
    Thread-safe quota reservation and accounting.
    """

    def __init__(
        self,
        storage_dir: str | Path,
        quota_bytes: int,
        create_if_missing: bool = True,
    ) -> None:
        if quota_bytes <= 0:
            raise ValueError(f"Quota bytes must be greater than 0, received: {quota_bytes}")

        self.raw_dir_path = str(storage_dir)
        self.storage_dir = Path(storage_dir).resolve()
        self.quota_bytes = int(quota_bytes)
        self.chunks_dir = self.storage_dir / "chunks"

        # Concurrency lock for quota reservations and writes
        self._lock = threading.Lock()
        self._used_bytes: int = 0
        self._reserved_bytes: int = 0

        # Initialize and validate filesystem directory
        self._init_and_validate_directory(create_if_missing=create_if_missing)

        # Validate filesystem free space
        self._validate_free_space()

        # Scan existing stored chunks and calculate committed usage
        self._scan_and_cleanup()

    def _init_and_validate_directory(self, create_if_missing: bool) -> None:
        """
        Validate that the target directory is safe, writable, and not a symlink.
        """
        # Prohibit root directory or system roots
        if self.storage_dir == Path(self.storage_dir.anchor) or self.storage_dir.parent == self.storage_dir:
            raise SecurityViolationError(
                f"Configured storage directory cannot be the filesystem root: '{self.storage_dir}'"
            )

        if not self.storage_dir.exists():
            if create_if_missing:
                try:
                    self.storage_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created storage directory: {self.storage_dir}")
                except Exception as exc:
                    raise StorageError(f"Failed to create storage directory '{self.storage_dir}': {exc}") from exc
            else:
                raise StorageError(f"Storage directory does not exist: '{self.storage_dir}'")

        if not self.storage_dir.is_dir():
            raise StorageError(f"Storage path exists but is not a directory: '{self.storage_dir}'")

        # Check if storage directory is a symlink or contains symlinks in path
        if self.storage_dir.is_symlink():
            raise SecurityViolationError(
                f"Storage directory is a symlink, which is prohibited for security: '{self.storage_dir}'"
            )

        # Ensure chunks subdirectory exists
        try:
            self.chunks_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise StorageError(f"Failed to create chunks subdirectory '{self.chunks_dir}': {exc}") from exc

        # Test write permissions by creating and removing a test probe
        probe_file = self.storage_dir / f".write_probe_{uuid.uuid4().hex}"
        try:
            probe_file.write_text("probe")
            probe_file.unlink()
        except Exception as exc:
            raise StorageError(f"Storage directory is not writable: '{self.storage_dir}': {exc}") from exc

    def _validate_free_space(self) -> None:
        """
        Check that physical filesystem free space is sufficient.
        """
        try:
            usage = shutil.disk_usage(self.storage_dir)
            if self.quota_bytes > usage.total:
                raise ValueError(
                    f"Configured quota ({self.quota_bytes} bytes) exceeds total drive capacity ({usage.total} bytes)."
                )
            # Log warning if requested quota exceeds currently free bytes
            if self.quota_bytes > usage.free:
                logger.warning(
                    f"Requested quota ({self.quota_bytes} bytes) exceeds currently available drive free space "
                    f"({usage.free} bytes). Writes will be limited by actual free space."
                )
        except (ValueError, StorageError):
            raise
        except Exception as exc:
            logger.warning(f"Could not inspect disk usage for '{self.storage_dir}': {exc}")

    def _scan_and_cleanup(self) -> None:
        """
        Calculate actual used bytes from existing valid chunks and clean up orphaned temporary files.
        """
        with self._lock:
            total_size = 0
            orphaned_tmp = 0

            for entry in self.chunks_dir.iterdir():
                try:
                    # Clean up interrupted temporary write files (.tmp_*)
                    if entry.name.startswith(".tmp_"):
                        entry.unlink(missing_ok=True)
                        orphaned_tmp += 1
                        continue

                    if entry.is_symlink():
                        logger.warning(f"Removing prohibited symlink found in chunk storage: {entry.name}")
                        entry.unlink(missing_ok=True)
                        continue

                    if entry.is_file():
                        total_size += entry.stat().st_size
                except Exception as exc:
                    logger.warning(f"Error inspecting chunk file {entry.name}: {exc}")

            self._used_bytes = total_size
            if orphaned_tmp > 0:
                logger.info(f"Cleaned up {orphaned_tmp} orphaned temporary chunk files.")
            logger.info(f"Storage initialized: {self._used_bytes} bytes used across existing chunks.")

    def get_safe_chunk_path(self, chunk_id: str) -> Path:
        """
        Strictly resolves and validates chunk file path against path traversal and symlinks.
        """
        if not chunk_id or not isinstance(chunk_id, str):
            raise SecurityViolationError("Chunk ID must be a non-empty string.")

        # Check regex: only allow safe characters (no slashes, no backslashes, no null bytes, no '..')
        if not CHUNK_ID_PATTERN.match(chunk_id):
            raise SecurityViolationError(
                f"Invalid chunk ID format: '{chunk_id}'. Must be alphanumeric with '-' or '_'."
            )

        if ".." in chunk_id or "/" in chunk_id or "\\" in chunk_id:
            raise SecurityViolationError(f"Path traversal detected in chunk ID: '{chunk_id}'")

        target = (self.chunks_dir / chunk_id).resolve()
        resolved_chunks_dir = self.chunks_dir.resolve()

        # Enforce that target is strictly inside chunks directory
        try:
            target.relative_to(resolved_chunks_dir)
        except ValueError:
            raise SecurityViolationError(
                f"Path traversal attempt: chunk path '{target}' escapes '{resolved_chunks_dir}'"
            )

        # Enforce that target is not a symlink
        if target.is_symlink():
            raise SecurityViolationError(f"Target chunk path is a prohibited symlink: '{target}'")

        return target

    def reserve_space(self, num_bytes: int) -> bool:
        """
        Atomically reserve storage quota for an upcoming write.
        Prevents race conditions where concurrent transfers exceed available quota.
        """
        if num_bytes <= 0:
            raise ValueError(f"Reservation size must be positive, got {num_bytes}")

        with self._lock:
            # Check configured Store-Share quota
            projected = self._used_bytes + self._reserved_bytes + num_bytes
            if projected > self.quota_bytes:
                raise QuotaExceededError(
                    f"Quota exceeded: requested {num_bytes} bytes, but only "
                    f"{max(0, self.quota_bytes - self._used_bytes - self._reserved_bytes)} bytes remain."
                )

            # Check physical filesystem free space
            try:
                free_disk = shutil.disk_usage(self.storage_dir).free
                if self._reserved_bytes + num_bytes > free_disk:
                    raise QuotaExceededError(
                        f"Physical disk full: requested {num_bytes} bytes, but disk only has {free_disk} free bytes."
                    )
            except (QuotaExceededError, ValueError):
                raise
            except Exception as exc:
                logger.warning(f"Could not verify physical disk space: {exc}")

            self._reserved_bytes += num_bytes
            return True

    def release_reservation(self, num_bytes: int) -> None:
        """
        Release previously reserved space if an in-flight write fails or cancels.
        """
        if num_bytes <= 0:
            return
        with self._lock:
            self._reserved_bytes = max(0, self._reserved_bytes - num_bytes)

    def write_chunk_atomic(
        self,
        chunk_id: str,
        data: bytes,
        pre_reserved: bool = False,
    ) -> int:
        """
        Atomically write an opaque chunk payload to the storage directory.
        Steps:
        1. Validates chunk path and checks quota.
        2. Writes data to a temporary file (.tmp_<uuid>) in the chunks directory.
        3. Flushes and fsyncs to ensure data is physically committed to disk.
        4. Atomically replaces target chunk file (os.replace).
        5. Updates internal accounting.
        """
        chunk_size = len(data)
        if chunk_size == 0:
            raise ValueError("Chunk data cannot be empty.")

        target_path = self.get_safe_chunk_path(chunk_id)

        # Acquire lock to verify quota and atomically reserve in-flight space
        with self._lock:
            # If target already exists, calculate difference
            existing_size = target_path.stat().st_size if target_path.exists() else 0
            size_delta = chunk_size - existing_size

            if not pre_reserved:
                if size_delta > 0 and (self._used_bytes + self._reserved_bytes + size_delta > self.quota_bytes):
                    raise QuotaExceededError(
                        f"Writing chunk {chunk_id} ({chunk_size} bytes) exceeds quota."
                    )
                # Check physical disk space
                try:
                    free_disk = shutil.disk_usage(self.storage_dir).free
                    if self._reserved_bytes + size_delta > free_disk:
                        raise QuotaExceededError(
                            f"Physical disk full: requested {size_delta} bytes, but disk only has {free_disk} free bytes."
                        )
                except (QuotaExceededError, ValueError):
                    raise
                except Exception as exc:
                    logger.warning(f"Could not verify physical disk space: {exc}")

                if size_delta > 0:
                    self._reserved_bytes += size_delta

        # Write to temporary file with unique UUID
        temp_filename = f".tmp_{uuid.uuid4().hex}"
        temp_path = self.chunks_dir / temp_filename

        try:
            with open(temp_path, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename / replace
            os.replace(temp_path, target_path)

            # Update committed usage and release in-flight reservation
            with self._lock:
                if not pre_reserved and size_delta > 0:
                    self._reserved_bytes = max(0, self._reserved_bytes - size_delta)
                elif pre_reserved:
                    self._reserved_bytes = max(0, self._reserved_bytes - chunk_size)
                self._used_bytes = max(0, self._used_bytes + size_delta)

            return chunk_size

        except Exception as exc:
            # Clean up temp file if present
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            # Release in-flight reservation
            if not pre_reserved and size_delta > 0:
                self.release_reservation(size_delta)
            elif pre_reserved:
                self.release_reservation(chunk_size)
            raise StorageError(f"Failed to write chunk {chunk_id} atomically: {exc}") from exc

    def read_chunk(self, chunk_id: str) -> bytes:
        """
        Read an opaque chunk payload from the storage directory.
        """
        target_path = self.get_safe_chunk_path(chunk_id)
        if not target_path.is_file():
            raise FileNotFoundError(f"Chunk not found: {chunk_id}")

        try:
            return target_path.read_bytes()
        except Exception as exc:
            raise StorageError(f"Failed to read chunk {chunk_id}: {exc}") from exc

    def delete_chunk(self, chunk_id: str) -> bool:
        """
        Safely delete a stored chunk and update committed quota accounting.
        """
        target_path = self.get_safe_chunk_path(chunk_id)
        if not target_path.exists():
            return False

        with self._lock:
            try:
                size = target_path.stat().st_size
                target_path.unlink()
                self._used_bytes = max(0, self._used_bytes - size)
                return True
            except Exception as exc:
                raise StorageError(f"Failed to delete chunk {chunk_id}: {exc}") from exc

    def get_stats(self) -> Dict[str, Any]:
        """
        Return current live storage statistics.
        """
        with self._lock:
            try:
                disk_usage = shutil.disk_usage(self.storage_dir)
                free_disk_bytes = disk_usage.free
                total_disk_bytes = disk_usage.total
            except Exception:
                free_disk_bytes = 0
                total_disk_bytes = 0

            available_quota = max(0, self.quota_bytes - self._used_bytes - self._reserved_bytes)
            chunk_files = [f for f in self.chunks_dir.iterdir() if f.is_file() and not f.name.startswith(".tmp_")]

            return {
                "storage_dir": str(self.storage_dir),
                "quota_bytes": self.quota_bytes,
                "quota_gb": round(self.quota_bytes / (1024**3), 2),
                "used_bytes": self._used_bytes,
                "used_mb": round(self._used_bytes / (1024**2), 2),
                "reserved_bytes": self._reserved_bytes,
                "available_quota_bytes": available_quota,
                "available_quota_gb": round(available_quota / (1024**3), 2),
                "free_disk_bytes": free_disk_bytes,
                "total_disk_bytes": total_disk_bytes,
                "chunk_count": len(chunk_files),
            }

    def set_quota(self, new_quota_bytes: int) -> int:
        """
        Safely update the configured storage quota without deleting existing data.
        If new quota is smaller than current usage, blocks future writes until space is freed.
        """
        if new_quota_bytes <= 0:
            raise ValueError(f"Quota bytes must be greater than 0, received: {new_quota_bytes}")

        with self._lock:
            self.quota_bytes = int(new_quota_bytes)
            if self._used_bytes > self.quota_bytes:
                logger.warning(
                    f"New quota ({self.quota_bytes} bytes) is less than existing used space ({self._used_bytes} bytes). "
                    "Existing data is preserved, but new writes will be blocked until usage falls below quota."
                )
            else:
                logger.info(f"Storage quota updated safely to {self.quota_bytes} bytes.")
            return self.quota_bytes

    def get_chunk_inventory(self) -> list[str]:
        """
        Return the list of chunk IDs physically stored on disk in the chunks directory.
        Only returns valid chunk IDs without exposing filesystem paths.
        """
        with self._lock:
            try:
                if not self.chunks_dir.exists():
                    return []
                return [
                    f.name
                    for f in self.chunks_dir.iterdir()
                    if f.is_file() and not f.name.startswith(".tmp_") and CHUNK_ID_PATTERN.match(f.name)
                ]
            except Exception as exc:
                logger.warning(f"Failed to scan chunk inventory: {exc}")
                return []

    def get_detailed_inventory(self) -> List[Dict[str, Any]]:
        """
        Return the list of chunk metadata physically stored on disk, including
        chunk ID, actual byte size, SHA-256 hash, and modification timestamp.
        Does not expose filesystem paths.
        """
        with self._lock:
            inventory: List[Dict[str, Any]] = []
            try:
                if not self.chunks_dir.exists():
                    return []
                for entry in self.chunks_dir.iterdir():
                    try:
                        if entry.name.startswith(".tmp_") or entry.is_symlink() or not entry.is_file():
                            continue
                        if not CHUNK_ID_PATTERN.match(entry.name):
                            continue

                        st = entry.stat()
                        size = st.st_size

                        # Compute SHA-256 hash of opaque chunk ciphertext
                        sha = hashlib.sha256()
                        with open(entry, "rb") as f:
                            while chunk := f.read(65536):
                                sha.update(chunk)

                        inventory.append({
                            "chunk_id": entry.name,
                            "size_bytes": size,
                            "sha256_hash": sha.hexdigest(),
                            "modified_at": int(st.st_mtime),
                        })
                    except Exception as exc:
                        logger.warning(f"Error inspecting chunk {entry.name}: {exc}")
                        continue
            except Exception as exc:
                logger.warning(f"Failed to scan detailed chunk inventory: {exc}")
            return inventory

    def verify_single_chunk(
        self,
        chunk_id: str,
        expected_size: Optional[int] = None,
        expected_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Inspect physical existence, byte size, and SHA-256 hash of a single chunk.
        """
        try:
            target_path = self.get_safe_chunk_path(chunk_id)
        except SecurityViolationError as sve:
            return {
                "chunk_id": chunk_id,
                "exists": False,
                "error": str(sve),
                "is_valid": False,
            }

        if not target_path.is_file():
            return {
                "chunk_id": chunk_id,
                "exists": False,
                "actual_size": 0,
                "sha256_hash": None,
                "is_valid": False,
            }

        try:
            st = target_path.stat()
            actual_size = st.st_size
            sha = hashlib.sha256()
            with open(target_path, "rb") as f:
                while chunk := f.read(65536):
                    sha.update(chunk)
            act_hash = sha.hexdigest()

            matches_size = (expected_size is None or actual_size == expected_size)
            matches_hash = (expected_hash is None or act_hash.lower() == expected_hash.lower())
            is_valid = matches_size and matches_hash

            return {
                "chunk_id": chunk_id,
                "exists": True,
                "actual_size": actual_size,
                "sha256_hash": act_hash,
                "matches_size": matches_size,
                "matches_hash": matches_hash,
                "is_valid": is_valid,
            }
        except Exception as exc:
            return {
                "chunk_id": chunk_id,
                "exists": True,
                "error": str(exc),
                "is_valid": False,
            }

