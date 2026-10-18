"""
Verification script for Phase 2: Coordinator and Provider Agent Job Transfer Pipeline.
Executes against live coordinator at http://127.0.0.1:8001.
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import httpx

from backend.database import (
    commit_provider_quota,
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    create_user,
    get_chunk_by_id,
    get_chunk_job_by_id,
    get_file_by_id,
    get_provider_node,
    init_db,
    reserve_provider_quota,
    upsert_provider_node,
)
from backend.security import create_access_token, hash_password
from backend.spool import get_spool_chunk, save_spool_chunk
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager

COORDINATOR_URL = "http://127.0.0.1:8001"


def main():
    print("=" * 70)
    print("Store-Share Phase 2: Live Localhost Job Transfer Pipeline Verification")
    print("=" * 70)

    # 1. Check health
    res = httpx.get(f"{COORDINATOR_URL}/health", timeout=5.0)
    assert res.status_code == 200, f"Health check failed: {res.text}"
    print(f"[*] Coordinator health: {res.json()['status']} ({COORDINATOR_URL})")

    # 2. Setup provider test account and storage
    provider_email = f"live_provider_{uuid.uuid4().hex[:6]}@storeshare.io"
    password = "LiveSecurePass123!"
    provider_user = create_user(
        email=provider_email,
        password_hash=hash_password(password),
        role="PROVIDER",
    )
    token, _ = create_access_token(user_id=provider_user["id"], email=provider_email, role="PROVIDER")
    auth_headers = {"Authorization": f"Bearer {token}"}
    print(f"[*] Created Provider #{provider_user['id']}: {provider_email}")

    temp_storage_dir = Path(tempfile.mkdtemp(prefix="storeshare_live_provider_"))
    quota_bytes = 50 * 1024 * 1024  # 50 MB
    print(f"[*] Local storage directory: {temp_storage_dir}")

    # Register storage setup via API
    setup_res = httpx.post(
        f"{COORDINATOR_URL}/api/provider/node/setup",
        headers=auth_headers,
        json={
            "storage_dir": str(temp_storage_dir),
            "quota_gb": 0.05,
            "action": "START",
        },
        timeout=10.0,
    )
    assert setup_res.status_code == 200, f"Setup failed: {setup_res.text}"
    node = get_provider_node(provider_user["id"])
    print(f"[*] Storage node configured: Node #{node['id']}, Status: {setup_res.json()['status']}")

    # 3. Initialize ProviderAgent
    storage_mgr = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota_bytes)
    cfg = AgentConfig(
        coordinator_url=COORDINATOR_URL,
        storage_dir=str(temp_storage_dir),
        quota_gb=0.05,
        token=token,
    )
    agent = ProviderAgent(config=cfg, storage_manager=storage_mgr)

    # Send initial heartbeat
    hb = agent.send_heartbeat()
    print(f"[*] Initial heartbeat sent: status={hb['status']}, action={hb['action']}")

    # 4. Prepare simulated client-side AES-256-GCM ciphertext test payload
    # In Store-Share, ciphertext = IV (12 bytes) + encrypted data + GCM tag (16 bytes)
    mock_iv = os.urandom(12).hex()  # 24 hex characters
    raw_plaintext = b"Hello Store-Share! This is a confidential test file."
    mock_ciphertext = os.urandom(len(raw_plaintext) + 16)  # Simulated encrypted payload
    mock_sha = hashlib.sha256(mock_ciphertext).hexdigest()
    chunk_id = f"chunk_live_{uuid.uuid4().hex[:12]}"

    print(f"[*] Test payload prepared: {len(mock_ciphertext)} bytes, SHA-256: {mock_sha[:16]}...")

    # 5. Reserve quota & create file record
    reserve_provider_quota(node["id"], len(mock_ciphertext))
    consumer_user = create_user(
        email=f"consumer_live_{uuid.uuid4().hex[:6]}@storeshare.io",
        password_hash=hash_password("Consumer123!"),
        role="CONSUMER",
    )
    file_rec = create_file_record(
        owner_id=consumer_user["id"],
        filename="live_confidential_document.pdf",
        file_size=len(raw_plaintext),
        encrypted_size=len(mock_ciphertext),
        iv_hex=mock_iv,
        sha256_hash=mock_sha,
    )
    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=node["id"],
        chunk_size=len(mock_ciphertext),
    )
    store_job = create_chunk_job(
        provider_node_id=node["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
    )
    save_spool_chunk(chunk_id, mock_ciphertext)
    print(f"[*] STORE_CHUNK Job #{store_job['id']} created and payload spooled on coordinator.")

    # 6. Provider Agent polls and processes STORE_CHUNK job
    processed = agent.poll_and_process_jobs_once()
    assert processed == 1, "Agent failed to process STORE_CHUNK job!"

    # Verify physical file written
    chunk_file = temp_storage_dir / "chunks" / chunk_id
    assert chunk_file.is_file(), f"Physical chunk file not found at {chunk_file}!"
    assert chunk_file.read_bytes() == mock_ciphertext, "On-disk ciphertext does not match!"
    print(f"[OK] STORE_CHUNK: Physical chunk verified on disk ({chunk_file.stat().st_size} bytes).")

    # Verify coordinator state
    job_rec = get_chunk_job_by_id(store_job["id"])
    assert job_rec["status"] == "COMPLETED", f"Expected COMPLETED, got {job_rec['status']}"
    file_state = get_file_by_id(file_rec["id"])
    assert file_state["status"] == "STORED", f"Expected STORED, got {file_state['status']}"
    assert get_spool_chunk(chunk_id) is None, "Ephemeral spool chunk was not purged!"
    print(f"[OK] STORE_CHUNK: Coordinator confirmed file STORED and ephemeral spool purged.")

    # 7. Test RETRIEVE_CHUNK flow
    retrieve_job = create_chunk_job(
        provider_node_id=node["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="RETRIEVE_CHUNK",
    )
    print(f"[*] RETRIEVE_CHUNK Job #{retrieve_job['id']} created.")
    processed_ret = agent.poll_and_process_jobs_once()
    assert processed_ret == 1, "Agent failed to process RETRIEVE_CHUNK job!"

    ret_job_rec = get_chunk_job_by_id(retrieve_job["id"])
    assert ret_job_rec["status"] == "COMPLETED"
    retrieved_spool = get_spool_chunk(chunk_id)
    assert retrieved_spool == mock_ciphertext, "Retrieved chunk payload mismatch!"
    print(f"[OK] RETRIEVE_CHUNK: Provider read from disk, coordinator verified SHA-256 and received data.")

    # 8. Test DELETE_CHUNK flow
    delete_job = create_chunk_job(
        provider_node_id=node["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="DELETE_CHUNK",
    )
    print(f"[*] DELETE_CHUNK Job #{delete_job['id']} created.")
    processed_del = agent.poll_and_process_jobs_once()
    assert processed_del == 1, "Agent failed to process DELETE_CHUNK job!"

    assert not chunk_file.exists(), "Chunk file still exists on disk after DELETE_CHUNK!"
    del_job_rec = get_chunk_job_by_id(delete_job["id"])
    assert del_job_rec["status"] == "COMPLETED"
    print(f"[OK] DELETE_CHUNK: Provider deleted chunk from disk and updated quota.")

    # Cleanup temp directory and agent
    agent.close()
    shutil.rmtree(temp_storage_dir, ignore_errors=True)
    print("=" * 70)
    print("[SUCCESS] All Phase 2 pipeline flows verified against live coordinator!")
    print("=" * 70)


if __name__ == "__main__":
    main()
