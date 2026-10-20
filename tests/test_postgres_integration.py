"""
PostgreSQL Integration Test Suite for StoreShare (Stage C)
==========================================================
Executes end-to-end integration flows against the real PostgreSQL database:
- Health and database status reporting with masked credentials
- Consumer and provider authentication, JWT issuance, and RBAC
- Provider node setup, heartbeat telemetry, and online state
- AES-256-GCM encrypted file upload, coordinator quota reservation, and spooling
- Real provider agent polling, atomic disk storage, and completion acknowledgement
- Consumer download, AES-256-GCM decryption, and SHA-256 integrity verification
- Cryptographic failure modes (wrong key, ciphertext corruption)
- Deletion flow with physical chunk removal and quota release
- Safe concurrent job polling with PostgreSQL FOR UPDATE SKIP LOCKED
- Duplicate job completion idempotency
- Foreign-key constraints and identity sequence generation
- Preservation of historical migrated records
"""

import hashlib
import os
from pathlib import Path
import time
from typing import Any, Dict, Generator, List, Tuple
import uuid

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from backend.database import (
    get_database_url,
    get_database_info,
    get_db_connection,
    get_file_by_id,
    get_provider_node,
    get_provider_node_by_id,
    get_user_by_email,
    poll_provider_jobs,
    record_provider_heartbeat,
    upsert_provider_node,
    create_user,
    create_file_record,
    create_chunk_job,
    create_file_chunk,
)
from backend.security import hash_password
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager

# Mark all tests in this file for PostgreSQL integration
pytestmark = pytest.mark.postgres_integration


@pytest.fixture
def pg_client() -> Generator[TestClient, None, None]:
    """FastAPI TestClient connected directly to PostgreSQL."""
    from backend.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_entities_tracker():
    """
    Tracks IDs of temporary test records created during integration tests
    so they can be cleanly cleaned up without touching historical migrated records.
    """
    created = {
        "users": set(),
        "provider_nodes": set(),
        "files": set(),
        "chunk_jobs": set(),
        "file_chunks": set(),
    }
    yield created

    # Clean up test entities in reverse dependency order
    pg_url = get_database_url()
    if not pg_url:
        return

    with get_db_connection(pg_url) as conn:
        cur = conn.cursor()
        if created["chunk_jobs"]:
            ids = tuple(created["chunk_jobs"])
            cur.execute(f"DELETE FROM chunk_jobs WHERE id IN ({','.join(['%s']*len(ids))})", ids)
        if created["file_chunks"]:
            ids = tuple(created["file_chunks"])
            cur.execute(f"DELETE FROM file_chunks WHERE id IN ({','.join(['%s']*len(ids))})", ids)
        if created["files"]:
            ids = tuple(created["files"])
            cur.execute(f"DELETE FROM chunk_jobs WHERE file_id IN ({','.join(['%s']*len(ids))})", ids)
            cur.execute(f"DELETE FROM file_chunks WHERE file_id IN ({','.join(['%s']*len(ids))})", ids)
            cur.execute(f"DELETE FROM files WHERE id IN ({','.join(['%s']*len(ids))})", ids)
        if created["provider_nodes"]:
            ids = tuple(created["provider_nodes"])
            cur.execute(f"DELETE FROM chunk_jobs WHERE provider_node_id IN ({','.join(['%s']*len(ids))})", ids)
            cur.execute(f"DELETE FROM file_chunks WHERE provider_node_id IN ({','.join(['%s']*len(ids))})", ids)
            cur.execute(f"DELETE FROM provider_nodes WHERE id IN ({','.join(['%s']*len(ids))})", ids)
        if created["users"]:
            ids = tuple(created["users"])
            cur.execute(f"DELETE FROM provider_nodes WHERE provider_id IN ({','.join(['%s']*len(ids))})", ids)
            cur.execute(f"DELETE FROM files WHERE owner_id IN ({','.join(['%s']*len(ids))})", ids)
            cur.execute(f"DELETE FROM users WHERE id IN ({','.join(['%s']*len(ids))})", ids)
        conn.commit()


# ==============================================================================
# 1. Health Endpoint and Database Info
# ==============================================================================

def test_health_endpoint_postgres(pg_client: TestClient):
    """Verify health check reports PostgreSQL connected with masked credentials."""
    resp = pg_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "postgresql://" in data["database_path"]
    assert "****" in data["database_path"]
    # Ensure raw password is NOT leaked in response
    assert "storeshare123" not in data["database_path"]


# ==============================================================================
# 2. Authentication, JWT, and Role Enforcement
# ==============================================================================

def test_auth_and_role_restrictions_postgres(pg_client: TestClient, test_entities_tracker: Dict[str, set]):
    """
    Verify consumer and provider user creation in PostgreSQL,
    identity sequence assignment, JWT validation, and RBAC enforcement.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    consumer_email = f"pg_consumer_{unique_suffix}@storeshare.io"
    provider_email = f"pg_provider_{unique_suffix}@storeshare.io"
    password = "IntegrationPassword123!"

    # 1. Register Consumer
    resp = pg_client.post("/api/auth/register", json={
        "email": consumer_email,
        "password": password,
    })
    assert resp.status_code == 201
    consumer_user = resp.json()
    test_entities_tracker["users"].add(consumer_user["id"])
    assert consumer_user["role"] == "CONSUMER"
    # Verify identity sequence generated ID > 43 (migrated user count)
    assert consumer_user["id"] > 43

    # 2. Login Consumer
    resp = pg_client.post("/api/auth/login", json={
        "email": consumer_email,
        "password": password,
    })
    assert resp.status_code == 200
    consumer_token = resp.json()["access_token"]
    consumer_headers = {"Authorization": f"Bearer {consumer_token}"}

    # 3. Create Provider directly in PostgreSQL
    provider_user = create_user(provider_email, hash_password(password), role="PROVIDER")
    test_entities_tracker["users"].add(provider_user["id"])
    assert provider_user["role"] == "PROVIDER"

    resp = pg_client.post("/api/auth/login", json={
        "email": provider_email,
        "password": password,
    })
    assert resp.status_code == 200
    provider_token = resp.json()["access_token"]
    provider_headers = {"Authorization": f"Bearer {provider_token}"}

    # 4. RBAC: Consumer cannot access provider endpoints
    resp = pg_client.post(
        "/api/provider/node/setup",
        headers=consumer_headers,
        json={"storage_dir": "C:/fake", "quota_gb": 1.0, "action": "START"}
    )
    assert resp.status_code == 403
    assert "Requires role: PROVIDER" in resp.json()["detail"]

    # 5. RBAC: Provider cannot access consumer endpoints
    resp = pg_client.get("/api/consumer/files", headers=provider_headers)
    assert resp.status_code == 403
    assert "Requires role: CONSUMER" in resp.json()["detail"]


# ==============================================================================
# 3. Provider Setup & Heartbeat Telemetry
# ==============================================================================

def test_provider_setup_and_heartbeat_postgres(
    pg_client: TestClient,
    tmp_path: Path,
    test_entities_tracker: Dict[str, set]
):
    """Verify provider node configuration, storage limits, and heartbeat in PostgreSQL."""
    unique_suffix = uuid.uuid4().hex[:8]
    provider_email = f"pg_prov_node_{unique_suffix}@storeshare.io"
    password = "ProviderPass123!"

    u = create_user(provider_email, hash_password(password), role="PROVIDER")
    test_entities_tracker["users"].add(u["id"])

    login_resp = pg_client.post("/api/auth/login", json={"email": provider_email, "password": password})
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    prov_dir = tmp_path / "pg_prov_storage"
    prov_dir.mkdir(parents=True, exist_ok=True)

    # Setup node
    setup_resp = pg_client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(prov_dir), "quota_gb": 5.0, "action": "START"}
    )
    assert setup_resp.status_code == 200
    setup_data = setup_resp.json()
    node = get_provider_node(u["id"])
    test_entities_tracker["provider_nodes"].add(node["id"])
    assert setup_data["configured"] is True
    assert setup_data["quota_bytes"] == 5 * 1024 * 1024 * 1024

    # Send heartbeat with required telemetry fields
    hb_resp = pg_client.post(
        "/api/provider/node/heartbeat",
        headers=headers,
        json={
            "allocated_bytes": 5 * 1024 * 1024 * 1024,
            "used_bytes": 1024,
            "free_disk_bytes": 50 * 1024 * 1024 * 1024,
        },
    )
    assert hb_resp.status_code == 200
    assert hb_resp.json()["status"] == "ONLINE"


# ==============================================================================
# 4. End-to-End Encrypted File Upload, Storage, Download, Decrypt, and Deletion
# ==============================================================================

def test_encrypted_file_lifecycle_postgres(
    pg_client: TestClient,
    tmp_path: Path,
    test_entities_tracker: Dict[str, set]
):
    """
    Comprehensive end-to-end test against PostgreSQL:
    1. Register consumer and provider accounts.
    2. Setup provider node and agent with large quota (100 GB) for highest priority placement.
    3. Client generates AES-256 key and encrypts test payload with AES-GCM.
    4. Client uploads encrypted file; coordinator reserves quota and spools chunk.
    5. Provider agent polls job via PostgreSQL FOR UPDATE SKIP LOCKED.
    6. Agent downloads ciphertext from coordinator, writes atomically to local disk, acknowledges completion.
    7. Quota accounting verified in PostgreSQL: reserved bytes -> used bytes.
    8. Client downloads ciphertext, decrypts with AES-256 key, verifies original SHA-256 hash.
    9. Cryptographic verification: wrong key fails, altered ciphertext fails tag validation.
    10. Consumer deletes file; provider agent processes DELETE_CHUNK; chunk removed from disk; quota released.
    """
    import base64
    import threading

    unique_suffix = uuid.uuid4().hex[:8]
    consumer_email = f"pg_consumer_flow_{unique_suffix}@storeshare.io"
    provider_email = f"pg_prov_flow_{unique_suffix}@storeshare.io"
    password = "FlowPassword123!"

    # 1. Create accounts
    cu = create_user(consumer_email, hash_password(password), role="CONSUMER")
    pu = create_user(provider_email, hash_password(password), role="PROVIDER")
    test_entities_tracker["users"].add(cu["id"])
    test_entities_tracker["users"].add(pu["id"])

    c_token = pg_client.post("/api/auth/login", json={"email": consumer_email, "password": password}).json()["access_token"]
    p_token = pg_client.post("/api/auth/login", json={"email": provider_email, "password": password}).json()["access_token"]

    c_headers = {"Authorization": f"Bearer {c_token}"}
    p_headers = {"Authorization": f"Bearer {p_token}"}

    # 2. Setup provider node with 100 GB so it has the highest available quota
    prov_storage_dir = tmp_path / "pg_agent_storage"
    prov_storage_dir.mkdir(parents=True, exist_ok=True)
    setup_resp = pg_client.post(
        "/api/provider/node/setup",
        headers=p_headers,
        json={"storage_dir": str(prov_storage_dir), "quota_gb": 100.0, "action": "START"}
    )
    assert setup_resp.status_code == 200
    node = get_provider_node(pu["id"])
    test_entities_tracker["provider_nodes"].add(node["id"])

    # Ensure heartbeat is fresh so node is eligible for coordinator placement
    record_provider_heartbeat(provider_id=pu["id"], used_bytes=0, allocated_bytes=node["quota_bytes"])

    # Initialize ProviderAgent pointing to the test client
    storage_mgr = StorageManager(storage_dir=prov_storage_dir, quota_bytes=node["quota_bytes"])
    agent_config = AgentConfig(
        coordinator_url="http://testserver",
        storage_dir=str(prov_storage_dir),
        quota_gb=1.0,
        token=p_token,
    )
    agent = ProviderAgent(config=agent_config, storage_manager=storage_mgr)
    # Route agent HTTP calls directly through FastAPI TestClient
    agent.client.get = lambda url, **kwargs: pg_client.get(url, **kwargs)
    agent.client.post = lambda url, **kwargs: pg_client.post(url, **kwargs)

    # 3. Simulate browser AES-256-GCM encryption
    plaintext = b"StoreShare PostgreSQL Integration Verification Confidential Data Payload -- " + uuid.uuid4().bytes * 16
    raw_size = len(plaintext)

    aes_key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(aes_key)
    iv = os.urandom(12)  # Standard 96-bit AES-GCM IV
    ciphertext = aesgcm.encrypt(iv, plaintext, None)  # Appends 16-byte authentication tag
    encrypted_size = len(ciphertext)
    assert encrypted_size == raw_size + 16

    ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()
    b64_ciphertext = base64.b64encode(ciphertext).decode("utf-8")

    # 4. Consumer uploads encrypted file
    upload_resp = pg_client.post(
        "/api/consumer/files/upload",
        headers=c_headers,
        json={
            "filename": "financial_records.pdf",
            "file_size": raw_size,
            "iv_hex": iv.hex(),
            "sha256_hash": ciphertext_sha256,
            "ciphertext_base64": b64_ciphertext,
        },
    )
    assert upload_resp.status_code == 200
    file_info = upload_resp.json()
    file_id = file_info["file_id"]
    test_entities_tracker["files"].add(file_id)
    assert file_info["status"] == "UPLOADING"

    # Verify quota reservation in PostgreSQL
    node_after_upload = get_provider_node_by_id(node["id"])
    assert node_after_upload["reserved_bytes"] == encrypted_size
    assert node_after_upload["used_bytes"] == 0

    # 5. Provider Agent polls and processes STORE_CHUNK job
    processed_count = agent.poll_and_process_jobs_once()
    assert processed_count == 1

    # 6. Verify coordinator state after provider stores chunk
    file_record = get_file_by_id(file_id)
    assert file_record["status"] == "STORED"

    node_after_store = get_provider_node_by_id(node["id"])
    assert node_after_store["reserved_bytes"] == 0
    assert node_after_store["used_bytes"] == encrypted_size

    # Verify physical file on provider storage disk
    chunk_files = [f for f in (prov_storage_dir / "chunks").iterdir() if f.is_file() and not f.name.startswith(".tmp_")]
    assert len(chunk_files) == 1
    assert chunk_files[0].read_bytes() == ciphertext  # Disk contains ONLY ciphertext!

    # 7. Consumer downloads and decrypts file
    # Setup background poll thread to serve RETRIEVE_CHUNK job
    def _poll_retrieve():
        time.sleep(0.3)
        agent.poll_and_process_jobs_once()

    poll_thread = threading.Thread(target=_poll_retrieve, daemon=True)
    poll_thread.start()

    dl_resp = pg_client.get(f"/api/consumer/files/{file_id}/download", headers=c_headers)
    poll_thread.join(timeout=3.0)

    assert dl_resp.status_code == 200
    downloaded_ciphertext = dl_resp.content
    assert downloaded_ciphertext == ciphertext

    decrypted_plaintext = aesgcm.decrypt(iv, downloaded_ciphertext, None)
    assert decrypted_plaintext == plaintext
    assert hashlib.sha256(plaintext).hexdigest() == hashlib.sha256(decrypted_plaintext).hexdigest()

    # 8. Cryptographic failure modes
    # Wrong key fails
    wrong_key = AESGCM.generate_key(bit_length=256)
    with pytest.raises(Exception):
        AESGCM(wrong_key).decrypt(iv, downloaded_ciphertext, None)

    # Tampered ciphertext fails
    tampered_ciphertext = bytearray(downloaded_ciphertext)
    tampered_ciphertext[0] ^= 0x01
    with pytest.raises(Exception):
        aesgcm.decrypt(iv, bytes(tampered_ciphertext), None)

    # 9. Consumer deletes file
    def _poll_delete():
        time.sleep(0.3)
        agent.poll_and_process_jobs_once()

    poll_del_thread = threading.Thread(target=_poll_delete, daemon=True)
    poll_del_thread.start()

    del_resp = pg_client.delete(f"/api/consumer/files/{file_id}", headers=c_headers)
    poll_del_thread.join(timeout=3.0)
    assert del_resp.status_code == 200

    # Verify physical file removed from provider disk
    chunk_files_after = [f for f in (prov_storage_dir / "chunks").iterdir() if f.is_file() and not f.name.startswith(".tmp_")]
    assert len(chunk_files_after) == 0

    # Verify quota released in PostgreSQL
    node_after_del = get_provider_node_by_id(node["id"])
    assert node_after_del["used_bytes"] == 0
    assert node_after_del["reserved_bytes"] == 0

    # File status is DELETED
    file_record_after = get_file_by_id(file_id)
    assert file_record_after["status"] == "DELETED"


# ==============================================================================
# 5. Concurrent Job Polling with FOR UPDATE SKIP LOCKED
# ==============================================================================

def test_concurrent_job_polling_postgres(test_entities_tracker: Dict[str, set]):
    """
    Verify PostgreSQL row-locking behavior:
    Concurrent queries with 'FOR UPDATE SKIP LOCKED' select distinct queued jobs
    without blocking or duplicate dispatching.
    """
    pg_url = get_database_url()
    assert pg_url is not None

    unique_suffix = uuid.uuid4().hex[:8]

    u = create_user(f"pg_worker_{unique_suffix}@storeshare.io", hash_password("pass"), role="PROVIDER")
    test_entities_tracker["users"].add(u["id"])

    node = upsert_provider_node(u["id"], 1024 * 1024 * 1024, "C:/worker_test", status="ONLINE")
    test_entities_tracker["provider_nodes"].add(node["id"])

    f = create_file_record(u["id"], "test.bin", 100, 116, "00" * 12, "aa" * 32)
    test_entities_tracker["files"].add(f["id"])

    # Create 2 queued jobs
    j1 = create_chunk_job(node["id"], f"chunk_1_{unique_suffix}", f["id"], "STORE_CHUNK")
    j2 = create_chunk_job(node["id"], f"chunk_2_{unique_suffix}", f["id"], "STORE_CHUNK")
    test_entities_tracker["chunk_jobs"].add(j1["id"])
    test_entities_tracker["chunk_jobs"].add(j2["id"])

    # Simulate two concurrent worker transactions
    with get_db_connection(pg_url) as conn1:
        with get_db_connection(pg_url) as conn2:
            # Worker 1 polls limit 1
            cur1 = conn1.cursor()
            cur1.execute("""
                SELECT id FROM chunk_jobs
                WHERE provider_node_id = %s AND status = 'QUEUED'
                ORDER BY created_at ASC LIMIT 1
                FOR UPDATE SKIP LOCKED;
            """, (node["id"],))
            w1_job = cur1.fetchone()
            assert w1_job is not None

            # Worker 2 simultaneously polls limit 1
            # Thanks to SKIP LOCKED, worker 2 immediately skips worker 1's locked job
            cur2 = conn2.cursor()
            cur2.execute("""
                SELECT id FROM chunk_jobs
                WHERE provider_node_id = %s AND status = 'QUEUED'
                ORDER BY created_at ASC LIMIT 1
                FOR UPDATE SKIP LOCKED;
            """, (node["id"],))
            w2_job = cur2.fetchone()
            assert w2_job is not None

            # Crucial verification: Worker 1 and Worker 2 got different jobs!
            assert w1_job["id"] != w2_job["id"]
            polled_ids = {w1_job["id"], w2_job["id"]}
            assert polled_ids == {j1["id"], j2["id"]}


# ==============================================================================
# 6. Foreign-Key Constraints & Identity Sequence Validation
# ==============================================================================

def test_foreign_key_and_identity_postgres():
    """Verify PostgreSQL foreign-key constraint enforcement and identity sequences."""
    pg_url = get_database_url()
    assert pg_url is not None

    with get_db_connection(pg_url) as conn:
        cur = conn.cursor()

        # 1. Foreign-key violation: referencing non-existent provider_id
        import psycopg
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.conn.transaction():
                cur.execute("""
                    INSERT INTO provider_nodes (provider_id, storage_dir, quota_bytes)
                    VALUES (999999, 'C:/invalid', 1000);
                """)

        # 2. Sequence validation: verify sequence counter is >= max(id)
        cur.execute("SELECT last_value FROM users_id_seq;")
        seq_val = cur.fetchone()["last_value"]
        cur.execute("SELECT COALESCE(MAX(id), 0) as max_id FROM users;")
        max_id = cur.fetchone()["max_id"]
        assert seq_val >= max_id


# ==============================================================================
# 7. Duplicate Job Completion and Failure Idempotency
# ==============================================================================

def test_duplicate_job_completion_and_failure_postgres(
    pg_client: TestClient,
    test_entities_tracker: Dict[str, set]
):
    """
    Verify idempotency in PostgreSQL:
    1. Duplicate job completion returns 200 with idempotent=True and does not double-count quota.
    2. Duplicate job failure returns 200 with idempotent=True.
    """
    from backend.spool import save_spool_chunk, remove_spool_chunk

    unique_suffix = uuid.uuid4().hex[:8]
    p_email = f"dup_prov_{unique_suffix}@storeshare.io"
    c_email = f"dup_cons_{unique_suffix}@storeshare.io"
    pw = "DupPass123!"

    pu = create_user(p_email, hash_password(pw), role="PROVIDER")
    cu = create_user(c_email, hash_password(pw), role="CONSUMER")
    test_entities_tracker["users"].add(pu["id"])
    test_entities_tracker["users"].add(cu["id"])

    p_token = pg_client.post("/api/auth/login", json={"email": p_email, "password": pw}).json()["access_token"]
    p_headers = {"Authorization": f"Bearer {p_token}"}

    node = upsert_provider_node(pu["id"], 10 * 1024 * 1024, "C:/prov_dup", status="ONLINE")
    test_entities_tracker["provider_nodes"].add(node["id"])

    chunk_size = 512
    chunk_id = f"chunk_dup_{unique_suffix}"
    f = create_file_record(cu["id"], "dup_test.bin", chunk_size, chunk_size + 16, "00"*12, "11"*32)
    test_entities_tracker["files"].add(f["id"])

    # Create file_chunks record so complete_chunk_job finds the chunk and commits quota
    chunk_rec = create_file_chunk(f["id"], chunk_id, node["id"], chunk_size + 16)
    test_entities_tracker["file_chunks"].add(chunk_rec["id"])

    # Reserve quota initially as the upload endpoint would
    pg_url = get_database_url()
    with get_db_connection(pg_url) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE provider_nodes SET reserved_bytes = %s WHERE id = %s", (chunk_size + 16, node["id"]))
        conn.commit()

    # Save a fake chunk to spool
    save_spool_chunk(chunk_id, b"D" * (chunk_size + 16))

    # Create STORE_CHUNK job
    store_job = create_chunk_job(node["id"], chunk_id, f["id"], "STORE_CHUNK")
    test_entities_tracker["chunk_jobs"].add(store_job["id"])

    # 1. First completion
    comp1 = pg_client.post(f"/api/provider/jobs/{store_job['id']}/complete", headers=p_headers)
    assert comp1.status_code == 200
    assert comp1.json()["status"] == "COMPLETED"

    node_after_first = get_provider_node_by_id(node["id"])
    assert node_after_first["reserved_bytes"] == 0
    assert node_after_first["used_bytes"] == chunk_size + 16

    # 2. Duplicate completion
    comp2 = pg_client.post(f"/api/provider/jobs/{store_job['id']}/complete", headers=p_headers)
    assert comp2.status_code == 200
    assert comp2.json()["status"] == "COMPLETED"
    assert comp2.json().get("idempotent") is True

    # Quota must NOT be double counted
    node_after_second = get_provider_node_by_id(node["id"])
    assert node_after_second["reserved_bytes"] == 0
    assert node_after_second["used_bytes"] == chunk_size + 16

    # 3. Job failure and duplicate failure
    fail_chunk_id = f"chunk_fail_{unique_suffix}"
    fail_job = create_chunk_job(node["id"], fail_chunk_id, f["id"], "RETRIEVE_CHUNK")
    test_entities_tracker["chunk_jobs"].add(fail_job["id"])

    fail1 = pg_client.post(
        f"/api/provider/jobs/{fail_job['id']}/fail",
        headers=p_headers,
        json={"error_message": "Disk I/O error simulated"}
    )
    assert fail1.status_code == 200
    assert fail1.json()["status"] == "FAILED"

    # Duplicate fail
    fail2 = pg_client.post(
        f"/api/provider/jobs/{fail_job['id']}/fail",
        headers=p_headers,
        json={"error_message": "Disk I/O error retry"}
    )
    assert fail2.status_code == 200
    assert fail2.json()["status"] == "FAILED"
    assert fail2.json().get("idempotent") is True


# ==============================================================================
# 8. Transaction Boundaries, Rollback & Connection Error Handling
# ==============================================================================

def test_transaction_rollback_and_error_handling_postgres():
    """
    Verify PostgreSQL transaction rollback and connection error handling:
    1. Uncommitted writes inside a failed transaction are completely rolled back.
    2. Connection failure to an unreachable database returns a clean error.
    """
    import psycopg

    pg_url = get_database_url()
    assert pg_url is not None

    test_email = f"rollback_{uuid.uuid4().hex[:8]}@storeshare.io"

    # 1. Transaction rollback test
    with get_db_connection(pg_url) as conn:
        cur = conn.cursor()
        try:
            with conn.conn.transaction():
                cur.execute(
                    "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s)",
                    (test_email, "hash", "CONSUMER"),
                )
                # Intentional error to force rollback
                raise RuntimeError("Simulated mid-transaction failure")
        except RuntimeError:
            pass

    # Verify user was NOT saved
    saved_user = get_user_by_email(test_email)
    assert saved_user is None

    # 2. Connection failure error handling
    invalid_url = "postgresql://storeshare_app:wrong@127.0.0.1:54329/storeshare"
    with pytest.raises(psycopg.OperationalError):
        with psycopg.connect(invalid_url, connect_timeout=1):
            pass

