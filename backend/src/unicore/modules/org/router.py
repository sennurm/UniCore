"""HTTP endpoints for the org module. No business logic here (see ARCHITECTURE.md).

There is deliberately NO delete endpoint: org units are deactivate-never-delete
(AUTH-FR-19).
"""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.org import service
from unicore.modules.org.schemas import (
    OrgImportResult,
    OrgUnitCreate,
    OrgUnitOut,
    OrgUnitRename,
    OrgUnitReparent,
    OrgUnitUpdate,
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


@router.post("/imports", response_model=OrgImportResult, status_code=201)
async def import_units(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:create")),
) -> OrgImportResult:
    """Bulk-create Faculty Divisions/Schools/Departments/Programs from the CSV
    template. Partial commit: valid rows land, invalid rows come back as errors."""
    content = await file.read()
    result = await service.import_csv(session, ctx, file.filename or "org.csv", content)
    return OrgImportResult.model_validate(result)


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


@router.get("/units", response_model=list[OrgUnitOut])
async def list_units(
    unit_type: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=500, le=2000),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:read")),
) -> list[OrgUnitOut]:
    """Flat, filterable listing that powers the org table."""
    units = await service.list_units(session, unit_type, search, include_inactive, limit)
    return [OrgUnitOut.model_validate(u) for u in units]


@router.put("/units/{unit_id}", response_model=OrgUnitOut)
async def update_unit(
    unit_id: uuid.UUID,
    payload: OrgUnitUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:update")),
) -> OrgUnitOut:
    changes = payload.model_dump(exclude_unset=True)
    return OrgUnitOut.model_validate(await service.update_unit(session, ctx, unit_id, changes))


@router.post("/units/{unit_id}/reactivate", response_model=OrgUnitOut)
async def reactivate_unit(
    unit_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:deactivate")),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(await service.reactivate_unit(session, ctx, unit_id))


@router.get("/root", response_model=OrgUnitOut | None)
async def get_root(
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("org:read")),
) -> OrgUnitOut | None:
    root = await service.get_root(session)
    return OrgUnitOut.model_validate(root) if root else None


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
