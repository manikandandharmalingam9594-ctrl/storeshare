"""
Unit and integration tests for Phase 1: Database tables, helper functions,
job lifecycle, and transactional quota reservation.

Tests verify:
1. File metadata constraints (<= 5 MB limit, valid hex IV, valid SHA-256 hash).
2. Zero server decryption key knowledge (no keys accepted or stored).
3. Consumer ownership isolation (users cannot access files belonging to others).
4. Transactional provider quota reservation, commitment, and release.
5. Eligible provider selection with heartbeat freshness.
6. Provider-specific job polling isolation (providers only see their own jobs).
7. Job lifecycle transitions (QUEUED -> PROCESSING -> COMPLETED / FAILED / TIMED_OUT).
8. Idempotent job completions and automatic file status updates.
9. Stale job reaping and quota rollback on timeout.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
import sqlite3

from backend.database import (
    MAX_FILE_SIZE_BYTES,
    commit_provider_quota,
    complete_chunk_job,
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    create_user,
    expire_stale_jobs,
    fail_chunk_job,
    find_eligible_provider_node,
    get_chunk_by_id,
    get_chunk_job_by_id,
    get_file_by_id,
    get_file_chunks,
    get_provider_node,
    get_provider_node_by_id,
    list_consumer_files,
    poll_provider_jobs,
    record_provider_heartbeat,
    release_provider_quota,
    reserve_provider_quota,
    update_file_status,
    upsert_provider_node,
)
from backend.security import hash_password


@pytest.fixture
def consumer_user():
    user = create_user(
        email="consumer_test_p1@storeshare.io",
        password_hash=hash_password("ConsumerSecure123!"),
        role="CONSUMER",
    )
    return user


@pytest.fixture
def other_consumer_user():
    user = create_user(
        email="other_consumer_p1@storeshare.io",
        password_hash=hash_password("OtherSecure123!"),
        role="CONSUMER",
    )
    return user


@pytest.fixture
def online_provider_node():
    provider_user = create_user(
        email="provider_node_p1@storeshare.io",
        password_hash=hash_password("ProviderSecure123!"),
        role="PROVIDER",
    )
    # Configure 100 MB quota
    quota = 100 * 1024 * 1024
    node = upsert_provider_node(
        provider_id=provider_user["id"],
        quota_bytes=quota,
        storage_dir="C:\\storeshare-storage",
        status="ONLINE",
    )
    # Record heartbeat so last_heartbeat is current
    record_provider_heartbeat(
        provider_id=provider_user["id"],
        used_bytes=0,
        allocated_bytes=quota,
    )
    return get_provider_node_by_id(node["id"])


# ==============================================================================
# 1. File Metadata Validation & Zero-Knowledge Tests
# ==============================================================================

def test_create_file_record_success(consumer_user):
    iv = "0123456789abcdef01234567"  # 24 hex characters = 12 bytes
    sha = "a" * 64                    # 64 hex characters = 32 bytes
    size = 1024 * 100                 # 100 KB
    enc_size = size + 16              # GCM tag overhead

    file_rec = create_file_record(
        owner_id=consumer_user["id"],
        filename="financial_report.pdf",
        file_size=size,
        encrypted_size=enc_size,
        iv_hex=iv,
        sha256_hash=sha,
    )

    assert file_rec["id"] is not None
    assert file_rec["owner_id"] == consumer_user["id"]
    assert file_rec["filename"] == "financial_report.pdf"
    assert file_rec["file_size"] == size
    assert file_rec["encrypted_size"] == enc_size
    assert file_rec["iv_hex"] == iv
    assert file_rec["sha256_hash"] == sha
    assert file_rec["status"] == "PENDING_UPLOAD"
    # Verify no raw key column exists in the record
    assert "key" not in file_rec
    assert "decryption_key" not in file_rec


def test_create_file_record_size_limit_enforced(consumer_user):
    iv = "0123456789abcdef01234567"
    sha = "b" * 64

    # File exceeding 5 MB must raise ValueError
    oversized = MAX_FILE_SIZE_BYTES + 1
    with pytest.raises(ValueError, match="maximum allowed size"):
        create_file_record(
            owner_id=consumer_user["id"],
            filename="large_video.mp4",
            file_size=oversized,
            encrypted_size=oversized + 16,
            iv_hex=iv,
            sha256_hash=sha,
        )

    # 0 or negative size must raise ValueError
    with pytest.raises(ValueError, match="greater than 0"):
        create_file_record(
            owner_id=consumer_user["id"],
            filename="empty.txt",
            file_size=0,
            encrypted_size=16,
            iv_hex=iv,
            sha256_hash=sha,
        )


def test_create_file_record_invalid_parameters(consumer_user):
    sha = "c" * 64

    # Empty filename
    with pytest.raises(ValueError, match="Filename cannot be empty"):
        create_file_record(
            owner_id=consumer_user["id"],
            filename="   ",
            file_size=1000,
            encrypted_size=1016,
            iv_hex="0123456789abcdef01234567",
            sha256_hash=sha,
        )

    # Invalid IV (too short or non-hex)
    with pytest.raises(ValueError, match="Invalid IV"):
        create_file_record(
            owner_id=consumer_user["id"],
            filename="test.txt",
            file_size=1000,
            encrypted_size=1016,
            iv_hex="tooshort",
            sha256_hash=sha,
        )

    with pytest.raises(ValueError, match="Invalid IV"):
        create_file_record(
            owner_id=consumer_user["id"],
            filename="test.txt",
            file_size=1000,
            encrypted_size=1016,
            iv_hex="zzzzzzzzzzzzzzzzzzzzzzzz",
            sha256_hash=sha,
        )

    # Invalid SHA-256 (too short or non-hex)
    with pytest.raises(ValueError, match="Invalid SHA-256"):
        create_file_record(
            owner_id=consumer_user["id"],
            filename="test.txt",
            file_size=1000,
            encrypted_size=1016,
            iv_hex="0123456789abcdef01234567",
            sha256_hash="not_64_characters",
        )


# ==============================================================================
# 2. Consumer File Ownership Isolation
# ==============================================================================

def test_file_ownership_isolation(consumer_user, other_consumer_user):
    iv = "0123456789abcdef01234567"
    sha = "d" * 64

    f1 = create_file_record(
        owner_id=consumer_user["id"],
        filename="secret_doc.pdf",
        file_size=5000,
        encrypted_size=5016,
        iv_hex=iv,
        sha256_hash=sha,
    )

    # Owner can fetch
    assert get_file_by_id(f1["id"], owner_id=consumer_user["id"]) is not None

    # Other consumer cannot access file
    assert get_file_by_id(f1["id"], owner_id=other_consumer_user["id"]) is None

    # list_consumer_files isolates per user
    user1_files = list_consumer_files(consumer_user["id"])
    assert len(user1_files) == 1
    assert user1_files[0]["id"] == f1["id"]

    other_files = list_consumer_files(other_consumer_user["id"])
    assert len(other_files) == 0

    # Deleted file is excluded from consumer files list
    update_file_status(f1["id"], "DELETED")
    assert len(list_consumer_files(consumer_user["id"])) == 0


# ==============================================================================
# 3. Transactional Quota Reservation & Provider Selection
# ==============================================================================

def test_find_eligible_provider_node(online_provider_node):
    # Online node with 100 MB quota can accept 10 MB
    eligible = find_eligible_provider_node(required_bytes=10 * 1024 * 1024)
    assert eligible is not None
    assert eligible["id"] == online_provider_node["id"]

    # Requesting more than available quota (150 MB > 100 MB) returns None
    assert find_eligible_provider_node(required_bytes=150 * 1024 * 1024) is None


def test_find_eligible_provider_rejects_stale_heartbeat(online_provider_node):
    # Fast-forward heartbeat age past 60s
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")
    from backend.database import get_db_connection
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE provider_nodes SET last_heartbeat = ? WHERE id = ?;",
            (stale_time, online_provider_node["id"]),
        )
        conn.commit()

    # Must reject stale provider node
    assert find_eligible_provider_node(required_bytes=1000) is None


def test_transactional_quota_reservation_lifecycle(online_provider_node):
    node_id = online_provider_node["id"]
    quota_size = 100 * 1024 * 1024

    # 1. Reserve 40 MB
    assert reserve_provider_quota(node_id, 40 * 1024 * 1024) is True
    node = get_provider_node_by_id(node_id)
    assert node["reserved_bytes"] == 40 * 1024 * 1024
    assert node["used_bytes"] == 0

    # 2. Reserve another 50 MB (Total 90 MB <= 100 MB)
    assert reserve_provider_quota(node_id, 50 * 1024 * 1024) is True
    node = get_provider_node_by_id(node_id)
    assert node["reserved_bytes"] == 90 * 1024 * 1024

    # 3. Attempting to reserve 20 MB fails (90 + 20 = 110 > 100 MB)
    assert reserve_provider_quota(node_id, 20 * 1024 * 1024) is False
    assert get_provider_node_by_id(node_id)["reserved_bytes"] == 90 * 1024 * 1024

    # 4. Commit 40 MB when transfer completes: reserved -> used
    assert commit_provider_quota(node_id, 40 * 1024 * 1024) is True
    node = get_provider_node_by_id(node_id)
    assert node["used_bytes"] == 40 * 1024 * 1024
    assert node["reserved_bytes"] == 50 * 1024 * 1024

    # 5. Release remaining 50 MB on abort/timeout
    assert release_provider_quota(node_id, 50 * 1024 * 1024) is True
    node = get_provider_node_by_id(node_id)
    assert node["used_bytes"] == 40 * 1024 * 1024
    assert node["reserved_bytes"] == 0


# ==============================================================================
# 4. Chunk Placement & Provider Job Lifecycle
# ==============================================================================

def test_chunk_job_lifecycle_and_idempotency(consumer_user, online_provider_node):
    iv = "0123456789abcdef01234567"
    sha = "e" * 64
    size = 2048
    node_id = online_provider_node["id"]

    # 1. Create file record and reserve quota
    file_rec = create_file_record(
        owner_id=consumer_user["id"],
        filename="my_file.txt",
        file_size=size,
        encrypted_size=size + 16,
        iv_hex=iv,
        sha256_hash=sha,
    )
    assert reserve_provider_quota(node_id, size + 16) is True

    # 2. Create file chunk
    chunk_id = "chunk_abc123_test"
    chunk = create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=node_id,
        chunk_size=size + 16,
    )
    assert chunk["chunk_id"] == chunk_id
    assert chunk["status"] == "PENDING"

    # 3. Create chunk job
    job = create_chunk_job(
        provider_node_id=node_id,
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
        timeout_seconds=60,
    )
    assert job["status"] == "QUEUED"

    # 4. Poll jobs: provider node fetches its queued jobs (transitions to PROCESSING)
    polled = poll_provider_jobs(provider_node_id=node_id, limit=5)
    assert len(polled) == 1
    assert polled[0]["id"] == job["id"]

    # Re-checking job shows status is PROCESSING
    in_flight = get_chunk_job_by_id(job["id"])
    assert in_flight["status"] == "PROCESSING"

    # Polling again immediately returns empty (no duplicate dispatch)
    assert len(poll_provider_jobs(provider_node_id=node_id, limit=5)) == 0

    # 5. Complete job: converts quota, marks chunk STORED, and marks file STORED!
    assert complete_chunk_job(job_id=job["id"], provider_node_id=node_id) is True

    completed_job = get_chunk_job_by_id(job["id"])
    assert completed_job["status"] == "COMPLETED"

    updated_chunk = get_chunk_by_id(chunk_id)
    assert updated_chunk["status"] == "STORED"

    updated_file = get_file_by_id(file_rec["id"])
    assert updated_file["status"] == "STORED"

    # Provider quota committed: used_bytes increased, reserved_bytes decreased
    node_after = get_provider_node_by_id(node_id)
    assert node_after["used_bytes"] == size + 16
    assert node_after["reserved_bytes"] == 0

    # 6. IDEMPOTENT: Calling complete again returns True without re-adding used_bytes
    assert complete_chunk_job(job_id=job["id"], provider_node_id=node_id) is True
    assert get_provider_node_by_id(node_id)["used_bytes"] == size + 16


def test_chunk_job_provider_isolation(consumer_user, online_provider_node):
    # Create a second provider
    p2 = create_user(
        email="provider_two@storeshare.io",
        password_hash=hash_password("ProviderSecure222!"),
        role="PROVIDER",
    )
    node2 = upsert_provider_node(
        provider_id=p2["id"],
        quota_bytes=50 * 1024 * 1024,
        storage_dir="C:\\node2-storage",
        status="ONLINE",
    )

    f = create_file_record(
        owner_id=consumer_user["id"],
        filename="isolated.txt",
        file_size=1000,
        encrypted_size=1016,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="f" * 64,
    )
    create_file_chunk(file_id=f["id"], chunk_id="chunk_iso_1", provider_node_id=online_provider_node["id"], chunk_size=1016)
    job = create_chunk_job(
        provider_node_id=online_provider_node["id"],
        chunk_id="chunk_iso_1",
        file_id=f["id"],
        job_type="STORE_CHUNK",
    )

    # Node 2 cannot poll Node 1's job
    assert len(poll_provider_jobs(provider_node_id=node2["id"])) == 0

    # Node 2 cannot complete Node 1's job
    assert complete_chunk_job(job_id=job["id"], provider_node_id=node2["id"]) is False
    assert get_chunk_job_by_id(job["id"])["status"] == "QUEUED"


def test_chunk_job_failure_releases_quota(consumer_user, online_provider_node):
    node_id = online_provider_node["id"]
    size = 4096

    f = create_file_record(
        owner_id=consumer_user["id"],
        filename="fail_test.txt",
        file_size=size,
        encrypted_size=size + 16,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="0" * 64,
    )
    reserve_provider_quota(node_id, size + 16)
    create_file_chunk(file_id=f["id"], chunk_id="chunk_fail_1", provider_node_id=node_id, chunk_size=size + 16)
    job = create_chunk_job(provider_node_id=node_id, chunk_id="chunk_fail_1", file_id=f["id"], job_type="STORE_CHUNK")

    # Fail job
    assert fail_chunk_job(job_id=job["id"], provider_node_id=node_id, error_message="Local disk error") is True

    # Statuses updated to FAILED
    assert get_chunk_job_by_id(job["id"])["status"] == "FAILED"
    assert get_chunk_by_id("chunk_fail_1")["status"] == "FAILED"
    assert get_file_by_id(f["id"])["status"] == "FAILED"

    # Reserved quota rolled back
    node = get_provider_node_by_id(node_id)
    assert node["reserved_bytes"] == 0
    assert node["used_bytes"] == 0


def test_expire_stale_jobs_rolls_back_quota(consumer_user, online_provider_node):
    node_id = online_provider_node["id"]
    size = 8192

    f = create_file_record(
        owner_id=consumer_user["id"],
        filename="stale.txt",
        file_size=size,
        encrypted_size=size + 16,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="1" * 64,
    )
    reserve_provider_quota(node_id, size + 16)
    create_file_chunk(file_id=f["id"], chunk_id="chunk_stale_1", provider_node_id=node_id, chunk_size=size + 16)

    # Create job with timeout of -10 seconds (already expired)
    job = create_chunk_job(
        provider_node_id=node_id,
        chunk_id="chunk_stale_1",
        file_id=f["id"],
        job_type="STORE_CHUNK",
        timeout_seconds=-10,
    )

    # Reap expired jobs
    reaped = expire_stale_jobs()
    assert reaped >= 1

    # Verify job marked TIMED_OUT and file marked FAILED
    stale_job = get_chunk_job_by_id(job["id"])
    assert stale_job["status"] == "TIMED_OUT"
    assert get_file_by_id(f["id"])["status"] == "FAILED"

    # Reserved quota safely released
    node = get_provider_node_by_id(node_id)
    assert node["reserved_bytes"] == 0
    assert node["used_bytes"] == 0
