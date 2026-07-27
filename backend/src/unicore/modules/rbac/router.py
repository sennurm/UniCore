"""HTTP endpoints for the rbac module. No business logic here (see ARCHITECTURE.md)."""

import uuid

from fastapi import APIRouter, Depends
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


@router.get("/users/{user_id}/grants", response_model=list[GrantOut])
async def list_user_grants(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("rbac:read")),
) -> list[GrantOut]:
    grants = await service.list_user_grants(session, user_id)
    return [GrantOut.model_validate(g) for g in grants]
