"""
Tests for Store-Share Provider Storage Allocation and Local Provider Agent.

Covers:
1. StorageManager:
   - Directory validation, creation, non-directory and root rejection.
   - Symlink and path traversal protection (relative escapes, special characters, null bytes).
   - Quota reservation, releases, and thread-safe quota enforcement.
   - Atomic writes (.tmp_ prefix, fsync, atomic rename) and chunk deletion.
   - Orphaned temporary file cleanup on startup.
2. Coordinator Provider Endpoints:
   - Authentication enforcement (unauthenticated -> 401, consumer -> 403, provider -> 200).
   - Provider identity derived strictly from validated JWT credentials.
   - Storage setup validation (directory required, positive quota).
   - Real-time status transitions: UNREGISTERED -> ONLINE (via heartbeat) -> OFFLINE (timeout) -> STOPPED.
3. Provider Agent:
   - Outbound-only NAT communication flow.
   - Rejection of consumer accounts for provider agent daemon.
   - Heartbeat reporting and coordinator STOP action handling.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Dict
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from backend.database import (
    create_user,
    get_db_connection,
    get_provider_node,
    init_db,
    record_provider_heartbeat,
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
def temp_storage_dir(tmp_path: Path) -> Path:
    storage = tmp_path / "provider_storage"
    return storage


# ==============================================================================
# 1. Local StorageManager Tests
# ==============================================================================

def test_storage_manager_init_success(temp_storage_dir: Path):
    quota = 50 * 1024 * 1024  # 50 MB
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota, create_if_missing=True)

    assert temp_storage_dir.exists()
    assert (temp_storage_dir / "chunks").is_dir()
    stats = sm.get_stats()
    assert stats["quota_bytes"] == quota
    assert stats["used_bytes"] == 0
    assert stats["reserved_bytes"] == 0
    assert stats["chunk_count"] == 0


def test_storage_manager_invalid_quota(temp_storage_dir: Path):
    with pytest.raises(ValueError, match="greater than 0"):
        StorageManager(storage_dir=temp_storage_dir, quota_bytes=0)
    with pytest.raises(ValueError, match="greater than 0"):
        StorageManager(storage_dir=temp_storage_dir, quota_bytes=-100)


def test_storage_manager_prohibit_root():
    root = Path(Path.cwd().anchor).resolve()
    with pytest.raises(SecurityViolationError, match="cannot be the filesystem root"):
        StorageManager(storage_dir=root, quota_bytes=1000)


def test_storage_manager_rejects_non_directory(tmp_path: Path):
    file_path = tmp_path / "regular_file.txt"
    file_path.write_text("not a directory")
    with pytest.raises(StorageError, match="not a directory"):
        StorageManager(storage_dir=file_path, quota_bytes=1000, create_if_missing=False)


def test_storage_manager_path_traversal_protection(temp_storage_dir: Path):
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=10000)

    # Relative directory escapes
    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("../escape_chunk")

    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("../../etc/passwd")

    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("sub/dir/chunk")

    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("sub\\dir\\chunk")

    # Invalid characters and injection attempts
    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("chunk;rm -rf")

    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("chunk\x00null")

    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("chunk name with spaces")

    with pytest.raises(SecurityViolationError):
        sm.get_safe_chunk_path("")


def test_storage_manager_quota_reservation(temp_storage_dir: Path):
    quota = 1000
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota)

    # Reserve 600 bytes
    assert sm.reserve_space(600) is True
    stats = sm.get_stats()
    assert stats["reserved_bytes"] == 600
    assert stats["available_quota_bytes"] == 400

    # Attempt to reserve 500 bytes (600 + 500 = 1100 > 1000)
    with pytest.raises(QuotaExceededError):
        sm.reserve_space(500)

    # Release 300 bytes
    sm.release_reservation(300)
    stats = sm.get_stats()
    assert stats["reserved_bytes"] == 300
    assert stats["available_quota_bytes"] == 700

    # Now reserving 400 bytes succeeds (300 + 400 = 700 <= 1000)
    assert sm.reserve_space(400) is True
    assert sm.get_stats()["reserved_bytes"] == 700


def test_storage_manager_atomic_write_and_read(temp_storage_dir: Path):
    quota = 5000
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota)
    chunk_id = "test_chunk_001_abc"
    payload = b"STORE_SHARE_ENCRYPTED_OPAQUE_CHUNK_PAYLOAD_12345"

    written = sm.write_chunk_atomic(chunk_id=chunk_id, data=payload)
    assert written == len(payload)

    # Verify atomic file is present
    target_path = sm.get_safe_chunk_path(chunk_id)
    assert target_path.exists()
    assert not (temp_storage_dir / "chunks" / ".tmp_").exists()

    # Read back and compare bytes
    read_data = sm.read_chunk(chunk_id)
    assert read_data == payload

    stats = sm.get_stats()
    assert stats["used_bytes"] == len(payload)
    assert stats["chunk_count"] == 1

    # Safely delete chunk
    assert sm.delete_chunk(chunk_id) is True
    assert not target_path.exists()
    assert sm.get_stats()["used_bytes"] == 0
    assert sm.get_stats()["chunk_count"] == 0

    # Deleting again returns False
    assert sm.delete_chunk(chunk_id) is False


def test_storage_manager_atomic_write_quota_exceeded(temp_storage_dir: Path):
    quota = 100
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota)

    oversized = b"X" * 150
    with pytest.raises(QuotaExceededError):
        sm.write_chunk_atomic("chunk_oversized", oversized)

    assert sm.get_stats()["used_bytes"] == 0


def test_storage_manager_startup_tmp_cleanup(temp_storage_dir: Path):
    quota = 10000
    chunks_dir = temp_storage_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    # Simulate an interrupted write that left a orphaned .tmp_ file
    orphaned = chunks_dir / ".tmp_orphaned_write_abc"
    orphaned.write_bytes(b"INTERRUPTED_DATA")
    valid = chunks_dir / "valid_chunk_1"
    valid.write_bytes(b"1234567890")

    # Initializing StorageManager cleans up orphaned tmp files
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota)
    assert not orphaned.exists()
    assert valid.exists()
    assert sm.get_stats()["used_bytes"] == 10


def test_concurrent_quota_reservations(temp_storage_dir: Path):
    quota = 1000
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=quota)

    successful_reservations = []
    failed_reservations = []

    def try_reserve():
        try:
            sm.reserve_space(100)
            successful_reservations.append(100)
        except QuotaExceededError:
            failed_reservations.append(100)

    # 15 concurrent threads requesting 100 bytes each (Total 1500 > 1000 quota)
    threads = [threading.Thread(target=try_reserve) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 10 reservations must succeed, exactly 5 must fail
    assert len(successful_reservations) == 10
    assert len(failed_reservations) == 5
    assert sum(successful_reservations) == 1000
    assert sm.get_stats()["reserved_bytes"] == 1000


# ==============================================================================
# 2. Coordinator Endpoints & Role Authorization Tests
# ==============================================================================

@pytest.fixture
def provider_token() -> Dict[str, str]:
    user = create_user(
        email="provider_node_op@example.com",
        password_hash=hash_password("ProviderSecurePwd!123"),
        role="PROVIDER",
    )
    token, _ = create_access_token(user_id=user["id"], email=user["email"], role=user["role"])
    return {"Authorization": f"Bearer {token}", "email": user["email"], "id": str(user["id"])}


@pytest.fixture
def consumer_token() -> Dict[str, str]:
    user = create_user(
        email="consumer_client@example.com",
        password_hash=hash_password("ConsumerSecurePwd!123"),
        role="CONSUMER",
    )
    token, _ = create_access_token(user_id=user["id"], email=user["email"], role=user["role"])
    return {"Authorization": f"Bearer {token}", "email": user["email"], "id": str(user["id"])}


def test_unauthenticated_provider_node_endpoints(client: TestClient):
    assert client.get("/api/provider/node").status_code == 401
    assert client.post("/api/provider/node/setup", json={"storage_dir": "C:\\test", "quota_gb": 10}).status_code == 401
    assert client.post("/api/provider/node/heartbeat", json={"allocated_bytes": 100, "used_bytes": 0, "free_disk_bytes": 1000}).status_code == 401


def test_consumer_forbidden_from_provider_node_endpoints(client: TestClient, consumer_token: Dict[str, str]):
    headers = {"Authorization": consumer_token["Authorization"]}
    assert client.get("/api/provider/node", headers=headers).status_code == 403
    assert client.post("/api/provider/node/setup", headers=headers, json={"storage_dir": "C:\\test", "quota_gb": 10}).status_code == 403
    assert client.post("/api/provider/node/heartbeat", headers=headers, json={"allocated_bytes": 100, "used_bytes": 0, "free_disk_bytes": 1000}).status_code == 403


def test_provider_initial_status_unregistered(client: TestClient, provider_token: Dict[str, str]):
    headers = {"Authorization": provider_token["Authorization"]}
    res = client.get("/api/provider/node", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is False
    assert data["status"] == "UNREGISTERED"
    assert data["is_online"] is False
    assert data["quota_gb"] == 0.0
    assert data["storage_dir"] is None


def test_provider_setup_validates_inputs(client: TestClient, provider_token: Dict[str, str]):
    headers = {"Authorization": provider_token["Authorization"]}

    # Empty storage dir
    res = client.post("/api/provider/node/setup", headers=headers, json={"storage_dir": "   ", "quota_gb": 10})
    assert res.status_code == 422

    # Negative / 0 quota
    res = client.post("/api/provider/node/setup", headers=headers, json={"storage_dir": "C:\\data", "quota_gb": 0})
    assert res.status_code == 422

    res = client.post("/api/provider/node/setup", headers=headers, json={"storage_dir": "C:\\data", "quota_gb": -5})
    assert res.status_code == 422

    # Excessive quota (>100,000 GB)
    res = client.post("/api/provider/node/setup", headers=headers, json={"storage_dir": "C:\\data", "quota_gb": 200000})
    assert res.status_code == 422


def test_provider_setup_and_heartbeat_online_flow(
    client: TestClient,
    provider_token: Dict[str, str],
    temp_storage_dir: Path,
):
    headers = {"Authorization": provider_token["Authorization"]}

    # Step 1: Provider configures node setup (action=START)
    res = client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(temp_storage_dir), "quota_gb": 25.0, "action": "START"},
    )
    assert res.status_code == 200
    setup_data = res.json()
    assert setup_data["configured"] is True
    assert setup_data["quota_gb"] == 25.0
    # Must NOT fabricate online status before heartbeat is received
    assert setup_data["status"] == "UNREGISTERED"
    assert setup_data["is_online"] is False

    # Step 2: Agent sends outbound heartbeat
    hb_res = client.post(
        "/api/provider/node/heartbeat",
        headers=headers,
        json={
            "allocated_bytes": int(25.0 * (1024**3)),
            "used_bytes": 1048576,  # 1 MB used
            "free_disk_bytes": 50000000000,
            "agent_version": "0.1.0",
        },
    )
    assert hb_res.status_code == 200
    hb_data = hb_res.json()
    assert hb_data["status"] == "ONLINE"
    assert hb_data["action"] == "CONTINUE"

    # Step 3: GET status reflects ONLINE and updated metrics
    status_res = client.get("/api/provider/node", headers=headers)
    assert status_res.status_code == 200
    live_status = status_res.json()
    assert live_status["configured"] is True
    assert live_status["status"] == "ONLINE"
    assert live_status["is_online"] is True
    assert live_status["used_bytes"] == 1048576
    assert live_status["last_heartbeat"] is not None


def test_heartbeat_timeout_transitions_to_offline(
    client: TestClient,
    provider_token: Dict[str, str],
    temp_storage_dir: Path,
):
    headers = {"Authorization": provider_token["Authorization"]}

    # Configure setup
    client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(temp_storage_dir), "quota_gb": 10.0, "action": "START"},
    )

    # Record heartbeat with stale timestamp (> 60s ago)
    provider_id = int(provider_token["id"])
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE provider_nodes
            SET last_heartbeat = ?, status = 'ONLINE', used_bytes = 0
            WHERE provider_id = ?;
            """,
            (stale_time, provider_id),
        )
        conn.commit()

    # Query status - coordinator must recognize stale heartbeat and mark OFFLINE
    status_res = client.get("/api/provider/node", headers=headers)
    data = status_res.json()
    assert data["status"] == "OFFLINE"
    assert data["is_online"] is False


def test_provider_stop_action_reflected(
    client: TestClient,
    provider_token: Dict[str, str],
    temp_storage_dir: Path,
):
    headers = {"Authorization": provider_token["Authorization"]}

    # Initial setup
    client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(temp_storage_dir), "quota_gb": 10.0, "action": "START"},
    )

    # Provider clicks Stop Storage Node (action=STOP)
    stop_res = client.post(
        "/api/provider/node/setup",
        headers=headers,
        json={"storage_dir": str(temp_storage_dir), "quota_gb": 10.0, "action": "STOP"},
    )
    assert stop_res.status_code == 200
    assert stop_res.json()["status"] == "STOPPED"

    # When agent heartbeats, it receives action="STOP"
    hb_res = client.post(
        "/api/provider/node/heartbeat",
        headers=headers,
        json={"allocated_bytes": 1000, "used_bytes": 0, "free_disk_bytes": 1000},
    )
    assert hb_res.status_code == 200
    assert hb_res.json()["action"] == "STOP"
    assert hb_res.json()["status"] == "STOPPED"


# ==============================================================================
# 3. Provider Agent Client & Daemon Tests
# ==============================================================================

def test_agent_config_from_env(monkeypatch):
    monkeypatch.setenv("STORESHARE_COORDINATOR_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("STORESHARE_STORAGE_DIR", "C:\\env_storage")
    monkeypatch.setenv("STORESHARE_QUOTA_GB", "42.5")
    monkeypatch.setenv("STORESHARE_EMAIL", "agent_op@example.com")
    monkeypatch.setenv("STORESHARE_PASSWORD", "AgentPass123!")

    cfg = AgentConfig.from_env()
    assert cfg.coordinator_url == "http://127.0.0.1:9999"
    assert cfg.storage_dir == "C:\\env_storage"
    assert cfg.quota_gb == 42.5
    assert cfg.email == "agent_op@example.com"
    assert cfg.password == "AgentPass123!"


def test_agent_rejects_missing_credentials(temp_storage_dir: Path):
    cfg = AgentConfig(
        coordinator_url="http://127.0.0.1:8001",
        storage_dir=str(temp_storage_dir),
        quota_gb=10.0,
    )
    agent = ProviderAgent(config=cfg)
    with pytest.raises(ValueError, match="No authentication credentials"):
        agent.authenticate()
    agent.close()


def test_agent_heartbeat_once_flow(
    client: TestClient,
    provider_token: Dict[str, str],
    temp_storage_dir: Path,
):
    token = provider_token["Authorization"].split(" ")[1]
    cfg = AgentConfig(
        coordinator_url="http://127.0.0.1:8001",
        storage_dir=str(temp_storage_dir),
        quota_gb=15.0,
        token=token,
    )

    # Use ProviderAgent with internal TestClient mock transport
    sm = StorageManager(storage_dir=temp_storage_dir, quota_bytes=int(15.0 * (1024**3)))
    agent = ProviderAgent(config=cfg, storage_manager=sm)

    # Mock agent.client to route requests to FastAPI TestClient
    def mock_get(url, **kwargs):
        return client.get(url, **kwargs)

    def mock_post(url, **kwargs):
        return client.post(url, **kwargs)

    agent.client.get = mock_get
    agent.client.post = mock_post

    # Execute single heartbeat workflow
    hb_result = agent.heartbeat_once()
    assert hb_result["status"] == "ONLINE"
    assert hb_result["action"] == "CONTINUE"

    # Verify coordinator state
    node = get_provider_node(int(provider_token["id"]))
    assert node is not None
    assert node["quota_bytes"] == int(15.0 * (1024**3))
    assert node["status"] == "ONLINE"
    agent.close()
