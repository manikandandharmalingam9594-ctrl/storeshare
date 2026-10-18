from datetime import timedelta
import os
import pytest
from fastapi.testclient import TestClient

from backend.database import create_user, get_db_path, get_user_by_email
from backend.security import create_access_token, hash_password


def test_consumer_registration_success(client: TestClient):
    """
    Verify public consumer registration returns 201 with role strictly CONSUMER.
    Password hash must NOT be in response.
    """
    payload = {
        "email": "testconsumer@storeshare.io",
        "password": "SecurePassword123!",
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "testconsumer@storeshare.io"
    assert data["role"] == "CONSUMER"
    assert data["is_active"] is True
    assert "password_hash" not in data
    assert "password" not in data

    # Verify user was saved in test database
    user = get_user_by_email("testconsumer@storeshare.io")
    assert user is not None
    assert user["role"] == "CONSUMER"


def test_registration_duplicate_email_conflict(client: TestClient):
    """
    Duplicate registration with identical or case-insensitive email must return 409 Conflict.
    """
    client.post("/api/auth/register", json={
        "email": "duplicate@storeshare.io",
        "password": "Password123!",
    })

    # Second registration with identical email
    resp1 = client.post("/api/auth/register", json={
        "email": "duplicate@storeshare.io",
        "password": "AnotherPassword123!",
    })
    assert resp1.status_code == 409
    assert "already exists" in resp1.json()["detail"].lower()

    # Third registration with mixed case
    resp2 = client.post("/api/auth/register", json={
        "email": "DUPLICATE@StoreShare.io",
        "password": "AnotherPassword123!",
    })
    assert resp2.status_code == 409


def test_registration_email_normalization(client: TestClient):
    """
    Emails should be normalized (lowercase and trimmed).
    """
    resp = client.post("/api/auth/register", json={
        "email": "  MixedCaseUser@StoreShare.io  ",
        "password": "ValidPassword123!",
    })
    assert resp.status_code == 201
    assert resp.json()["email"] == "mixedcaseuser@storeshare.io"

    # Login with different casing must succeed
    login_resp = client.post("/api/auth/login", json={
        "email": "MIXEDCASEUSER@STORESHARE.IO",
        "password": "ValidPassword123!",
    })
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


def test_registration_invalid_inputs(client: TestClient):
    """
    Verify validation rules on email and password.
    """
    # Invalid email format
    resp = client.post("/api/auth/register", json={
        "email": "not-an-email",
        "password": "ValidPassword123!",
    })
    assert resp.status_code == 422

    # Password too short (< 8 chars)
    resp2 = client.post("/api/auth/register", json={
        "email": "short@storeshare.io",
        "password": "short",
    })
    assert resp2.status_code == 422

    # Password empty or all whitespace
    resp3 = client.post("/api/auth/register", json={
        "email": "blank@storeshare.io",
        "password": "        ",
    })
    assert resp3.status_code == 422


def test_registration_role_escalation_prevented(client: TestClient):
    """
    Security check: Attempting to supply a role (e.g. ADMIN or PROVIDER) during
    registration must be forbidden and rejected, preventing privilege escalation.
    """
    payload = {
        "email": "hacker@storeshare.io",
        "password": "HackerPassword123!",
        "role": "ADMIN",
    }
    response = client.post("/api/auth/register", json=payload)
    # Extra fields are forbidden by Pydantic ConfigDict(extra="forbid")
    assert response.status_code == 422

    # Verify no admin user was created in the database
    user = get_user_by_email("hacker@storeshare.io")
    assert user is None


def test_login_success(client: TestClient):
    """
    Valid credentials issue a signed JWT access token.
    """
    client.post("/api/auth/register", json={
        "email": "loginuser@storeshare.io",
        "password": "LoginSecret123!",
    })

    response = client.post("/api/auth/login", json={
        "email": "loginuser@storeshare.io",
        "password": "LoginSecret123!",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 3600
    assert data["user"]["email"] == "loginuser@storeshare.io"
    assert data["user"]["role"] == "CONSUMER"


def test_login_invalid_password(client: TestClient):
    """
    Wrong password returns HTTP 401 Unauthorized with safe error message.
    """
    client.post("/api/auth/register", json={
        "email": "wrongpwd@storeshare.io",
        "password": "CorrectPassword123!",
    })

    response = client.post("/api/auth/login", json={
        "email": "wrongpwd@storeshare.io",
        "password": "WrongPassword999!",
    })
    assert response.status_code == 401
    assert "invalid email or password" in response.json()["detail"].lower()


def test_login_nonexistent_user(client: TestClient):
    """
    Nonexistent email returns identical 401 Unauthorized to prevent user enumeration.
    """
    response = client.post("/api/auth/login", json={
        "email": "nonexistent@storeshare.io",
        "password": "SomePassword123!",
    })
    assert response.status_code == 401
    assert "invalid email or password" in response.json()["detail"].lower()


def test_login_inactive_user_refused(client: TestClient):
    """
    Inactive accounts must not be able to log in.
    """
    # Create inactive user directly in database
    hashed = hash_password("InactiveSecret123!")
    create_user(
        email="inactive@storeshare.io",
        password_hash=hashed,
        role="CONSUMER",
        is_active=False,
    )

    response = client.post("/api/auth/login", json={
        "email": "inactive@storeshare.io",
        "password": "InactiveSecret123!",
    })
    assert response.status_code == 401
    assert "inactive" in response.json()["detail"].lower()


def test_auth_me_authenticated_user(client: TestClient):
    """
    GET /api/auth/me returns current user identity with valid Bearer token.
    """
    client.post("/api/auth/register", json={
        "email": "me@storeshare.io",
        "password": "MySecretPassword123!",
    })
    login_resp = client.post("/api/auth/login", json={
        "email": "me@storeshare.io",
        "password": "MySecretPassword123!",
    })
    token = login_resp.json()["access_token"]

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "me@storeshare.io"
    assert data["role"] == "CONSUMER"
    assert data["is_active"] is True
    assert "password_hash" not in data


def test_auth_me_missing_token_unauthorized(client: TestClient):
    """
    Calling protected endpoint without token returns HTTP 401.
    """
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_auth_me_invalid_token_unauthorized(client: TestClient):
    """
    Calling protected endpoint with malformed token returns HTTP 401.
    """
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not.a.valid.jwt.token"},
    )
    assert response.status_code == 401


def test_auth_me_expired_token_unauthorized(client: TestClient):
    """
    Calling protected endpoint with an expired JWT token returns HTTP 401.
    """
    # Create user
    hashed = hash_password("Password123!")
    user = create_user("expired@storeshare.io", hashed, role="CONSUMER")

    # Issue token that already expired 10 minutes ago
    expired_token, _ = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        expires_delta=timedelta(minutes=-10),
    )

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_auth_me_user_became_inactive(client: TestClient):
    """
    If a user is deactivated after token creation, subsequent requests return HTTP 403 Forbidden.
    """
    hashed = hash_password("DeactivatedPass123!")
    user = create_user("deactivated@storeshare.io", hashed, role="CONSUMER", is_active=True)

    token, _ = create_access_token(user["id"], user["email"], user["role"])

    # Now deactivate the user directly in the database
    from backend.database import get_db_connection
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user["id"],))
        conn.commit()

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "inactive" in response.json()["detail"].lower()


def test_consumer_cannot_access_admin_endpoints(client: TestClient):
    """
    Security check: A consumer token cannot access admin-only endpoints.
    Must return HTTP 403 Forbidden.
    """
    client.post("/api/auth/register", json={
        "email": "regularconsumer@storeshare.io",
        "password": "Password123!",
    })
    login_resp = client.post("/api/auth/login", json={
        "email": "regularconsumer@storeshare.io",
        "password": "Password123!",
    })
    consumer_token = login_resp.json()["access_token"]

    # Attempt to provision a provider account
    resp = client.post(
        "/api/admin/providers",
        json={"email": "newprovider@storeshare.io", "password": "ProviderPass123!"},
        headers={"Authorization": f"Bearer {consumer_token}"},
    )
    assert resp.status_code == 403
    assert "operation not permitted" in resp.json()["detail"].lower()


def test_admin_can_provision_provider(client: TestClient):
    """
    Admin user can successfully provision a PROVIDER account.
    """
    # Create admin in database
    hashed = hash_password("AdminSecurePass123!")
    admin_user = create_user("admin@storeshare.io", hashed, role="ADMIN")

    admin_token, _ = create_access_token(admin_user["id"], admin_user["email"], admin_user["role"])

    # Admin provisions provider
    resp = client.post(
        "/api/admin/providers",
        json={"email": "storageprovider@storeshare.io", "password": "ProviderPass123!"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "storageprovider@storeshare.io"
    assert data["role"] == "PROVIDER"

    # Provider can now log in
    prov_login = client.post("/api/auth/login", json={
        "email": "storageprovider@storeshare.io",
        "password": "ProviderPass123!",
    })
    assert prov_login.status_code == 200
    assert prov_login.json()["user"]["role"] == "PROVIDER"


def test_missing_jwt_secret_fails_securely(monkeypatch):
    """
    Security check: Missing JWT secret must raise RuntimeError rather than
    silently proceeding with an insecure default.
    """
    monkeypatch.delenv("STORESHARE_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)

    from backend.security import create_access_token, get_jwt_secret

    with pytest.raises(RuntimeError) as exc_info:
        get_jwt_secret()
    assert "critical security configuration error" in str(exc_info.value).lower()

    with pytest.raises(RuntimeError):
        create_access_token(1, "test@test.com", "CONSUMER")


def test_db_tables_endpoint_includes_users_table(client: TestClient):
    """
    Verify existing /db/tables endpoint remains functional and now lists
    both app_metadata and users tables.
    """
    response = client.get("/db/tables")
    assert response.status_code == 200
    tables = response.json()["tables"]
    assert "app_metadata" in tables
    assert "users" in tables


def test_health_endpoint_healthy(client: TestClient):
    """
    Verify existing /health endpoint continues to return 200 with database: connected.
    """
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"


def test_admin_bootstrap_cli_success():
    """
    Verify the create_admin_account CLI function creates an active ADMIN user.
    """
    from backend.create_admin import create_admin_account

    create_admin_account("bootstrapadmin@storeshare.io", "AdminPassword123!")
    user = get_user_by_email("bootstrapadmin@storeshare.io")
    assert user is not None
    assert user["role"] == "ADMIN"
    assert user["is_active"] == 1


def test_admin_bootstrap_cli_refuses_overwrite():
    """
    Verify the create_admin_account CLI strictly refuses to overwrite an existing account.
    """
    from backend.create_admin import create_admin_account

    create_admin_account("originaladmin@storeshare.io", "InitialPassword123!")

    # Attempting to bootstrap with the same email must exit with error
    with pytest.raises(SystemExit) as exc:
        create_admin_account("originaladmin@storeshare.io", "NewPassword456!")
    assert exc.value.code == 1
