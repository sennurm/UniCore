"""Business rules for the rbac module. The only layer other modules may call.

`require_permission` is the project-wide authorization dependency: every
non-public endpoint MUST declare it (project security rule, 25-07-2026).
Phase 2 implements the real grant/scope evaluation (AUTH-FR-04/05); until then
it fails closed.
"""

from fastapi import HTTPException, Request

from unicore.core.security import AuthContext


def require_permission(action: str):  # noqa: ANN201 — returns a FastAPI dependency
    """Dependency factory: asserts the authenticated caller may perform `action`.

    Resolves the AuthContext placed on the request by the auth gate, then
    evaluates role + org-unit scope for `action`. Fails closed until the
    grant engine lands in Phase 2.
    """

    async def dependency(request: Request) -> AuthContext:
        ctx: AuthContext | None = getattr(request.state, "auth", None)
        if ctx is None:  # gate bypassed somehow — refuse rather than trust
            raise HTTPException(status_code=401, detail="Unauthenticated.")
        raise HTTPException(
            status_code=403,
            detail=f"Permission engine not yet available for action '{action}'.",
        )

    return dependency
