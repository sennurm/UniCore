"""HTTP endpoints for the onboarding module. No business logic here (see ARCHITECTURE.md)."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.onboarding import service
from unicore.modules.onboarding.schemas import (
    AllotRequest,
    BatchOut,
    EnrollmentImportResult,
    RowErrorOut,
    SingleStudentAdd,
    TransferRequest,
)
from unicore.modules.rbac.service import require_permission

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/imports", response_model=BatchOut, status_code=201)
async def import_csv(
    term_code: str = Form(...),
    file: UploadFile = File(...),
    default_program_code: str | None = Form(default=None),
    default_position: int | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:import")),
) -> BatchOut:
    """The two `default_*` fields are the upload screen's pickers; they fill blank
    cells only, so a value in the file always wins (ONB-FR-21)."""
    content = await file.read()
    batch = await service.import_csv(
        session,
        ctx,
        file.filename or "upload.csv",
        content,
        term_code,
        service.ImportDefaults(
            program_code=default_program_code, position=default_position
        ),
    )
    return BatchOut.model_validate(batch)


@router.get("/imports", response_model=list[BatchOut])
async def list_batches(
    limit: int = Query(default=25, le=200),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> list[BatchOut]:
    batches = await service.list_batches(session, ctx, limit)
    return [BatchOut.model_validate(b) for b in batches]


@router.get("/imports/{batch_id}/errors", response_model=list[RowErrorOut])
async def batch_errors(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> list[RowErrorOut]:
    errors = await service.batch_errors(session, ctx, batch_id)
    return [RowErrorOut.model_validate(e) for e in errors]


@router.get("/imports/{batch_id}/errors.csv", response_class=PlainTextResponse)
async def error_report(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:read")),
) -> PlainTextResponse:
    errors = await service.batch_errors(session, ctx, batch_id)
    return PlainTextResponse(
        service.error_report_csv(errors),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="errors_{batch_id}.csv"'},
    )


@router.post("/imports/{batch_id}/confirm", response_model=BatchOut)
async def confirm_batch(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:transfer")),
) -> BatchOut:
    return BatchOut.model_validate(await service.confirm_batch(session, ctx, batch_id))


@router.post("/imports/{batch_id}/deliver-credentials")
async def deliver_credentials(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("onb:import")),
) -> dict[str, int]:
    return await service.deliver_credentials(session, ctx, batch_id)


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
