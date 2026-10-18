import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Configure testing environment variables before importing app modules
TEST_SECRET: str = "super-secret-test-jwt-key-minimum-32-chars-long-123456"
os.environ["STORESHARE_JWT_SECRET"] = TEST_SECRET
os.environ["STORESHARE_ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"


@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path: Path, monkeypatch):
    """
    Ensure every test run operates against a fresh, isolated SQLite database
    in a temporary directory. Protects data/storeshare.db from modification.
    """
    test_db_file = tmp_path / "test_storeshare.db"
    monkeypatch.setenv("STORESHARE_DB_PATH", str(test_db_file))
    monkeypatch.setenv("STORESHARE_JWT_SECRET", TEST_SECRET)

    # Initialize tables on the test database
    from backend.database import init_db
    init_db(test_db_file)

    yield test_db_file

    # Clean up test database file if it exists
    if test_db_file.exists():
        try:
            test_db_file.unlink()
        except OSError:
            pass


@pytest.fixture
def client(isolated_test_db):
    """
    FastAPI TestClient fixture with isolated test database.
    """
    from backend.main import app
    with TestClient(app) as test_client:
        yield test_client
