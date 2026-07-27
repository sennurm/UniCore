"""Project security rule: every API call needs a valid token + role check; deny by default."""

import json

import httpx
import pytest

from unicore.core import security
from unicore.core.security import (
    AuthContext,
    InvalidTokenError,
    pseudonymous_user_id,
    public_paths,
    register_token_verifier,
)
from unicore.main import create_app


@pytest.fixture(autouse=True)
def _reset_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_token_verifier", None)


def _client() -> httpx.AsyncClient:
    app = create_app()

    @app.get("/probe")
    async def probe() -> dict[str, str]:  # a stand-in for any future endpoint
        return {"data": "secret"}

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_is_public() -> None:
    async with _client() as client:
        assert (await client.get("/health")).status_code == 200


async def test_missing_token_rejected() -> None:
    async with _client() as client:
        response = await client.get("/probe")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_fails_closed_when_no_verifier_registered() -> None:
    async with _client() as client:
        response = await client.get("/probe", headers={"Authorization": "Bearer sometoken"})
    assert response.status_code == 401


async def test_invalid_token_rejected() -> None:
    async def verifier(token: str) -> AuthContext:
        raise InvalidTokenError

    async with _client() as client:
        register_token_verifier(verifier)
        response = await client.get("/probe", headers={"Authorization": "Bearer bad"})
    assert response.status_code == 401


async def test_valid_token_passes_and_binds_pseudonymous_user_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def verifier(token: str) -> AuthContext:
        assert token == "good"
        return AuthContext(user_id="user-123", session_id="s-1")

    async with _client() as client:
        register_token_verifier(verifier)
        response = await client.get("/probe", headers={"Authorization": "Bearer good"})
    assert response.status_code == 200

    access_logs = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"http request completed"' in line
    ]
    assert access_logs, "expected an access log line"
    logged_user = access_logs[-1].get("user_id")
    assert logged_user == pseudonymous_user_id("user-123")
    assert "user-123" not in (logged_user or "")  # never the raw id


async def test_every_registered_route_is_protected_or_allowlisted() -> None:
    """No endpoint can ship unauthenticated by accident — GET every concrete route."""
    app = create_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    allowed = public_paths()
    async with client:
        for route in app.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if path is None or "{" in path or "GET" not in methods:
                continue
            response = await client.get(path)
            if path in allowed:
                assert response.status_code != 401, f"{path} should be public"
            else:
                assert response.status_code == 401, f"{path} is reachable without a token"


def test_pseudonymous_user_id_is_stable_and_opaque() -> None:
    a, b = pseudonymous_user_id("erp-000123"), pseudonymous_user_id("erp-000123")
    assert a == b and a.startswith("u_") and "erp" not in a
