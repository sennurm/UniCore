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
    OfferingCreate,
    OfferingOut,
    OrgImportResult,
    OrgUnitCreate,
    OrgUnitOut,
    OrgUnitRename,
    OrgUnitReparent,
    OrgUnitUpdate,
    SubjectCreate,
    SubjectOut,
    SubjectUpdate,
    VenueCreate,
    VenueOut,
    VenueUpdate,
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


# --- subjects, offerings and venues ------------------------------------------


@router.post("/subjects", response_model=SubjectOut, status_code=201)
async def create_subject(
    payload: SubjectCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("subject:write")),
) -> SubjectOut:
    return SubjectOut.model_validate(await service.create_subject(session, ctx, payload))


@router.get("/subjects", response_model=list[SubjectOut])
async def list_subjects(
    department_id: uuid.UUID | None = None,
    kind: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=500, le=2000),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("subject:read")),
) -> list[SubjectOut]:
    subjects = await service.list_subjects(
        session, department_id, kind, search, include_inactive, limit
    )
    return [SubjectOut.model_validate(s) for s in subjects]


@router.put("/subjects/{subject_id}", response_model=SubjectOut)
async def update_subject(
    subject_id: uuid.UUID,
    payload: SubjectUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("subject:write")),
) -> SubjectOut:
    changes = payload.model_dump(exclude_unset=True)
    return SubjectOut.model_validate(
        await service.update_subject(session, ctx, subject_id, changes)
    )


@router.post("/subjects/imports")
async def import_subjects(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("subject:write")),
) -> dict[str, object]:
    """Bulk subject catalogue — one row per offering; partial commit."""
    content = await file.read()
    return await service.import_subjects(session, ctx, file.filename or "subjects.csv", content)


@router.post("/offerings", response_model=OfferingOut, status_code=201)
async def create_offering(
    payload: OfferingCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("subject:write")),
) -> OfferingOut:
    offering = await service.create_offering(
        session, ctx, payload.subject_id, payload.program_id, payload.position
    )
    subject = await service.get_subject(session, offering.subject_id)
    return OfferingOut.model_validate(
        {
            "id": offering.id,
            "subject_id": offering.subject_id,
            "program_id": offering.program_id,
            "position": offering.position,
            "status": offering.status,
            "subject": subject,
        }
    )


@router.get("/programmes/{program_id}/offerings", response_model=list[OfferingOut])
async def list_offerings(
    program_id: uuid.UUID,
    position: int | None = None,
    kind: str | None = None,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("subject:read")),
) -> list[OfferingOut]:
    """A Programme's curriculum — what is taught, and at which position."""
    rows = await service.list_offerings(session, program_id, position, kind)
    return [OfferingOut.model_validate(row) for row in rows]


@router.post("/venues", response_model=VenueOut, status_code=201)
async def create_venue(
    payload: VenueCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("venue:write")),
) -> VenueOut:
    return VenueOut.model_validate(
        await service.create_venue(session, ctx, payload.model_dump())
    )


@router.get("/venues", response_model=list[VenueOut])
async def list_venues(
    kind: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=500, le=2000),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("venue:read")),
) -> list[VenueOut]:
    venues = await service.list_venues(session, kind, search, include_inactive, limit)
    return [VenueOut.model_validate(v) for v in venues]


@router.put("/venues/{venue_id}", response_model=VenueOut)
async def update_venue(
    venue_id: uuid.UUID,
    payload: VenueUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("venue:write")),
) -> VenueOut:
    changes = payload.model_dump(exclude_unset=True)
    return VenueOut.model_validate(await service.update_venue(session, ctx, venue_id, changes))


@router.post("/venues/imports")
async def import_venues(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("venue:write")),
) -> dict[str, object]:
    content = await file.read()
    return await service.import_venues(session, ctx, file.filename or "venues.csv", content)
