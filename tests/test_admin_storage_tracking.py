"""
Comprehensive Test Suite for Admin File Storage Tracking Feature.

Covers:
1. Server-side role-based authorization:
   - Anonymous request: HTTP 401 Unauthorized
   - Consumer role: HTTP 403 Forbidden
   - Provider role: HTTP 403 Forbidden
   - Admin role: HTTP 200 OK
2. File-owner, chunk, and provider joins:
   - Validates consumer identity, file metadata, provider email, computer hostname, quota, and telemetry.
3. Multi-chunk distribution:
   - File with multiple chunks distributed across distinct provider nodes.
4. Physical presence verification state machine:
   - Provider ONLINE + chunk reported in inventory -> VERIFIED_PHYSICAL
   - Provider OFFLINE (heartbeat expired) -> PROVIDER_OFFLINE (unverified)
   - Provider ONLINE + chunk missing from inventory -> PHYSICALLY_MISSING
   - File DELETED -> DELETED
   - Storage job FAILED -> STORAGE_FAILED
   - Chunk PENDING / Job QUEUED -> DATABASE_ASSIGNED
5. Provider heartbeat telemetry & physical verification update.
6. Real PostgreSQL integration verification.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.database import (
    complete_chunk_job,
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    create_user,
    fail_chunk_job,
    get_admin_file_storage_records,
    get_provider_node,
    init_db,
    record_provider_heartbeat,
    update_chunk_status,
    update_chunk_verification,
    update_file_status,
    upsert_provider_node,
)
from backend.security import create_access_token, hash_password


def _auth_headers(user: dict) -> dict:
    token, _ = create_access_token(user["id"], user["email"], user["role"])
    return {"Authorization": f"Bearer {token}"}


class TestAdminStorageTrackingAuthorization:
    """
    Test server-side authorization enforcement on GET /api/admin/files/storage.
    """

    def test_anonymous_request_is_rejected_with_401(self, client: TestClient):
        res = client.get("/api/admin/files/storage")
        assert res.status_code == 401
        assert "not provided" in res.json().get("detail", "").lower() or "credentials" in res.json().get("detail", "").lower()

    def test_consumer_role_is_forbidden_with_403(self, client: TestClient):
        consumer = create_user("consumer@test.io", hash_password("Password123!"), role="CONSUMER")
        res = client.get("/api/admin/files/storage", headers=_auth_headers(consumer))
        assert res.status_code == 403
        assert "ADMIN" in res.json().get("detail", "")

    def test_provider_role_is_forbidden_with_403(self, client: TestClient):
        provider = create_user("provider@test.io", hash_password("Password123!"), role="PROVIDER")
        res = client.get("/api/admin/files/storage", headers=_auth_headers(provider))
        assert res.status_code == 403
        assert "ADMIN" in res.json().get("detail", "")

    def test_admin_role_is_authorized_with_200(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestAdminStorageTrackingDataJoins:
    """
    Test metadata joins from files, users, provider_nodes, file_chunks, and chunk_jobs.
    """

    def test_single_chunk_file_tracking_joins(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("alice@client.io", hash_password("Password123!"), role="CONSUMER")
        provider = create_user("bob@datacenter.net", hash_password("Password123!"), role="PROVIDER")

        # Set up provider node with hostname
        node = upsert_provider_node(
            provider_id=provider["id"],
            quota_bytes=50 * 1024 * 1024 * 1024,
            storage_dir="/var/storage",
            status="ONLINE",
            computer_name="WORKSTATION-BOB",
        )
        record_provider_heartbeat(
            provider_id=provider["id"],
            used_bytes=1048576,
            allocated_bytes=50 * 1024 * 1024 * 1024,
            computer_name="WORKSTATION-BOB",
        )

        # Create file
        file_rec = create_file_record(
            owner_id=consumer["id"],
            filename="quarterly_report.pdf",
            file_size=1048560,
            encrypted_size=1048576,
            iv_hex="0123456789abcdef01234567",
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        update_file_status(file_rec["id"], "STORED")

        # Create chunk
        chunk = create_file_chunk(
            file_id=file_rec["id"],
            chunk_id="chunk_test_report_001",
            provider_node_id=node["id"],
            chunk_size=1048576,
            chunk_index=0,
        )
        update_chunk_status("chunk_test_report_001", "STORED")

        # Create store chunk job
        job = create_chunk_job(
            provider_node_id=node["id"],
            chunk_id="chunk_test_report_001",
            file_id=file_rec["id"],
            job_type="STORE_CHUNK",
            timeout_seconds=300,
        )
        complete_chunk_job(job["id"], node["id"])

        # Verify physical chunk presence via inventory
        update_chunk_verification(node["id"], ["chunk_test_report_001"])

        # Fetch via admin API
        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        assert res.status_code == 200
        records = res.json()
        assert len(records) == 1

        rec = records[0]
        assert rec["file_id"] == file_rec["id"]
        assert rec["filename"] == "quarterly_report.pdf"
        assert rec["consumer_id"] == consumer["id"]
        assert rec["consumer_email"] == "alice@client.io"
        assert rec["file_size"] == 1048560
        assert rec["encrypted_size"] == 1048576
        assert rec["lifecycle_status"] == "STORED"
        assert rec["storage_status"] == "VERIFIED_PHYSICAL"
        assert rec["provider_node_id"] == node["id"]
        assert rec["provider_email"] == "bob@datacenter.net"
        assert rec["computer_name"] == "WORKSTATION-BOB"
        assert rec["provider_status"] == "ONLINE"
        assert rec["provider_online"] is True
        assert rec["last_heartbeat"] is not None
        assert rec["last_verified_at"] is not None
        assert rec["latest_store_job"]["status"] == "COMPLETED"
        assert len(rec["chunks"]) == 1

    def test_multi_chunk_file_across_multiple_providers(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("multi@client.io", hash_password("Password123!"), role="CONSUMER")

        # Provider 1
        p1 = create_user("node1@provider.net", hash_password("Password123!"), role="PROVIDER")
        node1 = upsert_provider_node(p1["id"], 10 * 1024**3, computer_name="HOST-NODE-1", status="ONLINE")
        record_provider_heartbeat(p1["id"], 500000, 10 * 1024**3, computer_name="HOST-NODE-1")

        # Provider 2
        p2 = create_user("node2@provider.net", hash_password("Password123!"), role="PROVIDER")
        node2 = upsert_provider_node(p2["id"], 20 * 1024**3, computer_name="HOST-NODE-2", status="ONLINE")
        record_provider_heartbeat(p2["id"], 500000, 20 * 1024**3, computer_name="HOST-NODE-2")

        # File with 2 chunks (< 5MB limit)
        file_rec = create_file_record(
            owner_id=consumer["id"],
            filename="large_archive.tar.gz",
            file_size=2000000,
            encrypted_size=2000032,
            iv_hex="aabbccddeeff001122334455",
            sha256_hash="11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff",
        )
        update_file_status(file_rec["id"], "STORED")

        create_file_chunk(file_rec["id"], "chunk_part_00", node1["id"], 1000016, 0)
        update_chunk_status("chunk_part_00", "STORED")
        j1 = create_chunk_job(node1["id"], "chunk_part_00", file_rec["id"], "STORE_CHUNK", 300)
        complete_chunk_job(j1["id"], node1["id"])
        update_chunk_verification(node1["id"], ["chunk_part_00"])

        create_file_chunk(file_rec["id"], "chunk_part_01", node2["id"], 1000016, 1)
        update_chunk_status("chunk_part_01", "STORED")
        j2 = create_chunk_job(node2["id"], "chunk_part_01", file_rec["id"], "STORE_CHUNK", 300)
        complete_chunk_job(j2["id"], node2["id"])
        update_chunk_verification(node2["id"], ["chunk_part_01"])

        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        assert res.status_code == 200
        records = res.json()
        assert len(records) == 1

        rec = records[0]
        assert len(rec["chunks"]) == 2
        assert rec["chunks"][0]["chunk_index"] == 0
        assert rec["chunks"][0]["provider_node_id"] == node1["id"]
        assert rec["chunks"][0]["computer_name"] == "HOST-NODE-1"
        assert rec["chunks"][0]["storage_status"] == "VERIFIED_PHYSICAL"

        assert rec["chunks"][1]["chunk_index"] == 1
        assert rec["chunks"][1]["provider_node_id"] == node2["id"]
        assert rec["chunks"][1]["computer_name"] == "HOST-NODE-2"
        assert rec["chunks"][1]["storage_status"] == "VERIFIED_PHYSICAL"

        assert rec["storage_status"] == "VERIFIED_PHYSICAL"


class TestPhysicalPresenceVerificationStates:
    """
    Test the 5 physical storage verification states.
    """

    def test_provider_offline_marks_physical_presence_unverified(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("user@test.io", hash_password("Password123!"), role="CONSUMER")
        provider = create_user("offline_prov@test.io", hash_password("Password123!"), role="PROVIDER")

        # Provider has not sent heartbeat recently (set to OFFLINE)
        node = upsert_provider_node(provider["id"], 10 * 1024**3, computer_name="OFFLINE-DESKTOP", status="OFFLINE")

        file_rec = create_file_record(
            consumer["id"], "offline_test.pdf", 100, 116, "0123456789abcdef01234567", "a" * 64
        )
        update_file_status(file_rec["id"], "STORED")
        create_file_chunk(file_rec["id"], "chunk_offline_01", node["id"], 116, 0)
        update_chunk_status("chunk_offline_01", "STORED")
        j = create_chunk_job(node["id"], "chunk_offline_01", file_rec["id"], "STORE_CHUNK", 300)
        complete_chunk_job(j["id"], node["id"])

        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        rec = res.json()[0]

        assert rec["storage_status"] == "PROVIDER_OFFLINE"
        assert rec["provider_online"] is False
        assert "offline" in rec["storage_status_detail"].lower()

    def test_provider_online_but_chunk_missing_from_disk_inventory(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("user@test.io", hash_password("Password123!"), role="CONSUMER")
        provider = create_user("online_prov@test.io", hash_password("Password123!"), role="PROVIDER")

        # Provider is ONLINE
        node = upsert_provider_node(provider["id"], 10 * 1024**3, computer_name="ONLINE-BOX", status="ONLINE")
        record_provider_heartbeat(provider["id"], 116, 10 * 1024**3, computer_name="ONLINE-BOX")

        file_rec = create_file_record(
            consumer["id"], "missing_chunk.pdf", 100, 116, "0123456789abcdef01234567", "b" * 64
        )
        update_file_status(file_rec["id"], "STORED")
        create_file_chunk(file_rec["id"], "chunk_ghost_01", node["id"], 116, 0)
        update_chunk_status("chunk_ghost_01", "STORED")
        j = create_chunk_job(node["id"], "chunk_ghost_01", file_rec["id"], "STORE_CHUNK", 300)
        complete_chunk_job(j["id"], node["id"])

        # Notice: update_chunk_verification is NEVER called for chunk_ghost_01!
        # Provider reported other chunks or none
        update_chunk_verification(node["id"], ["other_unrelated_chunk"])

        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        rec = res.json()[0]

        assert rec["storage_status"] == "PHYSICALLY_MISSING"
        assert "missing" in rec["storage_status_detail"].lower()

    def test_deleted_file_is_classified_as_deleted(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("user@test.io", hash_password("Password123!"), role="CONSUMER")
        provider = create_user("del_prov@test.io", hash_password("Password123!"), role="PROVIDER")
        node = upsert_provider_node(provider["id"], 10 * 1024**3, computer_name="DEL-HOST", status="ONLINE")

        file_rec = create_file_record(
            consumer["id"], "del_doc.pdf", 100, 116, "0123456789abcdef01234567", "c" * 64
        )
        update_file_status(file_rec["id"], "DELETED")
        create_file_chunk(file_rec["id"], "chunk_del_01", node["id"], 116, 0)
        j = create_chunk_job(node["id"], "chunk_del_01", file_rec["id"], "DELETE_CHUNK", 300)
        complete_chunk_job(j["id"], node["id"])

        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        rec = res.json()[0]

        assert rec["storage_status"] == "DELETED"
        assert rec["latest_delete_job"]["status"] == "COMPLETED"

    def test_failed_storage_job_is_classified_as_failed(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("user@test.io", hash_password("Password123!"), role="CONSUMER")
        provider = create_user("fail_prov@test.io", hash_password("Password123!"), role="PROVIDER")
        node = upsert_provider_node(provider["id"], 10 * 1024**3, computer_name="FAIL-HOST", status="ONLINE")

        file_rec = create_file_record(
            consumer["id"], "failed_upload.pdf", 100, 116, "0123456789abcdef01234567", "d" * 64
        )
        update_file_status(file_rec["id"], "FAILED")
        create_file_chunk(file_rec["id"], "chunk_fail_01", node["id"], 116, 0)
        j = create_chunk_job(node["id"], "chunk_fail_01", file_rec["id"], "STORE_CHUNK", 300)
        fail_chunk_job(j["id"], node["id"], error_message="Disk I/O error on host")

        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        rec = res.json()[0]

        assert rec["storage_status"] == "STORAGE_FAILED"
        assert rec["latest_store_job"]["status"] == "FAILED"
        assert rec["latest_store_job"]["error_message"] == "Disk I/O error on host"

    def test_database_assigned_when_transfer_is_in_progress(self, client: TestClient):
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        consumer = create_user("user@test.io", hash_password("Password123!"), role="CONSUMER")
        provider = create_user("prog_prov@test.io", hash_password("Password123!"), role="PROVIDER")
        node = upsert_provider_node(provider["id"], 10 * 1024**3, computer_name="PROG-HOST", status="ONLINE")

        file_rec = create_file_record(
            consumer["id"], "uploading.pdf", 100, 116, "0123456789abcdef01234567", "e" * 64
        )
        update_file_status(file_rec["id"], "UPLOADING")
        create_file_chunk(file_rec["id"], "chunk_prog_01", node["id"], 116, 0)
        create_chunk_job(node["id"], "chunk_prog_01", file_rec["id"], "STORE_CHUNK", 300)

        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        rec = res.json()[0]

        assert rec["storage_status"] == "DATABASE_ASSIGNED"
        assert rec["latest_store_job"]["status"] == "QUEUED"


class TestProviderAgentHeartbeatVerification:
    """
    Test the authenticated outbound heartbeat inventory verification mechanism.
    """

    def test_heartbeat_updates_hostname_and_chunk_verification(self, client: TestClient):
        provider = create_user("agent_prov@test.io", hash_password("Password123!"), role="PROVIDER")
        consumer = create_user("consumer_user@test.io", hash_password("Password123!"), role="CONSUMER")

        # Set up node
        setup_payload = {
            "storage_dir": "/storage/data",
            "quota_gb": 10.0,
            "action": "START",
            "computer_name": "AGENT-WORKSTATION-X",
        }
        setup_res = client.post("/api/provider/node/setup", json=setup_payload, headers=_auth_headers(provider))
        assert setup_res.status_code == 200
        assert setup_res.json()["computer_name"] == "AGENT-WORKSTATION-X"

        # Create file and chunk
        f = create_file_record(consumer["id"], "test.pdf", 100, 116, "0123456789abcdef01234567", "f" * 64)
        update_file_status(f["id"], "STORED")
        node_id = get_provider_node(provider["id"])["id"]
        create_file_chunk(f["id"], "chunk_inventory_test_01", node_id, 116, 0)
        update_chunk_status("chunk_inventory_test_01", "STORED")

        # Agent sends heartbeat with chunk inventory
        hb_payload = {
            "allocated_bytes": 10 * 1024**3,
            "used_bytes": 116,
            "free_disk_bytes": 50 * 1024**3,
            "agent_version": "0.1.0",
            "computer_name": "AGENT-WORKSTATION-X",
            "chunk_ids": ["chunk_inventory_test_01"],
        }
        hb_res = client.post("/api/provider/node/heartbeat", json=hb_payload, headers=_auth_headers(provider))
        assert hb_res.status_code == 200

        # Query admin endpoint
        admin = create_user("admin@test.io", hash_password("Password123!"), role="ADMIN")
        res = client.get("/api/admin/files/storage", headers=_auth_headers(admin))
        assert res.status_code == 200
        rec = res.json()[0]

        assert rec["storage_status"] == "VERIFIED_PHYSICAL"
        assert rec["computer_name"] == "AGENT-WORKSTATION-X"
        assert rec["last_verified_at"] is not None


@pytest.mark.postgres_integration
def test_postgres_admin_storage_tracking_live():
    """
    Test against real PostgreSQL database.
    Verifies that real records are returned cleanly without affecting existing rows.
    """
    test_email = f"temp_admin_pg_{uuid.uuid4().hex[:8]}@test.io"
    admin = create_user(test_email, hash_password("Password123!"), role="ADMIN")
    try:
        records = get_admin_file_storage_records()
        assert isinstance(records, list)
        for r in records:
            assert "file_id" in r
            assert "filename" in r
            assert "consumer_id" in r
            assert "lifecycle_status" in r
            assert "storage_status" in r
            assert "chunks" in r
    finally:
        # Cleanup temporary admin test user
        from backend.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM users WHERE email = %s;", (test_email,))
            conn.commit()
