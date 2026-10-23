"""
Integration and regression tests for Store-Share cross-computer connectivity,
CORS header exposure, provider storage pre-reservation, and cryptographic integrity.
"""

import hashlib
import os
import shutil
import tempfile
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import (
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    create_user,
    init_db,
    poll_provider_jobs,
    upsert_provider_node,
)
from backend.security import hash_password
from provider_agent.agent import ProviderAgent
from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()


def test_cors_expose_headers_present():
    """
    Test that CORS preflight and standard requests expose custom encryption/metadata headers
    so cross-computer browser clients can read X-IV-Hex, X-SHA256-Checksum, etc.
    """
    client = TestClient(app)
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    expose_headers = response.headers.get("access-control-expose-headers", "")
    assert "x-iv-hex" in expose_headers.lower()
    assert "x-sha256-checksum" in expose_headers.lower()
    assert "x-plaintext-size" in expose_headers.lower()
    assert "x-encrypted-size" in expose_headers.lower()
    assert "content-disposition" in expose_headers.lower()


def test_poll_provider_jobs_includes_chunk_size():
    """
    Test that poll_provider_jobs returns chunk_size by joining file_chunks,
    enabling provider agents to pre-reserve quota before downloading ciphertext.
    """
    suffix = uuid.uuid4().hex[:8]
    consumer = create_user(
        f"consumer_{suffix}@test.io",
        hash_password("Password123!"),
        "CONSUMER",
    )
    provider = create_user(
        f"provider_{suffix}@test.io",
        hash_password("Password123!"),
        "PROVIDER",
    )

    node = upsert_provider_node(
        provider_id=provider["id"],
        storage_dir=f"C:\\test_storage_{suffix}",
        quota_bytes=10 * 1024 * 1024,
        status="ONLINE",
    )

    chunk_size = 2048
    chunk_id = f"chunk_test_{suffix}"

    file_rec = create_file_record(
        owner_id=consumer["id"],
        filename="test_file.txt",
        file_size=1000,
        encrypted_size=chunk_size,
        iv_hex="0102030405060708090a0b0c",
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    create_file_chunk(
        file_id=file_rec["id"],
        chunk_id=chunk_id,
        provider_node_id=node["id"],
        chunk_size=chunk_size,
        chunk_index=0,
    )

    create_chunk_job(
        provider_node_id=node["id"],
        chunk_id=chunk_id,
        file_id=file_rec["id"],
        job_type="STORE_CHUNK",
        timeout_seconds=60,
    )

    polled = poll_provider_jobs(provider_node_id=node["id"], limit=5)
    assert len(polled) >= 1
    target_job = next(j for j in polled if j["chunk_id"] == chunk_id)
    assert target_job["chunk_size"] == chunk_size


def test_agent_rejects_corrupted_inbound_checksum():
    """
    Test that fetch_job_data in ProviderAgent computes SHA-256 and rejects
    payloads if the coordinator's X-SHA256-Checksum header does not match.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        storage = StorageManager(storage_dir=tmpdir, quota_bytes=1024 * 1024 * 1024)
        config = AgentConfig(
            coordinator_url="http://mock-coordinator",
            storage_dir=tmpdir,
            quota_gb=1.0,
            token="mock-token",
        )
        agent = ProviderAgent(config=config, storage_manager=storage)

        # Mock httpx client response with corrupted payload
        class MockResponse:
            status_code = 200
            content = b"Tampered ciphertext bytes"
            headers = {"X-SHA256-Checksum": "0000000000000000000000000000000000000000000000000000000000000000"}

        class MockClient:
            def get(self, *args, **kwargs):
                return MockResponse()

        agent.client = MockClient()

        with pytest.raises(RuntimeError, match="Integrity check failed"):
            agent.fetch_job_data(job_id=999)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_agent_pre_reserves_space_and_fails_early_on_insufficient_space():
    """
    Test that process_single_job attempts space pre-reservation and rejects
    STORE_CHUNK immediately if the chunk exceeds available quota.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        # Small quota: 1000 bytes
        storage = StorageManager(storage_dir=tmpdir, quota_bytes=1000)
        config = AgentConfig(
            coordinator_url="http://mock-coordinator",
            storage_dir=tmpdir,
            quota_gb=0.000001,
            token="mock-token",
        )
        agent = ProviderAgent(config=config, storage_manager=storage)

        failed_calls = []

        def mock_fail_job(job_id, error_message):
            failed_calls.append((job_id, error_message))
            return {}

        agent.fail_job = mock_fail_job

        # Job requests 500,000 bytes (far exceeds 1073 bytes quota)
        job = {
            "id": 1234,
            "job_type": "STORE_CHUNK",
            "chunk_id": "test_oversized_chunk",
            "chunk_size": 500000,
        }

        success = agent.process_single_job(job)
        assert success is False
        assert len(failed_calls) == 1
        assert failed_calls[0][0] == 1234
        assert "Storage reservation failed" in failed_calls[0][1]

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
