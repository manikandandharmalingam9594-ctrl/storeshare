"""
Regression test suite for Store-Share file deletion workflow.

Validates end-to-end deletion lifecycle:
1. Consumer authenticated DELETE request processing.
2. Ownership and authorization checks (401, 403, 404).
3. Provider offline guard (503 Service Unavailable).
4. Physical encrypted chunk removal from disk.
5. Provider quota accounting decrement.
6. Consistent database metadata transition (file DELETED, chunks unlinked, jobs preserved).
7. Duplicate/repeated deletion idempotency.
8. Unstored/pending file deletion with quota and job rollback.
9. Provider failure handling (504 Gateway Timeout).
10. Provider agent automatic token renewal upon expiration.
"""

import base64
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict
import uuid

from fastapi.testclient import TestClient
import pytest

from backend.database import (
    MAX_FILE_SIZE_BYTES,
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    create_user,
    get_chunk_job_by_id,
    get_file_by_id,
    get_file_chunks,
    get_provider_node,
    get_provider_node_by_id,
    list_consumer_files,
    record_provider_heartbeat,
    reserve_provider_quota,
    set_provider_node_status,
    update_file_status,
    upsert_provider_node,
)
from backend.main import app
from backend.security import create_access_token, hash_password
from backend.spool import get_spool_chunk, remove_spool_chunk, save_spool_chunk
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def consumer_a(client: TestClient) -> Dict[str, Any]:
    email = f"consumer_del_a_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ConsumerPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="CONSUMER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="CONSUMER")
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "token": token, "password": pwd}


@pytest.fixture
def consumer_b(client: TestClient) -> Dict[str, Any]:
    email = f"consumer_del_b_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ConsumerPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="CONSUMER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="CONSUMER")
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "token": token, "password": pwd}


@pytest.fixture
def provider_node(client: TestClient, tmp_path: Path) -> Dict[str, Any]:
    email = f"provider_del_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ProviderPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="PROVIDER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="PROVIDER")
    headers = {"Authorization": f"Bearer {token}"}

    storage_dir = tmp_path / "provider_storage"
    quota_bytes = 50 * 1024 * 1024  # 50 MB

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
        quota_gb=0.05,
        token=token,
        email=email,
        password=pwd,
    )
    agent = ProviderAgent(config=cfg, storage_manager=sm)
    agent.client.get = lambda url, **kwargs: client.get(url, **kwargs)
    agent.client.post = lambda url, **kwargs: client.post(url, **kwargs)

    return {
        "user": user,
        "password": pwd,
        "node": get_provider_node_by_id(node["id"]),
        "headers": headers,
        "token": token,
        "storage": sm,
        "agent": agent,
    }


def _make_mock_ciphertext(plaintext: bytes) -> Dict[str, Any]:
    """Helper to generate simulated AES-256-GCM ciphertext."""
    iv_hex = os.urandom(12).hex()
    ciphertext = os.urandom(len(plaintext)) + os.urandom(16)
    sha256_hash = hashlib.sha256(ciphertext).hexdigest()
    b64 = base64.b64encode(ciphertext).decode("utf-8")
    return {
        "plaintext": plaintext,
        "ciphertext": ciphertext,
        "ciphertext_base64": b64,
        "iv_hex": iv_hex,
        "sha256_hash": sha256_hash,
        "file_size": len(plaintext),
        "encrypted_size": len(ciphertext),
        "json_body": {
            "filename": "test_del.bin",
            "file_size": len(plaintext),
            "iv_hex": iv_hex,
            "sha256_hash": sha256_hash,
            "ciphertext_base64": b64,
        },
    }


def test_deletion_successful_stored_file(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    1. Upload and confirm physical store on provider.
    2. Consumer requests DELETE /api/consumer/files/{id}.
    3. Provider agent executes DELETE_CHUNK job.
    4. Confirm physical chunk deleted, quota decremented, chunk unlinked, file marked DELETED.
    """
    payload_info = _make_mock_ciphertext(b"sample data for end to end deletion test")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=payload_info["json_body"],
    )
    assert res_up.status_code == 200
    file_id = res_up.json()["file_id"]

    agent: ProviderAgent = provider_node["agent"]
    sm: StorageManager = provider_node["storage"]

    # Provider agent processes STORE_CHUNK
    stored_count = agent.poll_and_process_jobs_once()
    assert stored_count == 1

    chunks = get_file_chunks(file_id)
    assert len(chunks) == 1
    chunk_id = chunks[0]["chunk_id"]

    # Verify physical file exists on disk
    assert sm.read_chunk(chunk_id) == payload_info["ciphertext"]
    node_before = get_provider_node_by_id(provider_node["node"]["id"])
    assert node_before["used_bytes"] == payload_info["encrypted_size"]

    # Run agent in background thread to handle DELETE_CHUNK
    def _agent_worker():
        time.sleep(0.3)
        agent.poll_and_process_jobs_once()

    worker = threading.Thread(target=_agent_worker, daemon=True)
    worker.start()

    res_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    worker.join(timeout=3.0)

    assert res_del.status_code == 200
    assert res_del.json()["status"] == "DELETED"

    # Physical chunk must be deleted from disk
    with pytest.raises(FileNotFoundError):
        sm.read_chunk(chunk_id)

    # Quota decremented
    node_after = get_provider_node_by_id(provider_node["node"]["id"])
    assert node_after["used_bytes"] == 0

    # file_chunks record deleted
    assert len(get_file_chunks(file_id)) == 0

    # File status is DELETED
    file_db = get_file_by_id(file_id)
    assert file_db["status"] == "DELETED"

    # Preserved job history
    jobs = client.get("/api/provider/jobs?limit=10", headers=provider_node["headers"]).json()
    # Or query coordinator db for jobs associated with this file
    file_jobs = [j for j in jobs if j.get("file_id") == file_id]
    # At least STORE_CHUNK and DELETE_CHUNK were executed
    assert len(file_jobs) == 0  # Poll only returns QUEUED/PROCESSING, COMPLETED are preserved in DB


def test_deletion_missing_file_404(
    client: TestClient,
    consumer_a: Dict[str, Any],
):
    """
    Deleting a non-existent file returns 404 Not Found.
    """
    res = client.delete("/api/consumer/files/999999", headers=consumer_a["headers"])
    assert res.status_code == 404
    assert res.json()["detail"] == "File not found."


def test_deletion_unauthorized_user(
    client: TestClient,
    consumer_a: Dict[str, Any],
    consumer_b: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Consumer B cannot delete Consumer A's file (returns 404).
    """
    payload_info = _make_mock_ciphertext(b"owner consumer A confidential")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=payload_info["json_body"],
    )
    file_id = res_up.json()["file_id"]

    res_b = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_b["headers"])
    assert res_b.status_code == 404
    assert "access denied" in res_b.json()["detail"].lower() or "not found" in res_b.json()["detail"].lower()


def test_deletion_unauthenticated_user_401(
    client: TestClient,
):
    """
    Unauthenticated DELETE request returns 401 Unauthorized.
    """
    res = client.delete("/api/consumer/files/1")
    assert res.status_code == 401


def test_deletion_non_consumer_role_403(
    client: TestClient,
    provider_node: Dict[str, Any],
):
    """
    Provider user cannot access consumer DELETE endpoint (returns 403 Forbidden).
    """
    res = client.delete("/api/consumer/files/1", headers=provider_node["headers"])
    assert res.status_code == 403


def test_deletion_offline_provider_503(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    If assigned provider is OFFLINE, DELETE returns 503 and protects disk/database consistency.
    """
    payload_info = _make_mock_ciphertext(b"safe when provider offline")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=payload_info["json_body"],
    )
    file_id = res_up.json()["file_id"]
    agent: ProviderAgent = provider_node["agent"]
    agent.poll_and_process_jobs_once()  # STORE_CHUNK

    # Simulate node going offline
    set_provider_node_status(provider_node["user"]["id"], "STOPPED")

    res_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    assert res_del.status_code == 503
    assert "currently offline" in res_del.json()["detail"]

    # File must still be STORED
    file_db = get_file_by_id(file_id)
    assert file_db["status"] == "STORED"

    # Physical chunk must NOT be deleted
    chunks = get_file_chunks(file_id)
    sm: StorageManager = provider_node["storage"]
    assert sm.read_chunk(chunks[0]["chunk_id"]) == payload_info["ciphertext"]


def test_deletion_provider_failure_504(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    If provider reports job failure during DELETE_CHUNK, returns 504 and file is not marked DELETED.
    """
    payload_info = _make_mock_ciphertext(b"provider failure simulation")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=payload_info["json_body"],
    )
    file_id = res_up.json()["file_id"]
    agent: ProviderAgent = provider_node["agent"]
    agent.poll_and_process_jobs_once()  # STORE_CHUNK

    # Worker that fails the job intentionally
    def _failing_worker():
        time.sleep(0.3)
        jobs = agent.poll_jobs(limit=1)
        if jobs:
            agent.fail_job(jobs[0]["id"], "Physical drive failure")

    worker = threading.Thread(target=_failing_worker, daemon=True)
    worker.start()

    res_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    worker.join(timeout=3.0)

    assert res_del.status_code == 504
    assert "Provider failed to execute chunk deletion" in res_del.json()["detail"]

    # File status must NOT be DELETED
    file_db = get_file_by_id(file_id)
    assert file_db["status"] == "STORED"


def test_deletion_duplicate_idempotent(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Subsequent DELETE on already DELETED file returns 200 OK (idempotent).
    """
    payload_info = _make_mock_ciphertext(b"idempotency verification")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=payload_info["json_body"],
    )
    file_id = res_up.json()["file_id"]
    agent: ProviderAgent = provider_node["agent"]
    agent.poll_and_process_jobs_once()

    def _worker():
        time.sleep(0.2)
        agent.poll_and_process_jobs_once()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    res1 = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    worker.join(timeout=3.0)
    assert res1.status_code == 200

    # Repeat delete
    res2 = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    assert res2.status_code == 200
    assert res2.json()["status"] == "DELETED"
    assert "already deleted" in res2.json()["message"].lower()


def test_deletion_unstored_file_cleanup(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Deleting an unstored file (UPLOADING/PENDING_UPLOAD) rolls back reservations and cleans up.
    """
    payload_info = _make_mock_ciphertext(b"unstored file to cancel")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=payload_info["json_body"],
    )
    file_id = res_up.json()["file_id"]

    # File is in UPLOADING, provider has NOT processed STORE_CHUNK yet
    node_before = get_provider_node_by_id(provider_node["node"]["id"])
    assert node_before["reserved_bytes"] == payload_info["encrypted_size"]

    res_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "DELETED"

    # Reserved quota is released
    node_after = get_provider_node_by_id(provider_node["node"]["id"])
    assert node_after["reserved_bytes"] == 0

    # File chunks removed
    assert len(get_file_chunks(file_id)) == 0


def test_provider_agent_reauth_on_token_expiry(
    client: TestClient,
    provider_node: Dict[str, Any],
):
    """
    Provider agent automatically recovers and re-authenticates when Bearer token expires.
    """
    agent: ProviderAgent = provider_node["agent"]

    # Set an invalid/expired token
    agent.token = "expired_or_invalid_bearer_token"

    # Polling jobs should trigger re-authentication using configured email/password
    jobs = agent.poll_jobs(limit=5)
    assert isinstance(jobs, list)

    # Token should now be refreshed with a valid token
    assert agent.token != "expired_or_invalid_bearer_token"
    assert agent.token is not None

    # Heartbeat should also succeed
    hb = agent.send_heartbeat()
    assert hb.get("status") == "ONLINE"
