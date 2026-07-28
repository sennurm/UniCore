"""HTTP endpoints for the timetable module. No business logic here (see ARCHITECTURE.md)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.rbac.service import require_permission
from unicore.modules.timetable import service
from unicore.modules.timetable.schemas import (
    SectionCreate,
    SectionOut,
    TermCreate,
    TermOut,
)

router = APIRouter(prefix="/timetable", tags=["timetable"])


@router.post("/terms", response_model=TermOut, status_code=201)
async def upload_term(
    payload: TermCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-upload")),
) -> TermOut:
    return TermOut.model_validate(await service.upload_term(session, ctx, payload))


@router.post("/terms/{term_id}/approve", response_model=TermOut)
async def approve_term(
    term_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-approve")),
) -> TermOut:
    return TermOut.model_validate(await service.approve_term(session, ctx, term_id))


@router.get("/schools/{school_id}/terms", response_model=list[TermOut])
async def list_terms(
    school_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-read")),
) -> list[TermOut]:
    terms = await service.list_terms(session, school_id)
    return [TermOut.model_validate(t) for t in terms]


@router.post("/sections", response_model=SectionOut, status_code=201)
async def create_section(
    payload: SectionCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:section-create")),
) -> SectionOut:
    section = await service.create_section(
        session, ctx, payload.program_id, payload.label, payload.term_code
    )
    return SectionOut.model_validate(section)
