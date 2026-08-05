"""HTTP endpoints for the timetable module. No business logic here (see ARCHITECTURE.md)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.rbac.service import require_permission
from unicore.modules.timetable import service
from unicore.modules.timetable.schemas import (
    ApprovalDecision,
    CalendarExceptionCreate,
    CalendarExceptionOut,
    DraftCreate,
    DraftOut,
    DraftStatusOut,
    EntryCreate,
    EntryResult,
    GenerationPlanOut,
    GenerationRequest,
    GenerationResultOut,
    HolidayCreate,
    HolidayOut,
    HolidayUpdate,
    MultiSchoolTermCreate,
    PeriodGridCreate,
    PeriodGridOut,
    PersonalTimetableOut,
    ProgrammeSectionsOut,
    ResolvedDayOut,
    SchoolTermResult,
    SectionCreate,
    SectionOut,
    TermCreate,
    TermOut,
    TermParitySet,
    TimetableRowOut,
    WorkingPatternOut,
    WorkingPatternUpdate,
)

router = APIRouter(prefix="/timetable", tags=["timetable"])


def _ctx(request: Request) -> AuthContext:
    """Own-data endpoints resolve their subject from the token, never the client."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None:  # pragma: no cover — the gate rejects earlier
        raise HTTPException(status_code=401, detail="Unauthenticated.")
    return ctx


@router.get("/me", response_model=PersonalTimetableOut)
async def my_timetable(
    request: Request,
    term_code: str = Query(..., min_length=1, max_length=50),
    session: AsyncSession = Depends(get_session),
) -> PersonalTimetableOut:
    """Your own published timetable (TTM-FR-13).

    A student sees their Section with electives merged — only the alternative
    they chose. A Faculty Member sees their own load across every School.
    Neither sees a draft.
    """
    return PersonalTimetableOut.model_validate(
        await service.my_timetable(session, _ctx(request), term_code)
    )


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


# --- period grids, drafts, entries, approvals, publish ------------------------


@router.post("/grids", response_model=PeriodGridOut, status_code=201)
async def create_grid(
    payload: PeriodGridCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:grid-write")),
) -> PeriodGridOut:
    """A new grid version for a School. Grids are versioned, never edited."""
    grid = await service.create_grid(
        session,
        ctx,
        payload.school_id,
        payload.name,
        [p.model_dump() for p in payload.periods],
    )
    grids = await service.list_grids(session, payload.school_id)
    return PeriodGridOut.model_validate(next(g for g in grids if g["id"] == grid.id))


@router.get("/schools/{school_id}/grids", response_model=list[PeriodGridOut])
async def list_grids(
    school_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-read")),
) -> list[PeriodGridOut]:
    return [PeriodGridOut.model_validate(g) for g in await service.list_grids(session, school_id)]


@router.post("/drafts", response_model=DraftOut, status_code=201)
async def create_draft(
    payload: DraftCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-write")),
) -> DraftOut:
    draft = await service.create_draft(session, ctx, payload.school_id, payload.term_code)
    return DraftOut.model_validate(draft)


@router.post("/drafts/{draft_id}/entries", response_model=EntryResult, status_code=201)
async def add_entry(
    draft_id: uuid.UUID,
    payload: EntryCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-write")),
) -> EntryResult:
    """Place one class. Clashes are refused (409); a too-small venue warns and
    needs `acknowledge_capacity` to proceed."""
    result = await service.add_entry(
        session,
        ctx,
        draft_id,
        payload.section_id,
        payload.day_of_week,
        payload.period_id,
        payload.offering_id,
        payload.faculty_user_id,
        payload.venue_id,
        payload.acknowledge_capacity,
    )
    return EntryResult.model_validate(result)


@router.delete("/entries/{entry_id}", status_code=204)
async def remove_entry(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-write")),
) -> None:
    await service.remove_entry(session, ctx, entry_id)


@router.get("/drafts/{draft_id}", response_model=DraftStatusOut)
async def draft_status(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-read")),
) -> DraftStatusOut:
    """Approval state and everything blocking publication."""
    return DraftStatusOut.model_validate(await service.draft_status(session, draft_id))


@router.get("/drafts/{draft_id}/entries", response_model=list[TimetableRowOut])
async def timetable_view(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-read")),
) -> list[TimetableRowOut]:
    rows = await service.timetable_view(session, draft_id)
    return [TimetableRowOut.model_validate(r) for r in rows]


@router.post("/drafts/{draft_id}/approvals", status_code=200)
async def decide_approval(
    draft_id: uuid.UUID,
    payload: ApprovalDecision,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-approve")),
) -> dict[str, str]:
    """An HoD signs off the portion of this draft touching their Department."""
    approval = await service.decide_approval(
        session, ctx, draft_id, payload.department_id, payload.approve, payload.reason
    )
    return {"status": approval.status}


@router.post("/drafts/{draft_id}/publish", response_model=DraftOut)
async def publish_draft(
    draft_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:draft-publish")),
) -> DraftOut:
    """Make this the term's source of truth for the School."""
    return DraftOut.model_validate(await service.publish_draft(session, ctx, draft_id))


# --- calendar (TTM-FR-26/27/28) ----------------------------------------------


@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=500, le=2000),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-read")),
) -> list[HolidayOut]:
    rows = await service.list_holidays(session, from_date, to_date, limit)
    return [HolidayOut.model_validate(r) for r in rows]


@router.post("/holidays", response_model=HolidayOut, status_code=201)
async def create_holiday(
    payload: HolidayCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:holiday-write")),
) -> HolidayOut:
    """Close a date range for the university (System Admin)."""
    return HolidayOut.model_validate(await service.create_holiday(session, ctx, payload))


@router.put("/holidays/{holiday_id}", response_model=HolidayOut)
async def update_holiday(
    holiday_id: uuid.UUID,
    payload: HolidayUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:holiday-write")),
) -> HolidayOut:
    return HolidayOut.model_validate(
        await service.update_holiday(session, ctx, holiday_id, payload)
    )


@router.post("/holidays/{holiday_id}/withdraw", response_model=HolidayOut)
async def withdraw_holiday(
    holiday_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:holiday-write")),
) -> HolidayOut:
    """Withdrawn rather than deleted — a date that used to be closed is exactly
    what an audit asks about later."""
    return HolidayOut.model_validate(await service.withdraw_holiday(session, ctx, holiday_id))


@router.get("/schools/{school_id}/working-pattern", response_model=WorkingPatternOut)
async def get_working_pattern(
    school_id: uuid.UUID,
    term_code: str | None = None,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-read")),
) -> WorkingPatternOut:
    days, is_default = await service.pattern_for(session, school_id, term_code)
    return WorkingPatternOut(
        school_id=school_id, term_code=term_code, days=days, is_default=is_default
    )


@router.put("/schools/{school_id}/working-pattern", response_model=WorkingPatternOut)
async def set_working_pattern(
    school_id: uuid.UUID,
    payload: WorkingPatternUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-write")),
) -> WorkingPatternOut:
    """Which weekdays this School teaches — a Nursing School may run all seven."""
    row = await service.set_working_pattern(session, ctx, school_id, payload)
    return WorkingPatternOut(
        school_id=school_id, term_code=row.term_code, days=row.days, is_default=False
    )


@router.get("/schools/{school_id}/calendar", response_model=list[ResolvedDayOut])
async def resolve_calendar(
    school_id: uuid.UUID,
    from_date: date,
    to_date: date,
    term_code: str | None = None,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-read")),
) -> list[ResolvedDayOut]:
    """The resolved teaching days for a School — the range form ATT, LVE and TSK
    read, each answer naming the layer that decided it."""
    rows = await service.resolve_days(session, school_id, from_date, to_date, term_code)
    return [ResolvedDayOut.model_validate(r) for r in rows]


@router.get("/schools/{school_id}/exceptions", response_model=list[CalendarExceptionOut])
async def list_exceptions(
    school_id: uuid.UUID,
    from_date: date,
    to_date: date,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-read")),
) -> list[CalendarExceptionOut]:
    rows = await service.list_calendar_exceptions(session, school_id, from_date, to_date)
    return [CalendarExceptionOut.model_validate(r) for r in rows]


@router.post(
    "/schools/{school_id}/exceptions", response_model=CalendarExceptionOut, status_code=201
)
async def add_exception(
    school_id: uuid.UUID,
    payload: CalendarExceptionCreate,
    term_code: str | None = None,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-write")),
) -> CalendarExceptionOut:
    """A closed day, or a day worked anyway — including a compensatory day that
    follows another weekday's timetable."""
    return CalendarExceptionOut.model_validate(
        await service.add_calendar_exception(session, ctx, school_id, payload, term_code)
    )


@router.delete("/schools/{school_id}/exceptions/{on_date}", status_code=204)
async def remove_exception(
    school_id: uuid.UUID,
    on_date: date,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("ttm:calendar-write")),
) -> None:
    await service.remove_calendar_exception(session, ctx, school_id, on_date)
