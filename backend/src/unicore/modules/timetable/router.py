"""HTTP endpoints for the timetable module. No business logic here (see ARCHITECTURE.md)."""

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.rbac.service import require_permission
from unicore.modules.timetable import service
from unicore.modules.timetable.schemas import (
    GenerationPlanOut,
    GenerationRequest,
    GenerationResultOut,
    MultiSchoolTermCreate,
    ProgrammeSectionsOut,
    SchoolTermResult,
    SectionCreate,
    SectionOut,
    TermCreate,
    TermOut,
    TermParitySet,
)

router = APIRouter(prefix="/timetable", tags=["timetable"])


@router.post("/terms", response_model=TermOut, status_code=201)
async def upload_term(
    payload: TermCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-upload")),
) -> TermOut:
    return TermOut.model_validate(await service.upload_term(session, ctx, payload))


@router.post("/terms/multi", response_model=list[SchoolTermResult], status_code=201)
async def upload_term_multi(
    payload: MultiSchoolTermCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-upload-multi")),
) -> list[SchoolTermResult]:
    """Apply one calendar to several Schools; each gets its own draft to approve."""
    results = await service.upload_term_multi(session, ctx, payload)
    return [SchoolTermResult.model_validate(r) for r in results]


@router.get("/schools/{school_id}/generation-plan", response_model=GenerationPlanOut)
async def generation_plan(
    school_id: uuid.UUID,
    term_code: str = Query(..., min_length=1, max_length=50),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:section-read")),
) -> GenerationPlanOut:
    """The proposed Section ladder for a term — read-only; nothing is created."""
    plan = await service.generation_plan(session, ctx, school_id, term_code)
    return GenerationPlanOut.model_validate(plan)


@router.post("/schools/{school_id}/generate-sections", response_model=GenerationResultOut)
async def generate_sections(
    school_id: uuid.UUID,
    payload: GenerationRequest,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:section-create")),
) -> GenerationResultOut:
    """Commit the proposal. Idempotent — a re-run creates only what is missing."""
    result = await service.generate_sections(
        session, ctx, school_id, payload.term_code, payload.expected_intake
    )
    return GenerationResultOut.model_validate(result)


@router.post("/terms/{term_id}/approve", response_model=TermOut)
async def approve_term(
    term_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-approve")),
) -> TermOut:
    return TermOut.model_validate(await service.approve_term(session, ctx, term_id))


@router.patch("/terms/{term_id}/parity", response_model=TermOut)
async def set_term_parity(
    term_id: uuid.UUID,
    payload: TermParitySet,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-set-parity")),
) -> TermOut:
    """Backfill parity on a calendar that predates the field. Allowed once, while
    it is empty; changing a stated parity goes through versioned amendment."""
    return TermOut.model_validate(
        await service.set_term_parity(session, ctx, term_id, payload.parity)
    )


@router.post("/sections/imports", status_code=201)
async def import_sections(
    term_code: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:section-create")),
) -> dict[str, object]:
    """Bulk Section-instance creation for a term, from the CSV template."""
    content = await file.read()
    return await service.import_sections(session, ctx, content, term_code)


@router.get("/terms", response_model=list[TermOut])
async def list_all_terms(
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:term-read")),
) -> list[TermOut]:
    """Calendar status for every School, so term setup can show it in one list."""
    return [TermOut.model_validate(t) for t in await service.list_all_terms(session)]


@router.get("/schools/{school_id}/section-plan", response_model=list[ProgrammeSectionsOut])
async def section_plan(
    school_id: uuid.UUID,
    term_code: str = Query(..., min_length=1, max_length=50),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:section-read")),
) -> list[ProgrammeSectionsOut]:
    """One School's Programmes with the Sections open for a term."""
    plan = await service.section_plan(session, school_id, term_code)
    return [ProgrammeSectionsOut.model_validate(row) for row in plan]


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
