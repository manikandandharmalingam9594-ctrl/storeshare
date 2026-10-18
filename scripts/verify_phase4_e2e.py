"""
End-to-End Live Verification Script for Phase 4:
Consumer Dashboard, Browser-Side Encryption (Web Crypto AES-256-GCM),
Local Key Vault, and Provider Storage Lifecycle.

Demonstrates the required end-to-end workflow:
1. Consumer selects a real file in the browser.
2. The browser encrypts it before upload (AES-256-GCM + 12-byte IV + SHA-256).
3. The provider agent stores the ciphertext physically on disk.
4. The consumer sees the file marked STORED.
5. The consumer downloads and decrypts it in the browser.
6. The SHA-256 hash of the recovered file matches the original file.
7. The consumer deletes the file and the provider's physical chunk is removed.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import httpx

from backend.database import (
    create_user,
    get_file_by_id,
    record_provider_heartbeat,
    upsert_provider_node,
)
from backend.security import create_access_token, hash_password
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager

COORDINATOR_URL = "http://127.0.0.1:8001"


def main():
    print("=" * 75)
    print("Store-Share Phase 4: End-to-End Browser Crypto & Provider Storage Verification")
    print("=" * 75)

    # 1. Coordinator Health
    res = httpx.get(f"{COORDINATOR_URL}/health", timeout=5.0)
    assert res.status_code == 200, f"Coordinator health check failed: {res.text}"
    print(f"[1/8] Coordinator Online: {COORDINATOR_URL} ({res.json()['status']})")

    # 2. Setup Consumer User
    consumer_email = f"consumer_p4_{uuid.uuid4().hex[:6]}@storeshare.io"
    consumer_pwd = "ConsumerPass456!"
    consumer_user = create_user(
        email=consumer_email,
        password_hash=hash_password(consumer_pwd),
        role="CONSUMER",
    )
    consumer_token, _ = create_access_token(user_id=consumer_user["id"], email=consumer_email, role="CONSUMER")
    print(f"[2/8] Provisioned Consumer #{consumer_user['id']}: {consumer_email}")

    # 3. Setup Physical Provider Storage & Agent
    provider_email = f"prov_p4_{uuid.uuid4().hex[:6]}@storeshare.io"
    provider_pwd = "ProviderPass456!"
    provider_user = create_user(
        email=provider_email,
        password_hash=hash_password(provider_pwd),
        role="PROVIDER",
    )
    provider_token, _ = create_access_token(user_id=provider_user["id"], email=provider_email, role="PROVIDER")

    temp_storage_dir = Path(tempfile.mkdtemp(prefix="storeshare_p4_prov_"))
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
    agent.send_heartbeat()
    print(f"[3/8] Provider Node #{node['id']} Active at {temp_storage_dir}")

    stop_agent_event = threading.Event()

    def _agent_loop():
        while not stop_agent_event.is_set():
            try:
                agent.poll_and_process_jobs_once()
            except Exception:
                pass
            time.sleep(0.3)

    agent_thread = threading.Thread(target=_agent_loop, daemon=True)
    agent_thread.start()

    try:
        # 4. Run JS Web Crypto Client (frontend/src/services/crypto.js)
        print("[4/8] Spawning JavaScript Web Crypto client...")
        cmd = [
            "node",
            "scripts/phase4_client_e2e.mjs",
            COORDINATOR_URL,
            consumer_token,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print("ERROR STDOUT:\n", result.stdout)
            print("ERROR STDERR:\n", result.stderr)
            raise RuntimeError(f"Phase 4 JS Client verification failed with code {result.returncode}")

        # Extract completed file_id
        file_id = None
        for line in result.stdout.splitlines():
            if line.startswith("__COMPLETED_FILE_ID__:"):
                file_id = int(line.split(":")[1].strip())

        assert file_id is not None, "Failed to retrieve completed file ID from JS runner!"
        print(f"[5/8] JS Web Crypto pipeline verified: File #{file_id}")

        # 6. Verify coordinator database status is DELETED
        file_rec = get_file_by_id(file_id, owner_id=consumer_user["id"])
        assert file_rec["status"] == "DELETED", f"Expected DELETED, got {file_rec['status']}"
        print(f"[6/8] Backend Status: File #{file_id} marked DELETED.")

        # 7. Verify physical chunk was removed from provider disk (chunks dir should have 0 files)
        chunks_dir = temp_storage_dir / "chunks"
        remaining_chunks = list(chunks_dir.glob("*")) if chunks_dir.exists() else []
        assert len(remaining_chunks) == 0, f"Provider storage still contains {len(remaining_chunks)} chunks: {remaining_chunks}"
        print(f"[7/8] Physical Disk: Provider chunk verified removed from disk ({len(remaining_chunks)} chunks remaining).")

        # 8. Verify consumer file list excludes deleted file
        consumer_headers = {"Authorization": f"Bearer {consumer_token}"}
        list_res = httpx.get(f"{COORDINATOR_URL}/api/consumer/files", headers=consumer_headers, timeout=5.0)
        assert list_res.status_code == 200
        active_ids = [f["file_id"] for f in list_res.json()]
        assert file_id not in active_ids, "Deleted file should not appear in active files list!"
        print(f"[8/8] Consumer Isolation: File #{file_id} excluded from active files list.")

        print("=" * 75)
        print("[SUCCESS] Phase 4 Complete End-to-End Workflow Passed All Verifications!")
        print("  1. Consumer selected real file (110 bytes plaintext)")
        print("  2. Browser encrypted file using Web Crypto AES-256-GCM (12-byte IV, +16B tag)")
        print("  3. Provider agent stored ciphertext physically on disk")
        print("  4. Consumer saw file transition to STORED status")
        print("  5. Consumer downloaded ciphertext and decrypted in browser via local key")
        print("  6. SHA-256 checksum and byte-for-byte content matched original file exactly")
        print("  7. Password-protected key backup export / import recovered decryption keys")
        print("  8. Consumer deleted file and physical chunk was cleanly removed from disk")
        print("=" * 75)

    finally:
        stop_agent_event.set()
        agent_thread.join(timeout=2.0)
        agent.close()
        shutil.rmtree(temp_storage_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
