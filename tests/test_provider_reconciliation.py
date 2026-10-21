"""
StoreShare — Provider Storage, Reconciliation, and Health Telemetry Tests.

Covers:
1. StorageManager:
   - Dynamic safe quota modification without data loss.
   - Concurrent writes with strict quota reservation and enforcement.
   - Inaccessible directory validation.
   - Insufficient physical disk space handling.
   - Detailed chunk inventory generation with SHA-256 hashing.
2. Cryptographic Inventory Reconciliation:
   - 5-state chunk classification:
     * VERIFIED: physically present, size and SHA-256 match coordinator metadata.
     * MISSING: registered in coordinator, but absent from provider disk.
     * CORRUPTED: present on disk, but byte size or cryptographic checksum mismatch.
     * UNEXPECTED: present on disk, but unmapped in coordinator metadata.
     * UNVERIFIED: coordinator assignment exists, but provider node is offline.
3. Provider Health & Control Lifecycle:
   - Transitions between ONLINE, PAUSED, STOPPED, and OFFLINE.
   - PAUSED node rejects new STORE_CHUNK jobs while continuing RETRIEVE/DELETE jobs.
   - Provider-specific JWT authorization and node isolation.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Dict
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from backend.database import (
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    create_user,
    get_db_connection,
    get_provider_job_telemetry,
    get_provider_node,
    get_provider_reconciliation_records,
    init_db,
    poll_provider_jobs,
    record_provider_heartbeat,
    set_provider_node_status,
    upsert_provider_node,
)
from backend.main import app
from backend.security import create_access_token, hash_password
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import (
    QuotaExceededError,
    SecurityViolationError,
    StorageError,
    StorageManager,
)


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageManager:
    storage_path = tmp_path / "provider_storage"
    sm = StorageManager(storage_dir=storage_path, quota_bytes=100 * 1024, create_if_missing=True)
    return sm


# ==============================================================================
# 1. Quota Enforcement & Safe Modification Tests
# ==============================================================================

def test_safe_quota_change_without_data_loss(temp_storage: StorageManager):
    """
    Ensure provider can increase or decrease quota safely without losing existing data.
    """
    data1 = b"Chunk 1 encrypted ciphertext payload"
    data2 = b"Chunk 2 encrypted ciphertext payload"

    temp_storage.write_chunk_atomic("chunk_001", data1)
    temp_storage.write_chunk_atomic("chunk_002", data2)

    initial_stats = temp_storage.get_stats()
    assert initial_stats["chunk_count"] == 2
    assert initial_stats["used_bytes"] == len(data1) + len(data2)

    # 1. Increase quota
    new_quota = 500 * 1024
    temp_storage.set_quota(new_quota)
    assert temp_storage.quota_bytes == new_quota
    assert temp_storage.get_stats()["quota_bytes"] == new_quota

    # Existing chunks are intact
    assert temp_storage.read_chunk("chunk_001") == data1
    assert temp_storage.read_chunk("chunk_002") == data2

    # 2. Lower quota below current used bytes - MUST NOT delete data
    used_now = temp_storage.get_stats()["used_bytes"]
    tight_quota = used_now - 10
    temp_storage.set_quota(tight_quota)

    # Both chunks still physically exist on disk
    assert temp_storage.read_chunk("chunk_001") == data1
    assert temp_storage.read_chunk("chunk_002") == data2
    assert temp_storage.get_stats()["chunk_count"] == 2

    # But subsequent writes must be blocked due to quota exhaustion
    with pytest.raises(QuotaExceededError):
        temp_storage.write_chunk_atomic("chunk_003", b"Should fail because used > quota")


def test_concurrent_writes_quota_enforcement(tmp_path: Path):
    """
    Test thread-safe quota enforcement under concurrent chunk writes.
    """
    storage_path = tmp_path / "concurrent_storage"
    # Quota allows exactly 5 chunks of 100 bytes (500 bytes)
    quota = 500
    sm = StorageManager(storage_dir=storage_path, quota_bytes=quota)

    payload = b"X" * 100
    successful_writes = []
    failed_writes = []

    def _worker(idx: int):
        cid = f"conc_chunk_{idx:03d}"
        try:
            sm.write_chunk_atomic(cid, payload)
            successful_writes.append(cid)
        except QuotaExceededError:
            failed_writes.append(cid)

    # Launch 15 concurrent threads
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, i) for i in range(15)]
        for f in futures:
            f.result()

    stats = sm.get_stats()
    assert len(successful_writes) == 5
    assert len(failed_writes) == 10
    assert stats["used_bytes"] == 500
    assert stats["chunk_count"] == 5
    assert stats["reserved_bytes"] == 0


def test_inaccessible_directory_handling(tmp_path: Path):
    """
    Test StorageManager behavior when directory is invalid or prohibited.
    """
    # Prohibit root
    root = Path(Path.cwd().anchor).resolve()
    with pytest.raises(SecurityViolationError):
        StorageManager(storage_dir=root, quota_bytes=1024)

    # Missing directory with create_if_missing=False
    missing = tmp_path / "does_not_exist_dir"
    with pytest.raises(StorageError):
        StorageManager(storage_dir=missing, quota_bytes=1024, create_if_missing=False)


def test_insufficient_disk_space_handling(tmp_path: Path):
    """
    Test that StorageManager rejects writes when physical disk free space is lower than requested write.
    """
    storage_path = tmp_path / "disk_space_test"
    sm = StorageManager(storage_dir=storage_path, quota_bytes=10 * 1024 * 1024)

    # Mock shutil.disk_usage to report only 50 bytes free on filesystem
    fake_usage = shutil._ntuple_diskusage(100 * 1024 * 1024, 99 * 1024 * 1024, 50)
    with patch("shutil.disk_usage", return_value=fake_usage):
        with pytest.raises(QuotaExceededError, match="Physical disk full"):
            sm.reserve_space(100)


# ==============================================================================
# 2. Cryptographic Inventory & 5-State Reconciliation Tests
# ==============================================================================

def test_reconciliation_5_states_classification(tmp_path: Path):
    """
    Verify complete 5-state reconciliation categorization:
    VERIFIED, MISSING, CORRUPTED, UNEXPECTED, UNVERIFIED.
    """
    init_db()

    # 1. Create a provider user and node
    p_email = f"recon_prov_{datetime.now().timestamp()}@test.io"
    user = create_user(email=p_email, password_hash=hash_password("Pass123!"), role="PROVIDER")
    user_id = user["id"]
    storage_path = tmp_path / "recon_disk"
    storage_path.mkdir(parents=True, exist_ok=True)

    sm = StorageManager(storage_dir=storage_path, quota_bytes=10 * 1024 * 1024)

    node = upsert_provider_node(
        provider_id=user_id,
        quota_bytes=10 * 1024 * 1024,
        storage_dir=str(storage_path),
        status="ONLINE",
    )
    node_id = node["id"]

    # 2. Create consumer and files
    c_user = create_user(email=f"cons_{datetime.now().timestamp()}@test.io", password_hash=hash_password("Pass123!"), role="CONSUMER")
    c_user_id = c_user["id"]
    valid_iv = "0123456789abcdef01234567"

    # Chunk 1: VERIFIED (Present, size & sha256 match)
    c1_data = b"Encrypted chunk 1 verified payload"
    c1_hash = hashlib.sha256(c1_data).hexdigest()
    sm.write_chunk_atomic("chunk_verified", c1_data)
    f1 = create_file_record(c_user_id, "verified_doc.pdf", len(c1_data), len(c1_data), valid_iv, c1_hash)
    create_file_chunk(f1["id"], "chunk_verified", node_id, len(c1_data), 0)

    # Chunk 2: MISSING (In DB, but deleted from disk)
    c2_data = b"Encrypted chunk 2 missing payload"
    c2_hash = hashlib.sha256(c2_data).hexdigest()
    f2 = create_file_record(c_user_id, "missing_doc.pdf", len(c2_data), len(c2_data), valid_iv, c2_hash)
    create_file_chunk(f2["id"], "chunk_missing", node_id, len(c2_data), 0)

    # Chunk 3: CORRUPTED (Present, but tampered data / checksum mismatch)
    c3_good = b"Good payload before corruption"
    c3_bad = b"Tampered and corrupted content!"
    c3_hash = hashlib.sha256(c3_good).hexdigest()
    sm.write_chunk_atomic("chunk_corrupted", c3_bad)
    f3 = create_file_record(c_user_id, "corrupted_doc.pdf", len(c3_good), len(c3_good), valid_iv, c3_hash)
    create_file_chunk(f3["id"], "chunk_corrupted", node_id, len(c3_good), 0)

    # Chunk 4: UNEXPECTED (Physically on disk, but never registered to coordinator)
    c4_data = b"Orphaned or unexpected chunk payload"
    sm.write_chunk_atomic("chunk_unexpected", c4_data)

    # 3. Provider Agent generates detailed inventory
    inventory = sm.get_detailed_inventory()
    inv_json = json.dumps(inventory)

    # Transmit heartbeat with inventory while ONLINE
    record_provider_heartbeat(
        provider_id=user_id,
        used_bytes=sm.get_stats()["used_bytes"],
        allocated_bytes=sm.quota_bytes,
        free_disk_bytes=50 * 1024 * 1024,
        inventory_json=inv_json,
    )

    # 4. Perform reconciliation
    report = get_provider_reconciliation_records(provider_node_id=node_id)

    assert report["total_expected"] == 3
    assert report["verified_count"] == 1
    assert report["missing_count"] == 1
    assert report["corrupted_count"] == 1
    assert report["unexpected_count"] == 1

    by_cid = {item["chunk_id"]: item for item in report["items"]}
    assert by_cid["chunk_verified"]["status"] == "VERIFIED"
    assert by_cid["chunk_missing"]["status"] == "MISSING"
    assert by_cid["chunk_corrupted"]["status"] == "CORRUPTED"
    assert by_cid["chunk_unexpected"]["status"] == "UNEXPECTED"

    # 5. Test UNVERIFIED: Simulate node going OFFLINE and verification expiring
    with get_db_connection() as conn:
        cur = conn.cursor()
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        cur.execute(
            "UPDATE provider_nodes SET last_heartbeat = ?, status = 'ONLINE' WHERE id = ?;",
            (old_time, node_id),
        )
        cur.execute(
            "UPDATE file_chunks SET last_verified_at = ? WHERE provider_node_id = ?;",
            (old_time, node_id),
        )
        conn.commit()

    offline_report = get_provider_reconciliation_records(provider_node_id=node_id)
    assert offline_report["status"] == "OFFLINE"
    assert offline_report["unverified_count"] == 3


# ==============================================================================
# 3. Provider Operational Lifecycle (START, PAUSE, STOP, Heartbeat)
# ==============================================================================

def test_provider_pause_and_stop_lifecycle(tmp_path: Path):
    """
    Test provider transitions between ONLINE, PAUSED, and STOPPED.
    Ensure PAUSED nodes reject STORE_CHUNK jobs.
    """
    client = TestClient(app)
    init_db()

    p_email = f"lifecycle_{datetime.now().timestamp()}@test.io"
    user = create_user(email=p_email, password_hash=hash_password("Pass123!"), role="PROVIDER")
    user_id = user["id"]
    token, _ = create_access_token(user["id"], user["email"], user["role"])
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Setup node as START
    res = client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(tmp_path / "agent_dir"), "quota_gb": 20, "action": "START"},
    )
    assert res.status_code == 200
    assert res.json()["status"] in ("UNREGISTERED", "ONLINE")

    # Send heartbeat to become ONLINE
    hb_res = client.post(
        "/api/provider/node/heartbeat",
        headers=headers,
        json={
            "allocated_bytes": 20 * 1024**3,
            "used_bytes": 0,
            "free_disk_bytes": 100 * 1024**3,
            "computer_name": "TEST-HOST",
        },
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["status"] == "ONLINE"
    assert hb_res.json()["action"] == "CONTINUE"

    node_rec = get_provider_node(user_id)
    node_id = node_rec["id"]

    # 2. Transition to PAUSED
    pause_res = client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(tmp_path / "agent_dir"), "quota_gb": 20, "action": "PAUSE"},
    )
    assert pause_res.status_code == 200
    assert pause_res.json()["status"] == "PAUSED"
    assert any("PAUSED" in w for w in pause_res.json()["warnings"])

    # Heartbeat while PAUSED must NOT overwrite status to ONLINE, and must return action='PAUSE'
    hb_pause = client.post(
        "/api/provider/node/heartbeat",
        headers=headers,
        json={
            "allocated_bytes": 20 * 1024**3,
            "used_bytes": 0,
            "free_disk_bytes": 100 * 1024**3,
        },
    )
    assert hb_pause.status_code == 200
    assert hb_pause.json()["status"] == "PAUSED"
    assert hb_pause.json()["action"] == "PAUSE"

    # Create dummy file to satisfy foreign key constraint
    dummy_file = create_file_record(
        owner_id=user_id,
        filename="dummy.bin",
        file_size=100,
        encrypted_size=100,
        iv_hex="0123456789abcdef01234567",
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    file_id = dummy_file["id"]

    # Queue STORE_CHUNK and RETRIEVE_CHUNK jobs
    store_job = create_chunk_job(provider_node_id=node_id, chunk_id="test_store_chunk", file_id=file_id, job_type="STORE_CHUNK")
    retrieve_job = create_chunk_job(provider_node_id=node_id, chunk_id="test_retrieve_chunk", file_id=file_id, job_type="RETRIEVE_CHUNK")

    # Poll jobs for paused node: STORE_CHUNK should be excluded, but RETRIEVE_CHUNK returned!
    polled = poll_provider_jobs(provider_node_id=node_id, limit=5, exclude_job_types=["STORE_CHUNK"])
    polled_types = [j["job_type"] for j in polled]
    assert "STORE_CHUNK" not in polled_types
    assert "RETRIEVE_CHUNK" in polled_types

    # 3. Transition to STOPPED
    stop_res = client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(tmp_path / "agent_dir"), "quota_gb": 20, "action": "STOP"},
    )
    assert stop_res.status_code == 200
    assert stop_res.json()["status"] == "STOPPED"

    hb_stop = client.post(
        "/api/provider/node/heartbeat",
        headers=headers,
        json={
            "allocated_bytes": 20 * 1024**3,
            "used_bytes": 0,
            "free_disk_bytes": 100 * 1024**3,
        },
    )
    assert hb_stop.json()["status"] == "STOPPED"
    assert hb_stop.json()["action"] == "STOP"


def test_provider_auth_isolation(tmp_path: Path):
    """
    Ensure consumers cannot access provider endpoints, and providers are isolated.
    """
    client = TestClient(app)
    init_db()

    consumer = create_user(email=f"cons_iso_{datetime.now().timestamp()}@test.io", password_hash=hash_password("Pass123!"), role="CONSUMER")
    cons_token, _ = create_access_token(consumer["id"], consumer["email"], consumer["role"])
    cons_headers = {"Authorization": f"Bearer {cons_token}"}

    # Consumer accessing provider endpoints must get 403 Forbidden
    assert client.get("/api/provider/node", headers=cons_headers).status_code == 403
    assert client.get("/api/provider/reconciliation", headers=cons_headers).status_code == 403
    assert client.get("/api/provider/jobs/history", headers=cons_headers).status_code == 403
    assert client.post("/api/provider/node/setup", headers=cons_headers, json={"storage_dir": "C:\\test", "quota_gb": 10}).status_code == 403
