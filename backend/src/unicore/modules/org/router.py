"""HTTP endpoints for the org module. No business logic here (see ARCHITECTURE.md).

There is deliberately NO delete endpoint: org units are deactivate-never-delete
(AUTH-FR-19).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.org import service
from unicore.modules.org.schemas import (
    OrgUnitCreate,
    OrgUnitOut,
    OrgUnitRename,
    OrgUnitReparent,
)
from unicore.modules.rbac.service import require_permission

router = APIRouter(prefix="/org", tags=["org"])


@router.post("/units", response_model=OrgUnitOut, status_code=201)
async def create_unit(
    payload: OrgUnitCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:create")),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(await service.create_unit(session, ctx, payload))


@router.patch("/units/{unit_id}", response_model=OrgUnitOut)
async def rename_unit(
    unit_id: uuid.UUID,
    payload: OrgUnitRename,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:update")),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(await service.rename_unit(session, ctx, unit_id, payload.name))


@router.post("/units/{unit_id}/deactivate", response_model=OrgUnitOut)
async def deactivate_unit(
    unit_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:deactivate")),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(await service.deactivate_unit(session, ctx, unit_id))


@router.post("/units/{unit_id}/reparent", response_model=OrgUnitOut)
async def reparent_unit(
    unit_id: uuid.UUID,
    payload: OrgUnitReparent,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:reparent")),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(
        await service.reparent_unit(session, ctx, unit_id, payload.new_parent_id)
    )


@router.get("/units/{unit_id}", response_model=OrgUnitOut)
async def get_unit(
    unit_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:read")),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(await service.get_unit(session, unit_id))


@router.get("/units/{unit_id}/children", response_model=list[OrgUnitOut])
async def list_children(
    unit_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:read")),
) -> list[OrgUnitOut]:
    children = await service.list_children(session, unit_id)
    return [OrgUnitOut.model_validate(c) for c in children]
