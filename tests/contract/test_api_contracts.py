"""OpenAPI contract tests (WO-084/085/086)."""
import pytest

pytestmark = pytest.mark.requires_db


@pytest.mark.asyncio
async def test_openapi_includes_core_routes(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/orders/" in paths
    assert "/inventory/" in paths
    assert "/agents/" in paths


@pytest.mark.asyncio
async def test_orders_list_contract(async_client, test_session, user_factory, default_environment):
    from tests.conftest import auth_headers, env_headers, issue_token, persist_user

    user = await persist_user(test_session, user_factory, is_superadmin=True, platform_role="SUPERADMIN")
    token = await issue_token(user)
    headers = {**auth_headers(token), **env_headers(str(default_environment.id))}
    response = await async_client.get("/orders/", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
