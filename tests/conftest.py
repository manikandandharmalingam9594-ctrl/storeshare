import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Configure testing environment variables before importing app modules
TEST_SECRET: str = "super-secret-test-jwt-key-minimum-32-chars-long-123456"
os.environ["STORESHARE_JWT_SECRET"] = TEST_SECRET
os.environ["STORESHARE_ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
# Prevent tests from accidentally touching PostgreSQL database unless specifically instructed:
os.environ["STORESHARE_DATABASE_URL"] = ""


@pytest.fixture(autouse=True)
def isolated_test_db(request, tmp_path: Path, monkeypatch):
    """
    Ensure standard tests run against a fresh isolated SQLite database,
    while tests marked with @pytest.mark.postgres_integration connect
    to the configured PostgreSQL database.
    """
    if request.node.get_closest_marker("postgres_integration"):
        from dotenv import dotenv_values
        env_vals = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
        pg_url = env_vals.get("STORESHARE_DATABASE_URL") or os.environ.get("STORESHARE_DATABASE_URL")
        if not pg_url:
            pytest.skip("STORESHARE_DATABASE_URL not configured for PostgreSQL integration tests")
        monkeypatch.setenv("STORESHARE_DATABASE_URL", pg_url)
        monkeypatch.delenv("STORESHARE_DB_PATH", raising=False)
        monkeypatch.setenv("STORESHARE_JWT_SECRET", TEST_SECRET)
        yield None
        return

    test_db_file = tmp_path / "test_storeshare.db"
    monkeypatch.setenv("STORESHARE_DATABASE_URL", "")
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
