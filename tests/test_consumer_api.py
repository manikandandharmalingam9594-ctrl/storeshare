"""
Tests for Phase 3: Consumer Upload, Download, and File Management APIs.

Validates:
1. Authentication & Role Enforcement:
   - Anonymous access -> 401 Unauthorized.
   - Provider accounts accessing consumer endpoints -> 403 Forbidden.
2. Ownership Isolation:
   - Consumer B cannot list, inspect status, download, or delete Consumer A's files.
3. Cryptographic & Size Validation:
   - Enforces <= 5 MB plaintext limit (413).
   - Enforces exact AES-256-GCM ciphertext size (plaintext + 16 bytes tag).
   - Validates 12-byte (24-hex) IV and rejects insecure all-zero IV.
   - Validates SHA-256 checksum integrity against ciphertext.
   - Rejects unencrypted raw ASCII plaintext.
4. Provider Selection & Quota:
   - Returns 503 Service Unavailable when no eligible online provider exists.
   - Successfully reserves quota and queues STORE_CHUNK.
5. End-to-End File Lifecycle:
   - Upload returns status 'UPLOADING' (never prematurely reported STORED).
   - Agent confirms storage -> status transitions to 'STORED'.
   - Download triggers RETRIEVE_CHUNK, delivers ciphertext with IV headers.
   - Server never decrypts data or stores decryption keys.
   - Safe deletion: coordinates physical chunk deletion on provider, verifies quota release.
   - Idempotent delete returns success.
"""

import asyncio
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
    email = f"consumer_a_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ConsumerPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="CONSUMER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="CONSUMER")
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "token": token}


@pytest.fixture
def consumer_b(client: TestClient) -> Dict[str, Any]:
    email = f"consumer_b_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "ConsumerPass123!"
    user = create_user(email=email, password_hash=hash_password(pwd), role="CONSUMER")
    token, _ = create_access_token(user_id=user["id"], email=email, role="CONSUMER")
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "token": token}


@pytest.fixture
def provider_node(client: TestClient, tmp_path: Path) -> Dict[str, Any]:
    email = f"provider_p3_{uuid.uuid4().hex[:8]}@example.com"
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


def _make_mock_ciphertext(plaintext: bytes) -> Dict[str, Any]:
    """Helper to generate simulated AES-256-GCM ciphertext (plaintext + 16 random tag bytes)."""
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
            "filename": "test_file.bin",
            "file_size": len(plaintext),
            "iv_hex": iv_hex,
            "sha256_hash": sha256_hash,
            "ciphertext_base64": b64,
        },
    }


# ==============================================================================
# 1. Authentication and Ownership Isolation Tests
# ==============================================================================

def test_unauthenticated_and_provider_consumer_endpoints(
    client: TestClient,
    provider_node: Dict[str, Any],
):
    """
    Ensure all /api/consumer/files endpoints reject anonymous access (401)
    and reject PROVIDER accounts (403).
    """
    endpoints = [
        ("POST", "/api/consumer/files/upload", {}),
        ("GET", "/api/consumer/files", None),
        ("GET", "/api/consumer/files/1/status", None),
        ("GET", "/api/consumer/files/1/download", None),
        ("DELETE", "/api/consumer/files/1", None),
    ]

    for method, ep, payload in endpoints:
        # Unauthenticated -> 401
        if method == "GET":
            r_anon = client.get(ep)
        elif method == "DELETE":
            r_anon = client.delete(ep)
        else:
            r_anon = client.post(ep, json=payload)
        assert r_anon.status_code == 401, f"{method} {ep} should be 401, got {r_anon.status_code}"

        # Provider user -> 403 Forbidden
        if method == "GET":
            r_prov = client.get(ep, headers=provider_node["headers"])
        elif method == "DELETE":
            r_prov = client.delete(ep, headers=provider_node["headers"])
        else:
            r_prov = client.post(ep, headers=provider_node["headers"], json=payload)
        assert r_prov.status_code == 403, f"{method} {ep} should reject provider with 403, got {r_prov.status_code}"


def test_consumer_ownership_isolation(
    client: TestClient,
    consumer_a: Dict[str, Any],
    consumer_b: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Verify Consumer B cannot list, inspect status, download, or delete Consumer A's files.
    """
    payload_info = _make_mock_ciphertext(b"confidential data for user A")
    res_upload = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "user_a_file.pdf"},
    )
    assert res_upload.status_code == 200
    file_id = res_upload.json()["file_id"]

    # Consumer B listing files must NOT include Consumer A's file
    res_b_list = client.get("/api/consumer/files", headers=consumer_b["headers"])
    assert res_b_list.status_code == 200
    b_file_ids = [f["file_id"] for f in res_b_list.json()]
    assert file_id not in b_file_ids

    # Consumer B status check -> 404
    res_b_status = client.get(f"/api/consumer/files/{file_id}/status", headers=consumer_b["headers"])
    assert res_b_status.status_code == 404

    # Consumer B download -> 404
    res_b_dl = client.get(f"/api/consumer/files/{file_id}/download", headers=consumer_b["headers"])
    assert res_b_dl.status_code == 404

    # Consumer B delete -> 404
    res_b_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_b["headers"])
    assert res_b_del.status_code == 404


# ==============================================================================
# 2. Cryptographic and Size Parameter Validation Tests
# ==============================================================================

def test_upload_validation_failures(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Verify upload validation rejects:
    - Plaintext size 0 or negative (400)
    - Plaintext size > 5 MB (413)
    - Ciphertext size mismatch (400)
    - Invalid IV (400)
    - All-zero IV (400)
    - SHA-256 mismatch (400)
    - Unencrypted raw ASCII plaintext (400)
    """
    valid = _make_mock_ciphertext(b"sample data")

    # 1. Plaintext size 0
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**valid["json_body"], "filename": "zero.bin", "file_size": 0},
    )
    assert r.status_code in (400, 422)

    # 2. Plaintext size > 5 MB
    oversized = MAX_FILE_SIZE_BYTES + 1
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**valid["json_body"], "filename": "big.bin", "file_size": oversized},
    )
    assert r.status_code == 413

    # 3. Ciphertext length mismatch (expected file_size + 16)
    bad_cipher = base64.b64encode(b"short").decode("utf-8")
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={
            "filename": "mismatch.bin",
            "file_size": 100,
            "iv_hex": "0123456789abcdef01234567",
            "sha256_hash": hashlib.sha256(b"short").hexdigest(),
            "ciphertext_base64": bad_cipher,
        },
    )
    assert r.status_code == 400
    assert "Invalid ciphertext payload length" in r.json()["detail"]

    # 4. Invalid IV length
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**valid["json_body"], "filename": "bad_iv.bin", "iv_hex": "1234"},
    )
    assert r.status_code in (400, 422)

    # 5. Insecure all-zero IV
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**valid["json_body"], "filename": "zero_iv.bin", "iv_hex": "0" * 24},
    )
    assert r.status_code in (400, 422)

    # 6. SHA-256 mismatch
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**valid["json_body"], "filename": "bad_sha.bin", "sha256_hash": "a" * 64},
    )
    assert r.status_code == 400
    assert "integrity check failed" in r.json()["detail"]

    # 7. Unencrypted raw ASCII plaintext heuristic rejection
    plaintext_string = b"This is clearly unencrypted plain English text with exactly 16 extra bytes!!!!"
    plain_b64 = base64.b64encode(plaintext_string).decode("utf-8")
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={
            "filename": "unencrypted.txt",
            "file_size": len(plaintext_string) - 16,
            "iv_hex": "0123456789abcdef01234567",
            "sha256_hash": hashlib.sha256(plaintext_string).hexdigest(),
            "ciphertext_base64": plain_b64,
        },
    )
    assert r.status_code == 400
    assert "Unencrypted plaintext rejected" in r.json()["detail"]


def test_upload_no_eligible_provider(
    client: TestClient,
    consumer_a: Dict[str, Any],
):
    """
    When no provider node is configured or online, upload returns 503 Service Unavailable.
    """
    valid = _make_mock_ciphertext(b"hello world")
    r = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json=valid["json_body"],
    )
    assert r.status_code == 503
    assert "No online storage provider node" in r.json()["detail"]


# ==============================================================================
# 3. Upload & End-to-End Download Flow Tests
# ==============================================================================

def test_upload_and_status_pending_until_stored(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Upload initially reports file status as UPLOADING (never prematurely STORED).
    Once provider agent polls, writes, and acknowledges, file transitions to STORED.
    """
    payload_info = _make_mock_ciphertext(b"pending to stored test payload")
    res_upload = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "lifecycle.bin"},
    )
    assert res_upload.status_code == 200
    upload_data = res_upload.json()
    file_id = upload_data["file_id"]
    assert upload_data["status"] == "UPLOADING"

    # Status before provider agent processes job
    res_status = client.get(f"/api/consumer/files/{file_id}/status", headers=consumer_a["headers"])
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "UPLOADING"
    assert res_status.json()["is_stored"] is False

    # Provider agent polls and executes STORE_CHUNK
    agent: ProviderAgent = provider_node["agent"]
    processed = agent.poll_and_process_jobs_once()
    assert processed == 1

    # Status after provider agent acknowledges
    res_status_after = client.get(f"/api/consumer/files/{file_id}/status", headers=consumer_a["headers"])
    assert res_status_after.status_code == 200
    assert res_status_after.json()["status"] == "STORED"
    assert res_status_after.json()["is_stored"] is True


def test_download_flow_success(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Test complete download flow:
    1. Upload encrypted file.
    2. Provider agent stores chunk.
    3. Ephemeral spool is purged on completion.
    4. Consumer requests GET /download.
    5. RETRIEVE_CHUNK job is queued and fulfilled by provider agent.
    6. Ciphertext is verified by SHA-256 and returned with cryptographic headers.
    """
    raw_plaintext = b"Confidential business document for download verification."
    payload_info = _make_mock_ciphertext(raw_plaintext)

    # 1. Upload
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "business_doc.pdf"},
    )
    assert res_up.status_code == 200
    file_id = res_up.json()["file_id"]

    # 2. Provider agent executes STORE_CHUNK
    agent: ProviderAgent = provider_node["agent"]
    assert agent.poll_and_process_jobs_once() == 1

    # Verify coordinator spool is clean (zero persistent ciphertext on coordinator)
    chunks = get_file_chunks(file_id)
    assert get_spool_chunk(chunks[0]["chunk_id"]) is None

    # 3. Simulate background provider agent polling during download
    # We trigger the agent poll in a small timer thread so it answers the RETRIEVE_CHUNK job
    def _delayed_agent_poll():
        time.sleep(0.4)
        agent.poll_and_process_jobs_once()

    poll_thread = threading.Thread(target=_delayed_agent_poll, daemon=True)
    poll_thread.start()

    # 4. Consumer calls download endpoint
    res_dl = client.get(f"/api/consumer/files/{file_id}/download", headers=consumer_a["headers"])
    poll_thread.join(timeout=3.0)

    assert res_dl.status_code == 200
    assert res_dl.content == payload_info["ciphertext"]
    assert res_dl.headers["X-IV-Hex"] == payload_info["iv_hex"]
    assert res_dl.headers["X-SHA256-Checksum"] == payload_info["sha256_hash"]
    assert res_dl.headers["X-Plaintext-Size"] == str(payload_info["file_size"])
    assert res_dl.headers["X-Encrypted-Size"] == str(payload_info["encrypted_size"])
    assert 'filename="business_doc.pdf.enc"' in res_dl.headers["Content-Disposition"]

    # Spool must be purged again immediately after delivery
    assert get_spool_chunk(chunks[0]["chunk_id"]) is None


def test_download_unstored_file_conflict(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Attempting to download a file in UPLOADING or FAILED state returns 409 Conflict.
    """
    payload_info = _make_mock_ciphertext(b"not stored yet")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "unstored.bin"},
    )
    file_id = res_up.json()["file_id"]

    # Download before stored -> 409
    res_dl = client.get(f"/api/consumer/files/{file_id}/download", headers=consumer_a["headers"])
    assert res_dl.status_code == 409
    assert "not ready for download" in res_dl.json()["detail"]


def test_download_offline_provider(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    If the provider node holding the stored chunk is offline, download returns 503.
    """
    payload_info = _make_mock_ciphertext(b"offline provider test payload")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "offline.bin"},
    )
    file_id = res_up.json()["file_id"]
    agent: ProviderAgent = provider_node["agent"]
    agent.poll_and_process_jobs_once()  # STORE_CHUNK completed

    # Set provider node status to STOPPED
    set_provider_node_status(provider_node["user"]["id"], "STOPPED")

    res_dl = client.get(f"/api/consumer/files/{file_id}/download", headers=consumer_a["headers"])
    assert res_dl.status_code == 503
    assert "currently offline or unreachable" in res_dl.json()["detail"]


# ==============================================================================
# 4. Deletion Flow Tests
# ==============================================================================

def test_delete_file_flow_success(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Test deletion:
    1. Upload and store file.
    2. Consumer requests DELETE /api/consumer/files/{id}.
    3. Provider agent executes DELETE_CHUNK job in background.
    4. Coordinator confirms chunk deleted, quota decremented, and file marked DELETED.
    5. Subsequent delete call is idempotent.
    """
    payload_info = _make_mock_ciphertext(b"payload to delete safely")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "delete_me.pdf"},
    )
    file_id = res_up.json()["file_id"]
    agent: ProviderAgent = provider_node["agent"]
    agent.poll_and_process_jobs_once()  # STORE_CHUNK completed

    chunks = get_file_chunks(file_id)
    chunk_id = chunks[0]["chunk_id"]

    # Verify physical file exists before deletion
    sm: StorageManager = provider_node["storage"]
    assert sm.read_chunk(chunk_id) == payload_info["ciphertext"]
    node_before = get_provider_node_by_id(provider_node["node"]["id"])

    # Launch agent polling in background thread to handle DELETE_CHUNK job
    def _delayed_delete_poll():
        time.sleep(0.3)
        agent.poll_and_process_jobs_once()

    poll_thread = threading.Thread(target=_delayed_delete_poll, daemon=True)
    poll_thread.start()

    res_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    poll_thread.join(timeout=3.0)

    assert res_del.status_code == 200
    assert res_del.json()["status"] == "DELETED"

    # Physical chunk must be gone
    with pytest.raises(FileNotFoundError):
        sm.read_chunk(chunk_id)

    # Quota decremented
    node_after = get_provider_node_by_id(provider_node["node"]["id"])
    assert node_after["used_bytes"] == node_before["used_bytes"] - payload_info["encrypted_size"]

    # File status is DELETED
    file_db = get_file_by_id(file_id)
    assert file_db["status"] == "DELETED"

    # Excluded from consumer files list
    res_list = client.get("/api/consumer/files", headers=consumer_a["headers"])
    assert not any(f["file_id"] == file_id for f in res_list.json())

    # Idempotent second delete call
    res_del2 = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    assert res_del2.status_code == 200
    assert res_del2.json()["status"] == "DELETED"


def test_binary_upload_stream(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    Test uploading ciphertext as a raw application/octet-stream body with headers.
    """
    payload_info = _make_mock_ciphertext(b"raw binary upload test stream")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers={
            **consumer_a["headers"],
            "Content-Type": "application/octet-stream",
            "X-Filename": "stream_upload.bin",
            "X-Plaintext-Size": str(payload_info["file_size"]),
            "X-IV-Hex": payload_info["iv_hex"],
            "X-SHA256-Checksum": payload_info["sha256_hash"],
        },
        content=payload_info["ciphertext"],
    )
    assert res_up.status_code == 200
    data = res_up.json()
    assert data["filename"] == "stream_upload.bin"
    assert data["file_size"] == payload_info["file_size"]
    assert data["encrypted_size"] == payload_info["encrypted_size"]
    assert data["status"] == "UPLOADING"


def test_delete_offline_provider(
    client: TestClient,
    consumer_a: Dict[str, Any],
    provider_node: Dict[str, Any],
):
    """
    If the provider node is offline, deleting a STORED file returns 503.
    """
    payload_info = _make_mock_ciphertext(b"cannot delete while offline")
    res_up = client.post(
        "/api/consumer/files/upload",
        headers=consumer_a["headers"],
        json={**payload_info["json_body"], "filename": "offline_del.bin"},
    )
    file_id = res_up.json()["file_id"]
    agent: ProviderAgent = provider_node["agent"]
    agent.poll_and_process_jobs_once()  # STORE_CHUNK completed

    # Stop provider node
    set_provider_node_status(provider_node["user"]["id"], "STOPPED")

    res_del = client.delete(f"/api/consumer/files/{file_id}", headers=consumer_a["headers"])
    assert res_del.status_code == 503
    assert "currently offline" in res_del.json()["detail"]

