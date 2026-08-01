"""HTTP endpoints for the onboarding module. No business logic here (see ARCHITECTURE.md)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.onboarding import service
from unicore.modules.onboarding.schemas import (
    AllotRequest,
    ElectiveChoiceRequest,
    ElectiveGroupOut,
    EnrollmentImportResult,
    ImportRunOut,
    RowErrorOut,
    SingleStudentAdd,
    TransferRequest,
)
from unicore.modules.rbac.service import require_permission

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _ctx(request: Request) -> AuthContext:
    """Own-data endpoints resolve their subject from the token, never from the
    client (project security rule)."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None:  # pragma: no cover — the gate rejects earlier
        raise HTTPException(status_code=401, detail="Unauthenticated.")
    return ctx


@router.post("/imports", response_model=ImportRunOut, status_code=201)
async def import_csv(
    term_code: str = Form(...),
    file: UploadFile = File(...),
    default_program_code: str | None = Form(default=None),
    default_position: int | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:import")),
) -> ImportRunOut:
    """The two `default_*` fields are the upload screen's pickers; they fill blank
    cells only, so a value in the file always wins (ONB-FR-21)."""
    content = await file.read()
    run = await service.import_csv(
        session,
        ctx,
        file.filename or "upload.csv",
        content,
        term_code,
        service.ImportDefaults(
            program_code=default_program_code, position=default_position
        ),
    )
    return ImportRunOut.model_validate(run)


@router.get("/imports", response_model=list[ImportRunOut])
async def list_runs(
    limit: int = Query(default=25, le=200),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> list[ImportRunOut]:
    runs = await service.list_runs(session, ctx, limit)
    return [ImportRunOut.model_validate(r) for r in runs]


@router.get("/imports/{run_id}/errors", response_model=list[RowErrorOut])
async def run_errors(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> list[RowErrorOut]:
    errors = await service.run_errors(session, ctx, run_id)
    return [RowErrorOut.model_validate(e) for e in errors]


@router.get("/imports/{run_id}/errors.csv", response_class=PlainTextResponse)
async def error_report(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> PlainTextResponse:
    errors = await service.run_errors(session, ctx, run_id)
    return PlainTextResponse(
        service.error_report_csv(errors),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="errors_{run_id}.csv"'},
    )


@router.post("/imports/{run_id}/confirm", response_model=ImportRunOut)
async def confirm_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:transfer")),
) -> ImportRunOut:
    return ImportRunOut.model_validate(await service.confirm_run(session, ctx, run_id))


@router.post("/imports/{run_id}/deliver-credentials")
async def deliver_credentials(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:import")),
) -> dict[str, int]:
    return await service.deliver_credentials(session, ctx, run_id)


@router.post("/enrollment-ids", response_model=EnrollmentImportResult)
async def import_enrollment_ids(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:import")),
) -> EnrollmentImportResult:
    """Assign enrollment numbers (issued after admission) matched on SIF id."""
    content = await file.read()
    return EnrollmentImportResult.model_validate(
        await service.import_enrollment_ids(session, ctx, content)
    )


@router.post("/students", status_code=201)
async def add_student(
    payload: SingleStudentAdd,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:import")),
) -> dict[str, str]:
    user_id = await service.add_single_student(session, ctx, payload)
    return {"user_id": str(user_id)}


@router.post("/allotments", status_code=201)
async def allot(
    payload: AllotRequest,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:allot")),
) -> dict[str, str]:
    membership = await service.allot_section(
        session, ctx, payload.user_id, payload.section_id, payload.effective_from
    )
    return {"section_id": str(membership.section_id)}


@router.post("/transfers", status_code=202)
async def transfer(
    payload: TransferRequest,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:transfer")),
) -> dict[str, str]:
    await service.transfer_student(
        session,
        ctx,
        payload.user_id,
        payload.new_program_id,
        payload.new_section_id,
        payload.effective_from,
    )
    return {"status": "transferred"}


@router.post("/students/{user_id}/withdraw", status_code=202)
async def withdraw(
    user_id: uuid.UUID,
    reason: str = Form(...),
    effective_from: date = Form(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:withdraw")),
) -> dict[str, str]:
    await service.withdraw_student(session, ctx, user_id, effective_from, reason)
    return {"status": "withdrawn"}


@router.get("/sections/{section_id}/roster")
async def section_roster(
    section_id: uuid.UUID,
    as_of: date | None = None,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> list[dict[str, object]]:
    return await service.section_roster(session, ctx, section_id, as_of)


@router.post("/staff/imports", response_model=ImportRunOut, status_code=201)
async def import_staff(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:staff-import")),
) -> ImportRunOut:
    """Bulk staff provisioning; the designation column grants the matching role."""
    content = await file.read()
    run = await service.import_staff(session, ctx, file.filename or "staff.csv", content)
    return ImportRunOut.model_validate(run)


@router.get("/me/electives", response_model=list[ElectiveGroupOut])
async def my_elective_options(
    term_code: str = Query(..., min_length=1, max_length=50),
    request: Request = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_session),
) -> list[ElectiveGroupOut]:
    """The elective groups open to the signed-in student, and their current pick.

    Own-data endpoint: the subject is the caller, resolved from the AuthContext
    rather than from any client-supplied id (project security rule).
    """
    rows = await service.elective_options(session, _ctx(request), term_code)
    return [ElectiveGroupOut.model_validate(row) for row in rows]


@router.post("/me/electives", status_code=201)
async def choose_my_elective(
    payload: ElectiveChoiceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Pick one subject within an elective group. Re-posting replaces the pick."""
    choice = await service.choose_elective(
        session, _ctx(request), payload.offering_id, payload.term_code
    )
    return {"choice_id": str(choice.id), "elective_group": choice.elective_group}
