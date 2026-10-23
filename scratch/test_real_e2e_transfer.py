"""
End-to-End Live Transfer Verification Script
Tests real consumer upload, provider storage allocation, and retrieval with AES-256-GCM.
"""

import base64
import hashlib
import os
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import httpx

COORDINATOR_URL = "http://127.0.0.1:8001"

def main():
    print("--- 1. Authenticating Consumer ---")
    consumer_email = f"e2e_tester_{int(time.time())}@storeshare.io"
    consumer_pw = "ConsumerPass123!"

    client = httpx.Client(base_url=COORDINATOR_URL, timeout=30.0)
    # Register new consumer
    reg_res = client.post("/api/auth/register", json={"email": consumer_email, "password": consumer_pw})
    if reg_res.status_code not in (200, 201):
        raise RuntimeError(f"Registration failed: {reg_res.text}")

    auth_res = client.post("/api/auth/login", json={"email": consumer_email, "password": consumer_pw})
    if auth_res.status_code != 200:
        raise RuntimeError(f"Login failed: {auth_res.text}")
    consumer_token = auth_res.json()["access_token"]
    consumer_headers = {"Authorization": f"Bearer {consumer_token}"}
    print(f"Consumer {consumer_email} registered and authenticated successfully.")

    print("\n--- 2. Client-side AES-256-GCM Encryption ---")
    raw_plaintext = b"StoreShare Cross-Computer E2E Verification Payload - Physical Disk Write Check!"
    key = AESGCM.generate_key(bit_length=256)
    iv = os.urandom(12)  # 96-bit IV
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, raw_plaintext, None)

    iv_hex = iv.hex()
    sha256_hash = hashlib.sha256(ciphertext).hexdigest()
    print(f"Plaintext size: {len(raw_plaintext)} bytes")
    print(f"Ciphertext size: {len(ciphertext)} bytes")
    print(f"Ciphertext SHA-256: {sha256_hash}")
    print(f"IV (hex): {iv_hex}")

    print("\n--- 3. Uploading Encrypted File to Coordinator ---")
    upload_res = client.post(
        "/api/consumer/files/upload",
        headers=consumer_headers,
        json={
            "filename": "e2e_verification_test.txt",
            "file_size": len(raw_plaintext),
            "iv_hex": iv_hex,
            "sha256_hash": sha256_hash,
            "ciphertext_base64": base64.b64encode(ciphertext).decode("utf-8"),
        },
    )
    if upload_res.status_code != 200:
        raise RuntimeError(f"Upload failed: {upload_res.text}")

    upload_data = upload_res.json()
    file_id = upload_data["file_id"]
    job_id = upload_data["job_id"]
    print(f"Uploaded File #{file_id}, created Job #{job_id}. Status: {upload_data['status']}")

    print("\n--- 4. Awaiting Provider Agent to Process STORE_CHUNK Job ---")
    # Poll file status until STORED
    max_wait = 15
    start_t = time.time()
    stored = False
    while time.time() - start_t < max_wait:
        status_res = client.get(f"/api/consumer/files/{file_id}/status", headers=consumer_headers)
        if status_res.status_code == 200:
            st = status_res.json()
            print(f"Current file status: {st['status']}")
            if st["status"] == "STORED":
                stored = True
                break
        time.sleep(1.0)

    if not stored:
        raise RuntimeError(f"File #{file_id} was not STORED within {max_wait}s.")
    print("SUCCESS: File confirmed STORED on provider node!")

    print("\n--- 5. Downloading Encrypted File from Provider through Coordinator ---")
    dl_res = client.get(
        f"/api/consumer/files/{file_id}/download",
        headers={"Origin": "http://localhost:3000", **consumer_headers},
    )
    if dl_res.status_code != 200:
        raise RuntimeError(f"Download failed: {dl_res.text}")

    dl_ciphertext = dl_res.content
    print(f"Received downloaded ciphertext: {len(dl_ciphertext)} bytes")
    print(f"Download response headers:")
    for h in ["X-IV-Hex", "X-SHA256-Checksum", "X-Plaintext-Size", "X-Encrypted-Size", "Access-Control-Expose-Headers"]:
        print(f"  {h}: {dl_res.headers.get(h)}")

    print("\n--- 6. Client-Side Decryption & Integrity Verification ---")
    dl_iv_hex = dl_res.headers.get("X-IV-Hex")
    assert dl_iv_hex == iv_hex, f"IV mismatch: {dl_iv_hex} != {iv_hex}"

    dl_sha = hashlib.sha256(dl_ciphertext).hexdigest()
    assert dl_sha == sha256_hash, f"SHA-256 mismatch: {dl_sha} != {sha256_hash}"

    # Decrypt with the consumer key
    decrypted = aesgcm.decrypt(bytes.fromhex(dl_iv_hex), dl_ciphertext, None)
    assert decrypted == raw_plaintext, "Decrypted plaintext does not match original!"
    print(f"SUCCESS: Decrypted plaintext: '{decrypted.decode('utf-8')}'")
    print("ALL VERIFICATIONS PASSED 100%!")

if __name__ == "__main__":
    main()
