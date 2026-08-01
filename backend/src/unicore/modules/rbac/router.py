"""HTTP endpoints for the rbac module. No business logic here (see ARCHITECTURE.md)."""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.rbac import service
from unicore.modules.rbac.schemas import GrantCreate, GrantOut, GrantRevoke, SupersedeRequest
from unicore.modules.rbac.service import require_permission

router = APIRouter(prefix="/rbac", tags=["rbac"])


@router.post("/grants", response_model=GrantOut, status_code=201)
async def create_grant(
    payload: GrantCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:grant")),
) -> GrantOut:
    return GrantOut.model_validate(await service.create_grant(session, ctx, payload))


@router.post("/grants/{grant_id}/revoke", response_model=GrantOut)
async def revoke_grant(
    grant_id: uuid.UUID,
    payload: GrantRevoke,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:grant")),
) -> GrantOut:
    return GrantOut.model_validate(
        await service.revoke_grant(session, ctx, grant_id, payload.reason)
    )


@router.post("/grants/supersede", response_model=GrantOut, status_code=201)
async def supersede(
    payload: SupersedeRequest,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:grant")),
) -> GrantOut:
    return GrantOut.model_validate(await service.supersede(session, ctx, payload))


@router.get("/roles")
async def list_roles(
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:read")),
) -> list[dict[str, object]]:
    """The role registry — what may be granted, and what unit type each binds to."""
    return await service.list_roles(session)


@router.get("/users/{user_id}/grants", response_model=list[GrantOut])
async def list_user_grants(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:read")),
) -> list[GrantOut]:
    grants = await service.list_user_grants(session, user_id)
    return [GrantOut.model_validate(g) for g in grants]


@router.get("/users/{user_id}/reporting")
async def reporting_for_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:read")),
) -> list[dict]:
    return await service.resolve_reporting(session, user_id)


@router.get("/directory")
async def directory(
    search: str | None = None,
    role_code: str | None = None,
    status: str | None = None,
    limit: int = Query(default=500, le=2000),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:read")),
) -> list[dict[str, object]]:
    """Users with their active roles — the combined Users & roles table."""
    return await service.directory(
        session, search=search, role_code=role_code, status=status, limit=limit
    )


@router.get("/directory.csv", response_class=PlainTextResponse)
async def directory_csv(
    search: str | None = None,
    role_code: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:read")),
) -> PlainTextResponse:
    rows = await service.directory(
        session, search=search, role_code=role_code, status=status, limit=2000
    )
    return PlainTextResponse(
        service.directory_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="unicore_users_roles.csv"'},
    )


@router.post("/directory/imports")
async def apply_roles_csv(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:bulk-roles")),
) -> dict[str, object]:
    """Upload an edited directory export: roles added are granted, roles removed
    are revoked. Each change goes through the normal grant rules and audit."""
    content = await file.read()
    return await service.apply_roles_csv(session, ctx, content)
