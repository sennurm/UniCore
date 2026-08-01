"""Project security rule: every API call needs a valid token + role check; deny by default."""

import ast
import json
import re
from pathlib import Path

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

SRC = Path(__file__).resolve().parent.parent / "src" / "unicore"


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


def _concrete(path: str) -> str:
    """Fill path params with a syntactically valid UUID so the route actually matches.

    Without this the check silently skipped every `/{id}` route — which is most of
    the mutating surface.
    """
    return re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)


async def test_every_registered_route_is_protected_or_allowlisted() -> None:
    """No endpoint ships unauthenticated by accident.

    Enumerated from the OpenAPI schema, not `app.routes`: this FastAPI version
    keeps included routers as opaque `_IncludedRouter` entries, so walking
    `app.routes` sees only the four docs routes and silently guards nothing. The
    sweep covers every path and verb, parameterised paths included.
    """
    app = create_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    allowed = public_paths()
    checked = 0
    async with client:
        for path, operations in app.openapi()["paths"].items():
            for method in operations:
                if method.upper() in {"HEAD", "OPTIONS"}:
                    continue
                response = await client.request(method.upper(), _concrete(path))
                checked += 1
                if path in allowed:
                    assert response.status_code != 401, f"{method} {path} should be public"
                else:
                    assert response.status_code == 401, (
                        f"{method.upper()} {path} is reachable without a token"
                    )
    assert checked > 40, f"only {checked} route/method pairs checked — the sweep lost coverage"


ROUTER_FILES = sorted((SRC / "modules").glob("*/router.py")) + [
    SRC / "core" / "templates_router.py",
    SRC / "core" / "health.py",
]

# Endpoints that legitimately carry no `require_permission`: public pre-auth routes,
# and "own data" routes that resolve their subject from the AuthContext rather than
# from a client-supplied id (project rule). Anything else must declare a permission.
PERMISSION_EXEMPT = {
    "health",  # public allowlist
    "login",
    "verify_otp",
    "password_reset_request",
    "password_reset_confirm",
    "me",  # own identity, from the token
    "change_password",
    "register_device",
    "request_device_change",
    "record_consent",
    "consent_status",
    "file_grievance",  # own grievance
    "my_grievances",
}


def test_every_endpoint_declares_a_permission_or_is_exempt() -> None:
    """The second half of the project rule: a token alone is never enough.

    CLAUDE.md requires a role check on every non-public endpoint; nothing enforced
    it, which is how the templates routes shipped token-only.
    """
    undeclared: list[str] = []
    for path in ROUTER_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if not any(ast.unparse(d).startswith("router.") for d in node.decorator_list):
                continue
            args = ast.unparse(node.args)
            declared = "require_permission" in args or "requires(" in args
            if not declared and node.name not in PERMISSION_EXEMPT:
                undeclared.append(f"{path.name}:{node.lineno} {node.name}")
    assert not undeclared, "endpoints without a role check: " + ", ".join(undeclared)


def test_permission_exempt_list_has_no_stale_entries() -> None:
    """A carve-out that no longer names a real endpoint must not linger."""
    names = set()
    for path in ROUTER_FILES:
        tree = ast.parse(path.read_text())
        names |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and any(ast.unparse(d).startswith("router.") for d in node.decorator_list)
        }
    assert not (PERMISSION_EXEMPT - names), f"stale exemptions: {PERMISSION_EXEMPT - names}"


def test_pseudonymous_user_id_is_stable_and_opaque() -> None:
    a, b = pseudonymous_user_id("erp-000123"), pseudonymous_user_id("erp-000123")
    assert a == b and a.startswith("u_") and "erp" not in a
