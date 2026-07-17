"""
System integration test for the full auth flow (WO-003 acceptance criteria):

    login -> authenticated request -> logout -> revoked token rejected

Requires live PostgreSQL, MongoDB, and Redis (the same services CI spins up
for tests/, see .github/workflows/ci.yml, or `docker compose up -d postgres
mongodb redis` for local runs). Uses FastAPI's TestClient so the app's real
lifespan (DB init, seeding) runs exactly as it does in production.

Run with: PYTHONPATH=. pytest tests/test_auth_integration.py -v
"""
import os
import sys
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_EMAIL = f"integration-test-{uuid.uuid4().hex[:8]}@oms.local"
TEST_PASSWORD = "integration-test-password-1234"


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def test_user(client):
    """Insert a dedicated test user directly via a synchronous DB connection
    (independent of the app's async event loop / bootstrap-admin config) so
    this test is hermetic and repeatable."""
    import uuid as _uuid
    import psycopg2
    from app.config import settings
    from app.core.security import hash_password

    user_id = str(_uuid.uuid4())
    conn = psycopg2.connect(settings.SYNC_DATABASE_URL.replace("postgresql+psycopg2", "postgresql"))
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, email, full_name, hashed_password, is_active, is_superadmin)
                    VALUES (%s, %s, %s, %s, TRUE, TRUE)
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (user_id, TEST_EMAIL, "Integration Test User", hash_password(TEST_PASSWORD)),
                )
    finally:
        conn.close()
    return TEST_EMAIL, TEST_PASSWORD


@pytest.mark.integration
def test_full_login_auth_logout_revoked_flow(client, test_user):
    email, password = test_user

    # 1. Login — expect a valid JWT in the response body and httpOnly cookie
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    body = login_resp.json()
    access_token = body["access_token"]
    assert access_token
    assert "access_token" in login_resp.cookies

    # 2. Authenticated request using the Bearer token
    me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200, me_resp.text
    assert me_resp.json()["email"] == email

    # 2b. Authenticated request using the httpOnly cookie set at login (no header)
    me_via_cookie = client.get("/auth/me")
    assert me_via_cookie.status_code == 200
    assert me_via_cookie.json()["email"] == email

    # 3. Logout — revokes the token via the Redis jti blocklist and clears the cookie
    logout_resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})
    assert logout_resp.status_code == 204

    # 4. Re-using the revoked token must now be rejected
    rejected_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert rejected_resp.status_code == 401


@pytest.mark.integration
def test_login_with_wrong_password_rejected(client, test_user):
    email, _ = test_user
    resp = client.post("/auth/login", json={"email": email, "password": "definitely-wrong"})
    assert resp.status_code == 401


@pytest.mark.integration
def test_unauthenticated_request_rejected(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.integration
def test_authenticated_request_returns_503_when_redis_unavailable(client, test_user):
    """WO-012: fail-closed on Redis outage must surface as 503, not 401,
    all the way through the auth middleware."""
    email, password = test_user
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    access_token = login_resp.json()["access_token"]

    with patch("app.database.redis_client.get_redis_client", return_value=None):
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 503


@pytest.mark.integration
def test_login_transparently_upgrades_legacy_bcrypt_hash(client):
    """WO-010: a user whose password was hashed with the pre-migration
    algorithm (bcrypt) must be able to log in, and their stored hash must be
    silently upgraded to Argon2id on that successful login."""
    import uuid as _uuid
    import bcrypt
    import psycopg2
    from app.config import settings

    email = f"legacy-hash-test-{_uuid.uuid4().hex[:8]}@oms.local"
    password = "legacy-bcrypt-password-1234"
    legacy_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = psycopg2.connect(settings.SYNC_DATABASE_URL.replace("postgresql+psycopg2", "postgresql"))
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, email, full_name, hashed_password, is_active, is_superadmin)
                    VALUES (%s, %s, %s, %s, TRUE, FALSE)
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (str(_uuid.uuid4()), email, "Legacy Hash Test User", legacy_hash),
                )

        # Sanity check: hash is bcrypt before login
        with conn.cursor() as cur:
            cur.execute("SELECT hashed_password FROM users WHERE email = %s", (email,))
            stored_before = cur.fetchone()[0]
        assert stored_before.startswith("$2")

        login_resp = client.post("/auth/login", json={"email": email, "password": password})
        assert login_resp.status_code == 200, login_resp.text

        with conn.cursor() as cur:
            cur.execute("SELECT hashed_password FROM users WHERE email = %s", (email,))
            stored_after = cur.fetchone()[0]
        assert stored_after.startswith("$argon2id$")

        # The upgraded hash must still authenticate the same password.
        second_login = client.post("/auth/login", json={"email": email, "password": password})
        assert second_login.status_code == 200
    finally:
        conn.close()
