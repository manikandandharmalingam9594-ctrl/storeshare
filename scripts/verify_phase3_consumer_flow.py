"""
Live Verification Script for Store-Share Phase 3: Consumer Upload/Download APIs.
Runs against live coordinator at http://127.0.0.1:8001 and local provider agent.
"""

import base64
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

import httpx

from backend.database import (
    create_user,
    get_file_by_id,
    get_file_chunks,
    get_provider_node,
    record_provider_heartbeat,
    upsert_provider_node,
)
from backend.security import create_access_token, hash_password
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager

COORDINATOR_URL = "http://127.0.0.1:8001"


def main():
    print("=" * 70)
    print("Store-Share Phase 3: Live Consumer Upload/Download API Verification")
    print("=" * 70)

    # 1. Check health
    res = httpx.get(f"{COORDINATOR_URL}/health", timeout=5.0)
    assert res.status_code == 200, f"Coordinator health check failed: {res.text}"
    print(f"[*] Coordinator health: {res.json()['status']} ({COORDINATOR_URL})")

    # 2. Provision an authenticated CONSUMER user
    consumer_email = f"live_consumer_{uuid.uuid4().hex[:6]}@storeshare.io"
    consumer_pwd = "ConsumerSecret123!"
    consumer_user = create_user(
        email=consumer_email,
        password_hash=hash_password(consumer_pwd),
        role="CONSUMER",
    )
    consumer_token, _ = create_access_token(user_id=consumer_user["id"], email=consumer_email, role="CONSUMER")
    consumer_headers = {"Authorization": f"Bearer {consumer_token}"}
    print(f"[*] Created Consumer #{consumer_user['id']}: {consumer_email}")

    # 3. Provision an authenticated PROVIDER node
    provider_email = f"live_prov_p3_{uuid.uuid4().hex[:6]}@storeshare.io"
    provider_pwd = "ProviderSecret123!"
    provider_user = create_user(
        email=provider_email,
        password_hash=hash_password(provider_pwd),
        role="PROVIDER",
    )
    provider_token, _ = create_access_token(user_id=provider_user["id"], email=provider_email, role="PROVIDER")
    provider_headers = {"Authorization": f"Bearer {provider_token}"}

    temp_storage_dir = Path(tempfile.mkdtemp(prefix="storeshare_p3_provider_"))
    quota_bytes = 100 * 1024 * 1024  # 100 MB
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota_bytes)
    node = upsert_provider_node(
        provider_id=provider_user["id"],
        quota_bytes=quota_bytes,
        storage_dir=str(temp_storage_dir),
        status="ONLINE",
    )
    record_provider_heartbeat(provider_id=provider_user["id"], used_bytes=0, allocated_bytes=quota_bytes)

    cfg = AgentConfig(
        coordinator_url=COORDINATOR_URL,
        storage_dir=str(temp_storage_dir),
        quota_gb=0.1,
        token=provider_token,
        job_poll_interval=1,
    )
    agent = ProviderAgent(config=cfg, storage_manager=sm)
    hb = agent.send_heartbeat()
    print(f"[*] Provider Node #{node['id']} active (status={hb['status']}) at {temp_storage_dir}")

    # Start background thread for agent polling
    stop_agent_event = threading.Event()

    def _agent_polling_loop():
        while not stop_agent_event.is_set():
            try:
                agent.poll_and_process_jobs_once()
            except Exception:
                pass
            time.sleep(0.5)

    agent_thread = threading.Thread(target=_agent_polling_loop, daemon=True)
    agent_thread.start()

    try:
        # 4. Generate client-side encrypted test payload
        # Plaintext (e.g. 128 bytes) + 16 bytes authentication tag = 144 bytes ciphertext
        plaintext_len = 128
        raw_plaintext = b"A" * plaintext_len
        # Simulated AES-256-GCM ciphertext (cryptographically pseudorandom)
        mock_ciphertext = os.urandom(plaintext_len + 16)
        mock_iv = os.urandom(12).hex()
        mock_sha256 = hashlib.sha256(mock_ciphertext).hexdigest()
        b64_ciphertext = base64.b64encode(mock_ciphertext).decode("utf-8")

        print(f"[*] Prepared encrypted payload: {plaintext_len} bytes plaintext -> {len(mock_ciphertext)} bytes ciphertext.")
        print(f"    IV: {mock_iv} | SHA-256: {mock_sha256[:16]}...")

        # 5. POST /api/consumer/files/upload (JSON mode)
        upload_payload = {
            "filename": "quarterly_financials_2026.pdf",
            "file_size": plaintext_len,
            "iv_hex": mock_iv,
            "sha256_hash": mock_sha256,
            "ciphertext_base64": b64_ciphertext,
        }
        res_up = httpx.post(
            f"{COORDINATOR_URL}/api/consumer/files/upload",
            headers=consumer_headers,
            json=upload_payload,
            timeout=10.0,
        )
        assert res_up.status_code == 200, f"Upload failed: {res_up.text}"
        up_data = res_up.json()
        file_id = up_data["file_id"]
        assert up_data["status"] == "UPLOADING", f"Expected UPLOADING, got {up_data['status']}"
        print(f"[OK] POST /api/consumer/files/upload: File #{file_id} queued with initial status: {up_data['status']}.")

        # 6. Wait for provider agent to store chunk and verify status transitions to STORED
        stored_confirmed = False
        for _ in range(20):
            time.sleep(0.5)
            st_res = httpx.get(
                f"{COORDINATOR_URL}/api/consumer/files/{file_id}/status",
                headers=consumer_headers,
                timeout=5.0,
            )
            assert st_res.status_code == 200
            st_data = st_res.json()
            if st_data["status"] == "STORED" and st_data["is_stored"] is True:
                stored_confirmed = True
                break

        assert stored_confirmed, "File failed to transition to STORED status!"
        print(f"[OK] GET /api/consumer/files/{file_id}/status: Confirmed status STORED (is_stored=True).")

        # 7. Physically inspect chunk on provider disk
        chunks = get_file_chunks(file_id)
        assert len(chunks) == 1, "Expected 1 chunk"
        chunk_rec = chunks[0]
        physical_chunk_path = temp_storage_dir / "chunks" / chunk_rec["chunk_id"]
        assert physical_chunk_path.is_file(), f"Physical chunk missing from {physical_chunk_path}!"
        disk_bytes = physical_chunk_path.read_bytes()
        assert disk_bytes == mock_ciphertext, "On-disk ciphertext does not match uploaded bytes!"
        print(f"[OK] Physical Storage: Chunk '{chunk_rec['chunk_id']}' verified on provider disk ({len(disk_bytes)} bytes).")

        # 8. List consumer files
        list_res = httpx.get(f"{COORDINATOR_URL}/api/consumer/files", headers=consumer_headers, timeout=5.0)
        assert list_res.status_code == 200
        user_files = list_res.json()
        assert any(f["file_id"] == file_id for f in user_files), "Uploaded file missing from consumer list!"
        print(f"[OK] GET /api/consumer/files: File listed in consumer inventory ({len(user_files)} total files).")

        # 9. GET /api/consumer/files/{file_id}/download
        dl_res = httpx.get(
            f"{COORDINATOR_URL}/api/consumer/files/{file_id}/download",
            headers=consumer_headers,
            timeout=15.0,
        )
        assert dl_res.status_code == 200, f"Download failed: {dl_res.text}"
        downloaded_ciphertext = dl_res.content
        assert downloaded_ciphertext == mock_ciphertext, "Downloaded ciphertext does not match original payload!"
        assert dl_res.headers.get("X-IV-Hex") == mock_iv
        assert dl_res.headers.get("X-SHA256-Checksum") == mock_sha256
        assert dl_res.headers.get("X-Plaintext-Size") == str(plaintext_len)
        print(f"[OK] GET /api/consumer/files/{file_id}/download: Received {len(downloaded_ciphertext)} bytes ciphertext.")
        print(f"     Fidelity verified: byte-for-byte SHA-256 match with original client ciphertext.")

        # 10. DELETE /api/consumer/files/{file_id}
        del_res = httpx.delete(
            f"{COORDINATOR_URL}/api/consumer/files/{file_id}",
            headers=consumer_headers,
            timeout=15.0,
        )
        assert del_res.status_code == 200, f"Delete failed: {del_res.text}"
        assert del_res.json()["status"] == "DELETED"
        print(f"[OK] DELETE /api/consumer/files/{file_id}: Coordinator confirmed deletion.")

        # Verify physical file removed from disk
        assert not physical_chunk_path.exists(), "Chunk still exists on provider disk after deletion!"
        print(f"[OK] Physical Storage: Confirmed chunk '{chunk_rec['chunk_id']}' deleted from provider disk.")

        # Verify excluded from files list
        list_after = httpx.get(f"{COORDINATOR_URL}/api/consumer/files", headers=consumer_headers, timeout=5.0)
        assert not any(f["file_id"] == file_id for f in list_after.json()), "Deleted file still returned in list!"
        print(f"[OK] GET /api/consumer/files: Deleted file excluded from list.")

        print("=" * 70)
        print("[SUCCESS] All Phase 3 Consumer APIs verified end-to-end on localhost!")
        print("=" * 70)

    finally:
        stop_agent_event.set()
        agent_thread.join(timeout=2.0)
        agent.close()
        shutil.rmtree(temp_storage_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
