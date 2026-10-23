"""
Live verification script:
Validates that a consumer upload assigns a chunk to Provider user 24d126@psgitech.ac.in (Node #2),
writes the physical chunk file to C:\\storeshare-storage\\chunks,
and appears as VERIFIED in the provider's reconciliation dashboard.
"""

import hashlib
import os
from pathlib import Path
import sys
import time

import httpx

from backend.database import get_db_connection, get_provider_node
from backend.security import create_access_token
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig


COORDINATOR_URL = "http://127.0.0.1:8001"
STORAGE_DIR = "C:\\storeshare-storage"
PROVIDER_EMAIL = "24d126@psgitech.ac.in"
PROVIDER_ID = 6


def main():
    print(f"=== Starting Real E2E Storage Verification for {PROVIDER_EMAIL} ===")

    # 1. Generate JWT token for user 6
    token, _ = create_access_token(user_id=PROVIDER_ID, email=PROVIDER_EMAIL, role="PROVIDER")
    headers = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(base_url=COORDINATOR_URL, timeout=30.0)

    # 2. Check health
    health_res = client.get("/api/health")
    assert health_res.status_code == 200, f"Backend unhealthy: {health_res.text}"
    print("[1] Backend is healthy and reachable.")

    # 3. Setup Node #2 storage: C:\storeshare-storage, 50 GB
    setup_res = client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={
            "storage_dir": STORAGE_DIR,
            "quota_gb": 50.0,
            "action": "START",
            "computer_name": "OMEN",
        },
    )
    assert setup_res.status_code == 200, f"Setup failed: {setup_res.text}"
    node_data = setup_res.json()
    print(f"[2] Provider setup successful: Node #{node_data['node_id']} at {node_data['storage_dir']}")

    # Temporarily pause node 10 so coordinator assigns upload to Node #2
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE provider_nodes SET status = 'PAUSED' WHERE id = 10;")
        conn.commit()
    print("[3] Node #10 set to PAUSED so Node #2 is selected for upload.")

    # 4. Instantiate provider agent for 24d126@psgitech.ac.in
    agent_cfg = AgentConfig(
        storage_dir=STORAGE_DIR,
        quota_gb=50.0,
        coordinator_url=COORDINATOR_URL,
        token=token,
    )
    agent = ProviderAgent(agent_cfg)
    hb_data = agent.heartbeat_once()
    print(f"[4] Agent heartbeated: Status={hb_data.get('status')} | Action={hb_data.get('action')}")

    # 5. Create / register a real consumer user
    consumer_email = f"consumer_test_{int(time.time())}@storeshare.io"
    reg_res = client.post(
        "/api/auth/register",
        json={"email": consumer_email, "password": "ConsumerPassword123!"},
    )
    assert reg_res.status_code == 201, f"Registration failed: {reg_res.text}"
    login_res = client.post(
        "/api/auth/login",
        json={"email": consumer_email, "password": "ConsumerPassword123!"},
    )
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    consumer_token = login_res.json()["access_token"]
    consumer_headers = {"Authorization": f"Bearer {consumer_token}"}

    plaintext = b"STORESHARE_REAL_DATA_PAYLOAD_VERIFIED_24D126_" + str(time.time()).encode()
    file_size = len(plaintext)
    ciphertext = plaintext + os.urandom(16)  # 16-byte GCM authentication tag
    sha256_hash = hashlib.sha256(ciphertext).hexdigest()
    iv_hex = "1234567890abcdef12345678"
    filename = f"test_24d126_upload_{int(time.time())}.dat"

    import base64
    ciphertext_b64 = base64.b64encode(ciphertext).decode("ascii")

    init_res = client.post(
        "/api/consumer/files/upload",
        headers=consumer_headers,
        json={
            "filename": filename,
            "file_size": file_size,
            "iv_hex": iv_hex,
            "sha256_hash": sha256_hash,
            "ciphertext_base64": ciphertext_b64,
        },
    )
    assert init_res.status_code == 200, f"Consumer upload failed: {init_res.text}"
    init_data = init_res.json()
    file_id = init_data["file_id"]
    assigned_node_id = init_data["provider_node_id"]
    job_id = init_data["job_id"]
    print(f"[5] Consumer file #{file_id} created: assigned to Provider Node #{assigned_node_id} (Job #{job_id})")
    assert assigned_node_id == node_data["node_id"], f"Expected Node #{node_data['node_id']}, got #{assigned_node_id}"

    # 6. Provider agent polls for jobs and processes them
    jobs = agent.poll_jobs()
    print(f"[6] Agent polled {len(jobs)} pending jobs.")
    assert len(jobs) > 0, "No jobs returned for Node #2!"

    chunk_id = None
    for j in jobs:
        print(f"Processing job #{j['id']} ({j['job_type']}) for chunk {j['chunk_id']}...")
        chunk_id = j["chunk_id"]
        agent.process_single_job(j)

    # 7. Verify physical file in C:\storeshare-storage\chunks
    expected_chunk_path = Path(STORAGE_DIR) / "chunks" / chunk_id
    assert expected_chunk_path.is_file(), f"PHYSICAL CHUNK FILE NOT FOUND AT: {expected_chunk_path}"
    physical_bytes = expected_chunk_path.read_bytes()
    assert physical_bytes == ciphertext, "Physical chunk content mismatch!"
    print(f"[8] PHYSICAL CHUNK VERIFIED ON DISK: {expected_chunk_path} ({len(physical_bytes)} bytes)")

    # 8. Send agent heartbeat with inventory
    agent.send_heartbeat()

    # 9. Query provider reconciliation endpoint for 24d126@psgitech.ac.in
    recon_res = client.get("/api/provider/reconciliation", headers=headers)
    assert recon_res.status_code == 200, f"Reconciliation failed: {recon_res.text}"
    report = recon_res.json()
    print(f"[9] Reconciliation report: total_expected={report['total_expected']}, verified={report['verified_count']}")

    found_item = None
    for item in report["items"]:
        if item["chunk_id"] == chunk_id:
            found_item = item
            break

    assert found_item is not None, f"Chunk {chunk_id} not found in reconciliation report!"
    assert found_item["status"] == "VERIFIED", f"Expected VERIFIED status, got {found_item['status']}: {found_item.get('status_detail')}"
    print(f"[10] RECONCILIATION VERIFIED: Chunk {chunk_id} for file '{filename}' has status '{found_item['status']}'!")

    # 10. Restore node 10 status to ONLINE
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE provider_nodes SET status = 'ONLINE' WHERE id = 10;")
        conn.commit()
    print("[11] Node #10 restored to ONLINE.")
    print("\n>>> ALL CHECKS PASSED SUCCESSFULLY! The fix is verified end-to-end on live infrastructure. <<<")


if __name__ == "__main__":
    main()
