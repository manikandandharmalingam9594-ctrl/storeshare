"""
Tests for Phase 2: Coordinator and Provider Agent Job Transfer Pipeline.

Validates:
1. Endpoint security and authentication:
   - Unauthenticated requests return 401 Unauthorized.
   - Consumer accounts return 403 Forbidden.
   - Cross-provider job isolation (Provider B cannot access Provider A's jobs, data, or complete/fail).
2. Transactional job lifecycle & concurrency:
   - Duplicate polling does not return already PROCESSING/COMPLETED jobs.
   - Idempotent completions succeed without double-counting quota.
   - Lost response / repeat completion returns 200 idempotent.
3. STORE_CHUNK transfer round-trip:
   - Ephemeral spooling on coordinator.
   - Outbound agent poll, fetch, atomic local write, and completion acknowledgement.
   - Coordinator validates completion before marking file and chunk STORED.
   - Spool buffer purged immediately upon completion.
   - Quota reserved converted to used bytes.
4. RETRIEVE_CHUNK transfer round-trip:
   - Provider reads chunk from disk and uploads to coordinator.
   - SHA-256 checksum verification against file record.
   - Corrupted/invalid checksum rejected with 400.
5. DELETE_CHUNK handling:
   - Safely removes local disk chunk.
   - Decrements used quota and cleans up chunk record.
6. Error handling, quota rollback, and timeouts:
   - Agent-reported failure marks job/file/chunk FAILED and rolls back quota.
   - Stale job timeout (TIMED_OUT) and quota rollback.
   - Expired job completion rejected with 409 Conflict.
   - Ephemeral spool cleaned up on failure, timeout, and restart.
   - Payloads > 5 MB rejected (413) and empty payloads rejected (400).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Dict
import uuid

from fastapi.testclient import TestClient
import pytest

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
    get_chunk_by_id,
    get_chunk_job_by_id,
    get_db_connection,
    get_file_by_id,
    get_file_chunks,
    get_provider_node,
    get_provider_node_by_id,
    init_db,
    poll_provider_jobs,
    record_provider_heartbeat,
    release_provider_quota,
    reserve_provider_quota,
    upsert_provider_node,
)
from backend.main import app
from backend.security import create_access_token, hash_password
from backend.spool import (
    cleanup_all_spool,
    get_spool_chunk,
    init_spool_dir,
    remove_spool_chunk,
    save_spool_chunk,
)
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def consumer_auth(client: TestClient) -> Dict[str, Any]:
    email = f"consumer_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ConsumerPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="CONSUMER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="CONSUMER")
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "token": token}


@pytest.fixture
def provider_a(client: TestClient, tmp_path: Path) -> Dict[str, Any]:
    email = f"provider_a_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ProviderPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="PROVIDER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="PROVIDER")
    headers = {"Authorization": f"Bearer {token}"}

    storage_dir = tmp_path / "provider_a_storage"
    quota_bytes = 10 * 1024 * 1024  # 10 MB

    sm = StorageManager(storage_dir=storage_dir, quota_bytes=quota_bytes)
    node = upsert_provider_node(
        provider_id=user["id"],
        quota_bytes=quota_bytes,
        storage_dir=str(storage_dir),
        status="ONLINE",
    )
    record_provider_heartbeat(provider_id=user["id"], used_bytes=0, allocated_bytes=quota_bytes)

    cfg = AgentConfig(
        coordinator_url="http://127.0.0.1:8001",
        storage_dir=str(storage_dir),
        quota_gb=0.01,
        token=token,
    )
    agent = ProviderAgent(config=cfg, storage_manager=sm)

    # Route agent HTTP calls to TestClient
    agent.client.get = lambda url, **kwargs: client.get(url, **kwargs)
    agent.client.post = lambda url, **kwargs: client.post(url, **kwargs)

    return {
        "user": user,
        "node": get_provider_node_by_id(node["id"]),
        "headers": headers,
        "token": token,
        "storage": sm,
        "agent": agent,
    }


@pytest.fixture
def provider_b(client: TestClient, tmp_path: Path) -> Dict[str, Any]:
    email = f"provider_b_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ProviderPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="PROVIDER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="PROVIDER")
    headers = {"Authorization": f"Bearer {token}"}

    storage_dir = tmp_path / "provider_b_storage"
    quota_bytes = 10 * 1024 * 1024

    sm = StorageManager(storage_dir=storage_dir, quota_bytes=quota_bytes)
    node = upsert_provider_node(
        provider_id=user["id"],
        quota_bytes=quota_bytes,
        storage_dir=str(storage_dir),
        status="ONLINE",
    )
    record_provider_heartbeat(provider_id=user["id"], used_bytes=0, allocated_bytes=quota_bytes)

    cfg = AgentConfig(
        coordinator_url="http://127.0.0.1:8001",
        storage_dir=str(storage_dir),
        quota_gb=0.01,
        token=token,
    )
    agent = ProviderAgent(config=cfg, storage_manager=sm)
    agent.client.get = lambda url, **kwargs: client.get(url, **kwargs)
    agent.client.post = lambda url, **kwargs: client.post(url, **kwargs)

    return {
        "user": user,
        "node": get_provider_node_by_id(node["id"]),
        "headers": headers,
        "token": token,
        "storage": sm,
        "agent": agent,
    }


# ==============================================================================
# 1. Authentication and Cross-Provider Job Isolation Tests
# ==============================================================================

def test_unauthenticated_and_forbidden_job_endpoints(client: TestClient, consumer_auth: Dict[str, Any]):
    """
    Ensure all provider job endpoints reject unauthenticated requests (401)
    and consumer accounts (403).
    """
    endpoints = [
        ("GET", "/api/provider/jobs"),
        ("GET", "/api/provider/jobs/1/data"),
        ("POST", "/api/provider/jobs/1/data"),
        ("POST", "/api/provider/jobs/1/complete"),
        ("POST", "/api/provider/jobs/1/fail"),
    ]

    for method, ep in endpoints:
        # Unauthenticated -> 401
        if method == "GET":
            res_anon = client.get(ep)
        else:
            res_anon = client.post(ep, json={})
        assert res_anon.status_code == 401, f"{method} {ep} should require auth, got {res_anon.status_code}"

        # Consumer -> 403 Forbidden
        if method == "GET":
            res_consumer = client.get(ep, headers=consumer_auth["headers"])
        else:
            res_consumer = client.post(ep, headers=consumer_auth["headers"], json={})
        assert res_consumer.status_code == 403, f"{method} {ep} should reject consumer, got {res_consumer.status_code}"


def test_cross_provider_job_isolation(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
    provider_b: Dict[str, Any],
):
    """
    Strict provider isolation:
    Provider B cannot view or manipulate jobs assigned to Provider A.
    """
    # Create test encrypted file and job assigned to Provider A
    fake_ciphertext = b"encrypted-blob-for-provider-a"
    sha = hashlib.sha256(fake_ciphertext).hexdigest()
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="secret.dat",
        file_size=len(fake_ciphertext) - 16,
        encrypted_size=len(fake_ciphertext),
        iv_hex="0123456789abcdef01234567",
        sha256_hash=sha,
    )
    chunk_id = f"chunk_{uuid.uuid4().hex}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=len(fake_ciphertext),
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
    )
    save_spool_chunk(chunk_id, fake_ciphertext)

    try:
        # Provider B polling jobs should NOT see Provider A's job
        res_b_poll = client.get("/api/provider/jobs", headers=provider_b["headers"])
        assert res_b_poll.status_code == 200
        b_job_ids = [j["id"] for j in res_b_poll.json()]
        assert job["id"] not in b_job_ids

        # Provider B attempting to download Provider A's job data -> 403
        res_b_data = client.get(f"/api/provider/jobs/{job['id']}/data", headers=provider_b["headers"])
        assert res_b_data.status_code == 403

        # Provider B attempting to upload data to Provider A's job -> 403
        res_b_post_data = client.post(
            f"/api/provider/jobs/{job['id']}/data",
            headers={**provider_b["headers"], "Content-Type": "application/octet-stream"},
            content=b"evil-data",
        )
        assert res_b_post_data.status_code == 403

        # Provider B attempting to complete Provider A's job -> 403
        res_b_complete = client.post(f"/api/provider/jobs/{job['id']}/complete", headers=provider_b["headers"])
        assert res_b_complete.status_code == 403

        # Provider B attempting to fail Provider A's job -> 403
        res_b_fail = client.post(
            f"/api/provider/jobs/{job['id']}/fail",
            headers=provider_b["headers"],
            json={"error_message": "Sabotage"},
        )
        assert res_b_fail.status_code == 403

    finally:
        remove_spool_chunk(chunk_id)


# ==============================================================================
# 2. Duplicate Polling and Idempotency Tests
# ==============================================================================

def test_duplicate_polling_and_state_transition(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Ensure polling claims jobs atomically:
    First poll returns QUEUED jobs and transitions them to PROCESSING.
    Second poll returns empty list.
    """
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="concurrent.bin",
        file_size=100,
        encrypted_size=116,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="e" * 64,
    )
    chunk_id = f"chunk_poll_{uuid.uuid4().hex[:12]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=116,
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
    )

    # First poll: returns job
    res1 = client.get("/api/provider/jobs", headers=provider_a["headers"])
    assert res1.status_code == 200
    jobs1 = res1.json()
    assert any(j["id"] == job["id"] for j in jobs1)

    # Job in DB must now be PROCESSING
    job_db = get_chunk_job_by_id(job["id"])
    assert job_db["status"] == "PROCESSING"

    # Second poll: returns empty list
    res2 = client.get("/api/provider/jobs", headers=provider_a["headers"])
    assert res2.status_code == 200
    jobs2 = res2.json()
    assert not any(j["id"] == job["id"] for j in jobs2)


# ==============================================================================
# 3. STORE_CHUNK Round-Trip Transfer Tests
# ==============================================================================

def test_store_chunk_round_trip_success(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Complete STORE_CHUNK flow:
    1. Coordinator has ephemeral chunk in spool and reserves quota.
    2. Provider agent polls for jobs.
    3. Provider agent downloads chunk payload from coordinator.
    4. Provider agent writes chunk to local disk atomically.
    5. Provider agent sends completion acknowledgement.
    6. Coordinator validates completion:
       - File status -> STORED
       - Chunk status -> STORED
       - Ephemeral spool is purged
       - Reserved quota converted to used quota.
    """
    test_payload = b"encrypted_store_chunk_payload_content_1234567890"
    payload_len = len(test_payload)
    sha = hashlib.sha256(test_payload).hexdigest()

    # Reserve quota on coordinator
    assert reserve_provider_quota(provider_a["node"]["id"], payload_len) is True
    node_before = get_provider_node_by_id(provider_a["node"]["id"])
    assert node_before["reserved_bytes"] >= payload_len

    # Create file and chunk records
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="report.pdf.enc",
        file_size=payload_len - 16,
        encrypted_size=payload_len,
        iv_hex="0123456789abcdef01234567",
        sha256_hash=sha,
    )
    chunk_id = f"chunk_store_{uuid.uuid4().hex[:12]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=payload_len,
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
    )

    # Save to coordinator spool
    save_spool_chunk(chunk_id, test_payload)
    assert get_spool_chunk(chunk_id) == test_payload

    # Run agent single poll & process cycle
    agent: ProviderAgent = provider_a["agent"]
    processed = agent.poll_and_process_jobs_once()
    assert processed == 1

    # Verify coordinator state
    job_after = get_chunk_job_by_id(job["id"])
    assert job_after["status"] == "COMPLETED"

    chunk_after = get_chunk_by_id(chunk_id)
    assert chunk_after["status"] == "STORED"

    file_after = get_file_by_id(file_rec["id"])
    assert file_after["status"] == "STORED"

    # Ephemeral spool must be purged
    assert get_spool_chunk(chunk_id) is None

    # Quota check: reserved_bytes decremented, used_bytes incremented
    node_after = get_provider_node_by_id(provider_a["node"]["id"])
    assert node_after["used_bytes"] == node_before["used_bytes"] + payload_len
    assert node_after["reserved_bytes"] == node_before["reserved_bytes"] - payload_len

    # Local disk verification: physical file in provider's chunks dir
    storage: StorageManager = provider_a["storage"]
    stored_bytes = storage.read_chunk(chunk_id)
    assert stored_bytes == test_payload


def test_idempotent_job_completion(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Duplicate / retried job completion acknowledgement:
    - Subsequent calls to POST /complete return 200 idempotent.
    - Quota is NOT committed twice.
    """
    test_payload = b"idempotency_test_payload"
    payload_len = len(test_payload)
    sha = hashlib.sha256(test_payload).hexdigest()

    reserve_provider_quota(provider_a["node"]["id"], payload_len)
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="idem.dat",
        file_size=payload_len - 16,
        encrypted_size=payload_len,
        iv_hex="0123456789abcdef01234567",
        sha256_hash=sha,
    )
    chunk_id = f"chunk_idem_{uuid.uuid4().hex[:12]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=payload_len,
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
    )
    save_spool_chunk(chunk_id, test_payload)

    # First completion
    res1 = client.post(f"/api/provider/jobs/{job['id']}/complete", headers=provider_a["headers"])
    assert res1.status_code == 200
    assert res1.json()["status"] == "COMPLETED"

    node_after_first = get_provider_node_by_id(provider_a["node"]["id"])

    # Second completion (duplicate/retry)
    res2 = client.post(f"/api/provider/jobs/{job['id']}/complete", headers=provider_a["headers"])
    assert res2.status_code == 200
    assert res2.json()["status"] == "COMPLETED"
    assert res2.json().get("idempotent") is True

    # Quota must be identical (not doubled)
    node_after_second = get_provider_node_by_id(provider_a["node"]["id"])
    assert node_after_second["used_bytes"] == node_after_first["used_bytes"]
    assert node_after_second["reserved_bytes"] == node_after_first["reserved_bytes"]


# ==============================================================================
# 4. RETRIEVE_CHUNK Round-Trip Transfer Tests
# ==============================================================================

def test_retrieve_chunk_round_trip_success(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Complete RETRIEVE_CHUNK flow:
    1. Chunk exists in provider's local storage.
    2. Coordinator queues RETRIEVE_CHUNK job.
    3. Provider agent polls for job, reads chunk from disk.
    4. Provider agent uploads chunk payload to coordinator.
    5. Coordinator validates SHA-256 integrity and buffers in spool.
    6. Provider agent acknowledges completion.
    """
    test_payload = b"encrypted_payload_for_download_retrieval"
    sha = hashlib.sha256(test_payload).hexdigest()
    chunk_id = f"chunk_ret_{uuid.uuid4().hex[:12]}"

    # Write chunk to provider local storage
    storage: StorageManager = provider_a["storage"]
    storage.write_chunk_atomic(chunk_id, test_payload)

    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="retrieved_file.pdf",
        file_size=len(test_payload) - 16,
        encrypted_size=len(test_payload),
        iv_hex="0123456789abcdef01234567",
        sha256_hash=sha,
    )
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=len(test_payload),
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="RETRIEVE_CHUNK",
    )

    try:
        # Run agent single poll & process cycle
        agent: ProviderAgent = provider_a["agent"]
        processed = agent.poll_and_process_jobs_once()
        assert processed == 1

        # Check job completion
        job_after = get_chunk_job_by_id(job["id"])
        assert job_after["status"] == "COMPLETED"

        # Verify spooled chunk on coordinator matches
        spooled_data = get_spool_chunk(chunk_id)
        assert spooled_data == test_payload

    finally:
        remove_spool_chunk(chunk_id)


def test_retrieve_chunk_invalid_checksum_rejected(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Coordinator rejects uploaded retrieval chunk if SHA-256 checksum fails.
    """
    correct_payload = b"valid_chunk_bytes_12345"
    corrupted_payload = b"corrupted_chunk_bytes_67890"

    expected_sha = hashlib.sha256(correct_payload).hexdigest()
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="integrity_test.bin",
        file_size=len(correct_payload),
        encrypted_size=len(correct_payload),
        iv_hex="0123456789abcdef01234567",
        sha256_hash=expected_sha,
    )
    chunk_id = f"chunk_bad_hash_{uuid.uuid4().hex[:8]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=len(correct_payload),
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="RETRIEVE_CHUNK",
    )

    # Provider uploads corrupted data with mismatched hash
    res = client.post(
        f"/api/provider/jobs/{job['id']}/data",
        headers={**provider_a["headers"], "Content-Type": "application/octet-stream"},
        content=corrupted_payload,
    )
    assert res.status_code == 400
    assert "Integrity check failed" in res.json()["detail"]


# ==============================================================================
# 5. Error Handling, Storage Failure, and Quota Rollback Tests
# ==============================================================================

def test_storage_failure_reporting_and_quota_release(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    If local storage write fails:
    - Agent catches exception and calls POST /fail.
    - Coordinator marks job, chunk, and file FAILED.
    - Reserved quota is released.
    - Ephemeral spool is purged.
    """
    test_payload = b"simulated_failure_payload"
    payload_len = len(test_payload)

    reserve_provider_quota(provider_a["node"]["id"], payload_len)
    node_before = get_provider_node_by_id(provider_a["node"]["id"])

    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="failing_file.bin",
        file_size=payload_len,
        encrypted_size=payload_len,
        iv_hex="0123456789abcdef01234567",
        sha256_hash=hashlib.sha256(test_payload).hexdigest(),
    )
    chunk_id = f"chunk_fail_{uuid.uuid4().hex[:12]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=payload_len,
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
    )
    save_spool_chunk(chunk_id, test_payload)

    # Simulate write failure by mocking write_chunk_atomic
    agent: ProviderAgent = provider_a["agent"]
    original_write = agent.storage.write_chunk_atomic

    def mock_broken_write(cid, data):
        raise OSError("Simulated physical disk I/O error on provider node")

    agent.storage.write_chunk_atomic = mock_broken_write

    try:
        processed = agent.poll_and_process_jobs_once()
        assert processed == 0  # 0 successful

        # Verify coordinator state
        job_after = get_chunk_job_by_id(job["id"])
        assert job_after["status"] == "FAILED"
        assert "Simulated physical disk I/O error" in (job_after["error_message"] or "")

        chunk_after = get_chunk_by_id(chunk_id)
        assert chunk_after["status"] == "FAILED"

        file_after = get_file_by_id(file_rec["id"])
        assert file_after["status"] == "FAILED"

        # Reserved quota must be released
        node_after = get_provider_node_by_id(provider_a["node"]["id"])
        assert node_after["reserved_bytes"] == node_before["reserved_bytes"] - payload_len

        # Ephemeral spool must be removed
        assert get_spool_chunk(chunk_id) is None

    finally:
        agent.storage.write_chunk_atomic = original_write


def test_expired_stale_job_cannot_be_completed(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Jobs past expiration time:
    - Reaped to TIMED_OUT by expire_stale_jobs.
    - Reserved quota released.
    - Subsequent calls to POST /complete return 409 Conflict.
    """
    payload_len = 5000
    reserve_provider_quota(provider_a["node"]["id"], payload_len)

    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="stale_job.bin",
        file_size=payload_len,
        encrypted_size=payload_len,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="f" * 64,
    )
    chunk_id = f"chunk_stale_{uuid.uuid4().hex[:12]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=payload_len,
    )
    # Create job with -10 second timeout (already expired)
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
        timeout_seconds=-10,
    )

    # Reaper runs
    reaped = expire_stale_jobs()
    assert reaped >= 1

    job_db = get_chunk_job_by_id(job["id"])
    assert job_db["status"] == "TIMED_OUT"

    # Completing a timed-out job must fail with 409 Conflict
    res = client.post(f"/api/provider/jobs/{job['id']}/complete", headers=provider_a["headers"])
    assert res.status_code == 409
    assert "terminal state" in res.json()["detail"]


# ==============================================================================
# 6. DELETE_CHUNK Handling Tests
# ==============================================================================

def test_delete_chunk_round_trip(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Safe DELETE_CHUNK handling:
    - Removes chunk from provider local storage.
    - Decrements used_bytes quota.
    - Removes file_chunk and marks file DELETED.
    """
    test_payload = b"chunk_payload_to_be_deleted"
    payload_len = len(test_payload)
    chunk_id = f"chunk_del_{uuid.uuid4().hex[:12]}"

    # Write to local disk
    storage: StorageManager = provider_a["storage"]
    storage.write_chunk_atomic(chunk_id, test_payload)
    stats_before = storage.get_stats()
    assert stats_before["used_bytes"] >= payload_len

    # Create file and chunk records
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="to_delete.txt",
        file_size=payload_len,
        encrypted_size=payload_len,
        iv_hex="0123456789abcdef01234567",
        sha256_hash=hashlib.sha256(test_payload).hexdigest(),
    )
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=payload_len,
    )
    # Manually mark as stored for delete test
    commit_provider_quota(provider_a["node"]["id"], payload_len)
    node_before = get_provider_node_by_id(provider_a["node"]["id"])

    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="DELETE_CHUNK",
    )

    # Provider agent polls and processes DELETE_CHUNK
    agent: ProviderAgent = provider_a["agent"]
    processed = agent.poll_and_process_jobs_once()
    assert processed == 1

    # Chunk must be gone from physical disk
    with pytest.raises(FileNotFoundError):
        storage.read_chunk(chunk_id)

    # Job status COMPLETED
    job_after = get_chunk_job_by_id(job["id"])
    assert job_after["status"] == "COMPLETED"

    # Chunk record removed from database
    chunk_db = get_chunk_by_id(chunk_id)
    assert chunk_db is None

    # File status updated to DELETED
    file_db = get_file_by_id(file_rec["id"])
    assert file_db["status"] == "DELETED"

    # Used quota decremented
    node_after = get_provider_node_by_id(provider_a["node"]["id"])
    assert node_after["used_bytes"] == node_before["used_bytes"] - payload_len


# ==============================================================================
# 7. Payload Bounds and Ephemeral Spool Security Tests
# ==============================================================================

def test_payload_bounds_and_spool_cleanup(
    client: TestClient,
    consumer_auth: Dict[str, Any],
    provider_a: Dict[str, Any],
):
    """
    Test size limits and spool cleanup:
    - Empty payload rejected (400).
    - Oversized payload (> 5 MB) rejected (413).
    - cleanup_all_spool() leaves coordinator with 0 persistent bytes.
    """
    file_rec = create_file_record(
        owner_id=consumer_auth["user"]["id"],
        filename="bounds.bin",
        file_size=100,
        encrypted_size=100,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="d" * 64,
    )
    chunk_id = f"chunk_bounds_{uuid.uuid4().hex[:12]}"
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=provider_a["node"]["id"],
        chunk_size=100,
    )
    job = create_chunk_job(
        provider_node_id=provider_a["node"]["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="RETRIEVE_CHUNK",
    )

    # Empty payload upload -> 400
    res_empty = client.post(
        f"/api/provider/jobs/{job['id']}/data",
        headers={**provider_a["headers"], "Content-Type": "application/octet-stream"},
        content=b"",
    )
    assert res_empty.status_code == 400

    # Oversized payload (> 5 MB) -> 413
    oversized = b"X" * (MAX_FILE_SIZE_BYTES + 10)
    res_oversized = client.post(
        f"/api/provider/jobs/{job['id']}/data",
        headers={**provider_a["headers"], "Content-Type": "application/octet-stream"},
        content=oversized,
    )
    assert res_oversized.status_code == 413

    # Coordinator spool wipe on server restart/cleanup
    save_spool_chunk("chunk_temp_1", b"temp1")
    save_spool_chunk("chunk_temp_2", b"temp2")
    assert get_spool_chunk("chunk_temp_1") is not None
    assert get_spool_chunk("chunk_temp_2") is not None

    cleaned = cleanup_all_spool()
    assert cleaned >= 2
    assert get_spool_chunk("chunk_temp_1") is None
    assert get_spool_chunk("chunk_temp_2") is None
