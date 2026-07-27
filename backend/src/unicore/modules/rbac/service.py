"""Business rules for the rbac module. The only layer other modules may call.

`require_permission` is the project-wide authorization dependency: every
non-public endpoint MUST declare it (project security rule, 25-07-2026).

Phase 1 interim: permissions resolve through a static action->roles registry
against the roles carried by the AuthContext. Phase 2 replaces the lookup with
the real grant engine (org-unit scope, validity, term binding — AUTH-FR-04/05)
without changing any endpoint code. Unknown actions fail closed.
"""

from fastapi import HTTPException, Request

from unicore.core.logging import get_logger
from unicore.core.security import AuthContext

# Interim action registry (Phase 1). Scope-aware grants replace this in Phase 2.
ACTION_ROLES: dict[str, tuple[str, ...]] = {
    "org:create": ("super-admin",),
    "org:update": ("super-admin",),
    "org:deactivate": ("super-admin",),
    "org:reparent": ("super-admin",),
    "org:read": ("super-admin", "system-admin"),
    "user:create": ("super-admin", "system-admin"),
    "user:read": ("super-admin", "system-admin"),
    "user:deactivate": ("super-admin", "system-admin"),
}


def require_permission(action: str):  # noqa: ANN201 — returns a FastAPI dependency
    """Dependency factory: asserts the authenticated caller may perform `action`."""

    async def dependency(request: Request) -> AuthContext:
        ctx: AuthContext | None = getattr(request.state, "auth", None)
        if ctx is None:  # gate bypassed somehow — refuse rather than trust
            raise HTTPException(status_code=401, detail="Unauthenticated.")
        allowed = ACTION_ROLES.get(action)
        if allowed is None or not set(allowed) & set(ctx.role_names):
            get_logger().warning(
                "permission denied",
                action=action,
                roles=list(ctx.role_names),
            )
            raise HTTPException(status_code=403, detail=f"Not permitted: {action}.")
        return ctx

    return dependency
