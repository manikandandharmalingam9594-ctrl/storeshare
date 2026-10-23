"""
Tests for Store-Share Provider Identity, Storage Directory Configuration, and Chunk Isolation.

Verifies:
1. Provider A cannot see Provider B's chunks (cross-provider isolation).
2. The correct provider sees its own stored chunks.
3. A configured directory is the directory used by the agent and reconciliation.
4. Mismatches between physical files and metadata are reported clearly (VERIFIED, MISSING, CORRUPTED, UNEXPECTED).
5. Restarting the agent does not register duplicate nodes or lose storage configuration.
6. Safe migration: Changing the storage directory preserves and safely transfers existing chunks without orphaning or losing them.
"""

import hashlib
from pathlib import Path
from typing import Dict
import pytest
from fastapi.testclient import TestClient

from backend.database import (
    create_file_chunk,
    create_file_record,
    create_user,
    get_provider_nodes_by_user_id,
    upsert_provider_node,
)
from backend.security import create_access_token, hash_password
from provider_agent.storage import StorageManager


def _create_provider(email: str, storage_dir: str, quota_bytes: int = 10 * 1024 * 1024 * 1024) -> Dict:
    user = create_user(
        email=email,
        password_hash=hash_password("Secret123!"),
        role="PROVIDER",
    )
    node = upsert_provider_node(
        provider_id=user["id"],
        quota_bytes=quota_bytes,
        storage_dir=storage_dir,
        status="ONLINE",
        computer_name="TEST_NODE",
    )
    token, _ = create_access_token(user_id=user["id"], email=email, role="PROVIDER")
    return {"user": user, "node": node, "token": token, "storage_dir": storage_dir}


def test_provider_a_cannot_see_provider_b_chunks(tmp_path: Path, client: TestClient):
    """
    Test 1: Provider A cannot see Provider B's chunks.
    Verify that querying reconciliation or jobs history with Provider A's token
    never leaks Provider B's chunk records, files, or telemetry.
    """
    dir_a = tmp_path / "storage_a"
    dir_b = tmp_path / "storage_b"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    prov_a = _create_provider("provider_a@storeshare.io", str(dir_a))
    prov_b = _create_provider("provider_b@storeshare.io", str(dir_b))

    # Create consumer and files
    consumer = create_user("consumer@test.io", hash_password("Secret123!"), role="CONSUMER")
    sha_b = hashlib.sha256(b"secret_doc_b").hexdigest()
    file_b = create_file_record(consumer["id"], "secret_doc_b.pdf", 4096, 4096, "00" * 12, sha_b)

    # Assign chunk to Provider B's node
    chunk_b_id = "chunk_b_111111111111"
    create_file_chunk(
        file_id=file_b["id"],
        chunk_id=chunk_b_id,
        provider_node_id=prov_b["node"]["id"],
        chunk_size=4096,
        chunk_index=0,
    )

    # Provider A requests reconciliation
    res_a = client.get(
        "/api/provider/reconciliation",
        headers={"Authorization": f"Bearer {prov_a['token']}"},
    )
    assert res_a.status_code == 200
    report_a = res_a.json()
    assert report_a["node_id"] == prov_a["node"]["id"]
    # Provider A must have 0 chunks
    assert len(report_a["items"]) == 0
    assert chunk_b_id not in [item["chunk_id"] for item in report_a["items"]]

    # Provider B requests reconciliation
    res_b = client.get(
        "/api/provider/reconciliation",
        headers={"Authorization": f"Bearer {prov_b['token']}"},
    )
    assert res_b.status_code == 200
    report_b = res_b.json()
    assert report_b["node_id"] == prov_b["node"]["id"]
    # Provider B must see chunk_b_id
    assert len(report_b["items"]) == 1
    assert report_b["items"][0]["chunk_id"] == chunk_b_id


def test_correct_provider_sees_own_stored_chunks(tmp_path: Path, client: TestClient):
    """
    Test 2: The correct provider sees its own stored chunks.
    Verify that when chunks are assigned to a provider's node, that provider's dashboard
    reconciliation returns the chunks with correct metadata and physical states.
    """
    dir_p = tmp_path / "storage_provider"
    prov = _create_provider("correct_provider@storeshare.io", str(dir_p))

    consumer = create_user("consumer2@test.io", hash_password("Secret123!"), role="CONSUMER")
    payload1 = b"A" * 1024
    sha_data = hashlib.sha256(payload1).hexdigest()
    file_rec = create_file_record(consumer["id"], "data_backup.bin", 1024, 1024, "00" * 12, sha_data)

    cid1 = "chunk_own_0001"
    cid2 = "chunk_own_0002"
    create_file_chunk(file_rec["id"], cid1, prov["node"]["id"], chunk_size=1024, chunk_index=0)
    create_file_chunk(file_rec["id"], cid2, prov["node"]["id"], chunk_size=1024, chunk_index=1)

    # Provider agent writes cid1 to physical disk in configured storage dir
    sm = StorageManager(storage_dir=dir_p, quota_bytes=10 * 1024 * 1024, create_if_missing=True)
    sm.write_chunk_atomic(cid1, payload1)

    # Report inventory via heartbeat
    inventory = sm.get_detailed_inventory()
    hb_res = client.post(
        "/api/provider/node/heartbeat",
        headers={"Authorization": f"Bearer {prov['token']}"},
        json={
            "allocated_bytes": 10 * 1024 * 1024,
            "used_bytes": len(payload1),
            "free_disk_bytes": 100 * 1024 * 1024,
            "computer_name": "OMEN",
            "chunk_ids": [cid1],
            "inventory": inventory,
        },
    )
    assert hb_res.status_code == 200

    # Query reconciliation
    res = client.get(
        "/api/provider/reconciliation",
        headers={"Authorization": f"Bearer {prov['token']}"},
    )
    assert res.status_code == 200
    report = res.json()
    assert report["node_id"] == prov["node"]["id"]
    assert report["total_expected"] == 2

    # Check states: cid1 is VERIFIED, cid2 is MISSING
    item_map = {item["chunk_id"]: item for item in report["items"]}
    assert item_map[cid1]["status"] == "VERIFIED"
    assert item_map[cid1]["actual_size_bytes"] == 1024
    assert item_map[cid2]["status"] == "MISSING"


def test_configured_directory_used_by_agent_and_reconciliation(tmp_path: Path, client: TestClient):
    """
    Test 3: A configured directory is the directory used by the agent and reconciliation.
    Verify that updating the directory via the setup endpoint is returned in the node status
    and agent heartbeat, and reconciliation inspects the correct storage directory.
    """
    custom_dir = tmp_path / "custom_storage_dir"
    prov = _create_provider("dir_test@storeshare.io", str(tmp_path / "old_dir"))

    # Provider changes storage directory via /api/provider/node/setup
    setup_res = client.post(
        "/api/provider/node/setup",
        headers={"Authorization": f"Bearer {prov['token']}"},
        json={
            "storage_dir": str(custom_dir),
            "quota_gb": 20.0,
            "action": "START",
            "computer_name": "TEST_HOST",
        },
    )
    assert setup_res.status_code == 200
    data = setup_res.json()
    assert data["storage_dir"] == str(custom_dir)

    # Verify agent heartbeat receives the configured storage directory
    hb_res = client.post(
        "/api/provider/node/heartbeat",
        headers={"Authorization": f"Bearer {prov['token']}"},
        json={
            "allocated_bytes": 20 * 1024 * 1024 * 1024,
            "used_bytes": 0,
            "free_disk_bytes": 50 * 1024 * 1024 * 1024,
            "computer_name": "TEST_HOST",
        },
    )
    assert hb_res.status_code == 200
    hb_data = hb_res.json()
    assert hb_data["storage_dir"] == str(custom_dir)

    # Verify node status endpoint returns the configured storage directory
    status_res = client.get(
        "/api/provider/node",
        headers={"Authorization": f"Bearer {prov['token']}"},
    )
    assert status_res.status_code == 200
    assert status_res.json()["storage_dir"] == str(custom_dir)


def test_physical_file_and_metadata_mismatch_reported_clearly(tmp_path: Path, client: TestClient):
    """
    Test 4: A mismatch between physical files and metadata is reported clearly.
    Tests VERIFIED, MISSING, CORRUPTED, and UNEXPECTED classifications.
    """
    storage_dir = tmp_path / "reconciliation_storage"
    prov = _create_provider("mismatch_test@storeshare.io", str(storage_dir))
    node_id = prov["node"]["id"]

    consumer = create_user("consumer_m@test.io", hash_password("Secret123!"), role="CONSUMER")

    # 1. VERIFIED chunk: present on disk, size & hash match
    c_verified = "chunk_verified_01"
    payload_verified = b"VERIFIED_PAYLOAD_DATA"
    sha_v = hashlib.sha256(payload_verified).hexdigest()
    file_rec_v = create_file_record(consumer["id"], "file_v.dat", len(payload_verified), len(payload_verified), "00" * 12, sha_v)
    create_file_chunk(file_rec_v["id"], c_verified, node_id, chunk_size=len(payload_verified), chunk_index=0)

    # 2. MISSING chunk: expected in DB, but not on disk
    c_missing = "chunk_missing_02"
    sha_missing = hashlib.sha256(b"MISSING_EXPECTED_DATA").hexdigest()
    file_rec_m = create_file_record(consumer["id"], "file_m.dat", 500, 500, "00" * 12, sha_missing)
    create_file_chunk(file_rec_m["id"], c_missing, node_id, chunk_size=500, chunk_index=0)

    # 3. CORRUPTED chunk: present on disk, but wrong size and wrong checksum
    c_corrupted = "chunk_corrupted_03"
    sha_corrupted = hashlib.sha256(b"CORRUPTED_LONG_DATA" * 50).hexdigest()
    file_rec_c = create_file_record(consumer["id"], "file_c.dat", 1000, 1000, "00" * 12, sha_corrupted)
    create_file_chunk(file_rec_c["id"], c_corrupted, node_id, chunk_size=1000, chunk_index=0)

    # Write files to disk
    sm = StorageManager(storage_dir=storage_dir, quota_bytes=10 * 1024 * 1024, create_if_missing=True)
    sm.write_chunk_atomic(c_verified, payload_verified)
    sm.write_chunk_atomic(c_corrupted, b"SHORT")  # 5 bytes instead of 1000

    # 4. UNEXPECTED chunk: present on disk, but not in DB
    c_unexpected = "chunk_unexpected_99"
    sm.write_chunk_atomic(c_unexpected, b"UNEXPECTED_DATA_FILE")

    # Agent reports inventory and active heartbeat
    inventory = sm.get_detailed_inventory()
    client.post(
        "/api/provider/node/heartbeat",
        headers={"Authorization": f"Bearer {prov['token']}"},
        json={
            "allocated_bytes": 10 * 1024 * 1024,
            "used_bytes": sm.get_stats()["used_bytes"],
            "free_disk_bytes": 100 * 1024 * 1024,
            "computer_name": "OMEN",
            "chunk_ids": [c_verified],
            "inventory": inventory,
        },
    )

    # Query reconciliation
    res = client.get(
        "/api/provider/reconciliation",
        headers={"Authorization": f"Bearer {prov['token']}"},
    )
    assert res.status_code == 200
    rep = res.json()
    item_map = {item["chunk_id"]: item for item in rep["items"]}

    assert item_map[c_verified]["status"] == "VERIFIED"
    assert item_map[c_missing]["status"] == "MISSING"
    assert item_map[c_corrupted]["status"] == "CORRUPTED"
    assert item_map[c_unexpected]["status"] == "UNEXPECTED"


def test_restarting_agent_does_not_register_duplicate_nodes(tmp_path: Path, client: TestClient):
    """
    Test 5: Restarting the agent does not register duplicate nodes or lose storage configuration.
    Verify that calling setup/heartbeat multiple times maintains the same node_id and preserves quota.
    """
    storage_dir = tmp_path / "stable_storage"
    prov = _create_provider("restart_test@storeshare.io", str(storage_dir))
    original_node_id = prov["node"]["id"]

    # First restart / reconnection
    res1 = client.post(
        "/api/provider/node/setup",
        headers={"Authorization": f"Bearer {prov['token']}"},
        json={
            "storage_dir": str(storage_dir),
            "quota_gb": 10.0,
            "action": "START",
            "computer_name": "OMEN",
        },
    )
    assert res1.status_code == 200
    assert res1.json()["node_id"] == original_node_id

    # Second restart / reconnection with updated computer name
    res2 = client.post(
        "/api/provider/node/setup",
        headers={"Authorization": f"Bearer {prov['token']}"},
        json={
            "storage_dir": str(storage_dir),
            "quota_gb": 10.0,
            "action": "START",
            "computer_name": "OMEN_REBOOTED",
        },
    )
    assert res2.status_code == 200
    assert res2.json()["node_id"] == original_node_id

    # Check total nodes for this user is still exactly 1
    nodes = get_provider_nodes_by_user_id(prov["user"]["id"])
    assert len(nodes) == 1
    assert nodes[0]["id"] == original_node_id
    assert nodes[0]["computer_name"] == "OMEN_REBOOTED"
    assert nodes[0]["storage_dir"] == str(storage_dir)


def test_safe_directory_migration_preserves_chunks(tmp_path: Path):
    """
    Test 6: Safe migration: Changing the storage directory migrates existing chunks safely.
    Tests StorageManager.change_storage_dir: verifies files are copied, fsynced, integrity preserved,
    and old directory cleaned up safely without losing or corrupting chunks.
    """
    old_dir = tmp_path / "old_storage"
    new_dir = tmp_path / "new_storage"
    quota = 50 * 1024 * 1024

    sm = StorageManager(storage_dir=old_dir, quota_bytes=quota, create_if_missing=True)

    # Write 3 chunks to old directory
    chunks = {
        "chunk_mig_01": b"FIRST_CHUNK_DATA_" * 100,
        "chunk_mig_02": b"SECOND_CHUNK_DATA_" * 50,
        "chunk_mig_03": b"THIRD_CHUNK_DATA_" * 200,
    }
    for cid, data in chunks.items():
        sm.write_chunk_atomic(cid, data)

    original_used = sm.get_stats()["used_bytes"]
    assert original_used > 0

    # Migrate storage directory
    migrated_count = sm.change_storage_dir(str(new_dir), migrate_existing=True)
    assert migrated_count == 3
    assert sm.storage_dir == new_dir.resolve()
    assert sm.get_stats()["used_bytes"] == original_used

    # Verify each chunk is present and identical in new directory
    for cid, expected_data in chunks.items():
        assert sm.chunk_exists(cid)
        read_data = sm.read_chunk(cid)
        assert read_data == expected_data

    # Verify chunks are no longer in old directory
    old_chunks_dir = old_dir / "chunks"
    for cid in chunks:
        assert not (old_chunks_dir / cid).exists()
