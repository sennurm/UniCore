"""Project-level API security rule (locked 25-07-2026): deny by default.

Every API call must carry a valid session token and pass a role+scope permission
check; responses must never contain data outside the caller's scope. This module
implements the first gate — authentication — as ASGI middleware so it covers
every route, present and future, with an explicit allowlist for public paths.
Permission checks are declared per-endpoint via the rbac module's
`require_permission` dependency (AUTH-FR-05); DAOs must take the caller's scope
as a parameter so cross-user leakage is impossible by construction.

The token verifier is registered by the auth module at startup (Phase 3 wires it
to Redis sessions). Until a verifier is registered, every protected request is
rejected — the gate fails closed, never open.
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from unicore.core.config import get_settings

ALWAYS_PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/auth/login",
        "/auth/otp/verify",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
    }
)
DEV_ONLY_PUBLIC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


@dataclass(frozen=True)
class AuthContext:
    """The authenticated caller, attached to request.state.auth by the gate."""

    user_id: str
    session_id: str
    role_names: tuple[str, ...] = field(default=())


class InvalidTokenError(Exception):
    """Raised by the token verifier for missing/expired/revoked sessions."""


TokenVerifier = Callable[[str], Awaitable[AuthContext]]
PermissionChecker = Callable[[Request, str], Awaitable[AuthContext]]

_token_verifier: TokenVerifier | None = None
_permission_checker: PermissionChecker | None = None


def register_token_verifier(verifier: TokenVerifier) -> None:
    """Called by the auth module at startup; core stays free of module imports."""
    global _token_verifier
    _token_verifier = verifier


def register_permission_checker(checker: PermissionChecker) -> None:
    """Called by the rbac module at startup, mirroring the token verifier.

    Routers under core/ (templates, health) cannot import modules/ — the
    architecture rule is one-way — but the project rule still demands a role check
    on every endpoint. Registration inverts the dependency so core keeps its
    independence and still refuses to serve an unauthorised caller.
    """
    global _permission_checker
    _permission_checker = checker


def requires(action: str):  # noqa: ANN201 — returns a FastAPI dependency
    """core/ equivalent of rbac's `require_permission`, resolved at request time."""

    async def dependency(request: Request) -> AuthContext:
        if _permission_checker is None:  # nothing registered — fail closed
            raise HTTPException(status_code=403, detail="Authorization is unavailable.")
        return await _permission_checker(request, action)

    return dependency


def public_paths() -> frozenset[str]:
    if get_settings().environment == "dev":
        return ALWAYS_PUBLIC_PATHS | DEV_ONLY_PUBLIC_PATHS
    return ALWAYS_PUBLIC_PATHS


def pseudonymous_user_id(user_id: str) -> str:
    """Stable pseudonymous id for logs per the logging standard — never raw PII."""
    return "u_" + hashlib.sha256(user_id.encode()).hexdigest()[:8]


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
        content={"error": {"code": "unauthenticated", "message": detail}},
    )


async def auth_gate_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Deny-by-default authentication for every route not on the public allowlist."""
    if request.url.path in public_paths():
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return _unauthorized("A valid bearer token is required.")

    if _token_verifier is None:  # no session backend registered yet — fail closed
        return _unauthorized("Authentication is unavailable.")

    try:
        ctx = await _token_verifier(token)
    except InvalidTokenError:
        return _unauthorized("Invalid or expired session.")

    request.state.auth = ctx
    structlog.contextvars.bind_contextvars(user_id=pseudonymous_user_id(ctx.user_id))
    return await call_next(request)
