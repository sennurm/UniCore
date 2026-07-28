"""Business rules for the timetable module. The only layer other modules may call.

Milestone-2 slice: per-School academic terms (TTM-FR-18) and per-term Section
instances (TTM-FR-19). Term dates are per School — one campus hosts semester- and
year-based Schools simultaneously.
"""

import csv
import io
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.org import service as org_service
from unicore.modules.timetable import dao
from unicore.modules.timetable.models import AcademicTerm
from unicore.modules.timetable.schemas import SECTION_CSV_COLUMNS, TermCreate


def _snapshot(term: AcademicTerm) -> dict[str, str | int | None]:
    return {
        "term_code": term.term_code,
        "version": term.version,
        "status": term.status,
        "start_date": term.start_date.isoformat(),
        "end_date": term.end_date.isoformat(),
    }


async def upload_term(
    session: AsyncSession, ctx: AuthContext, data: TermCreate
) -> AcademicTerm:
    """School office staff upload; the term is inactive until approved."""
    school = await org_service.get_unit(session, data.school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="Academic terms belong to a School.")
    if school.status != "active":
        raise HTTPException(status_code=409, detail="School is deactivated.")

    previous = await dao.latest_version(session, data.school_id, data.term_code)
    version = (previous.version + 1) if previous else 1

    term = AcademicTerm(
        school_id=data.school_id,
        term_code=data.term_code,
        version=version,
        start_date=data.start_date,
        end_date=data.end_date,
        exam_ranges=data.exam_ranges,
        special_events=data.special_events,
        archival_backstop_date=data.archival_backstop_date,
        uploaded_by=ctx.user_id,
    )
    session.add(term)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.term.uploaded",
        object_type="academic_term",
        object_id=str(term.id),
        scope=school.path,
        after=_snapshot(term),
    )
    await session.commit()
    return term


async def approve_term(
    session: AsyncSession, ctx: AuthContext, term_id: uuid.UUID
) -> AcademicTerm:
    """School Incharge approval — the recorded gate that makes a term active."""
    term = await dao.get_term(session, term_id)
    if term is None:
        raise HTTPException(status_code=404, detail="Term not found.")
    if term.status != "draft":
        raise HTTPException(status_code=409, detail=f"Term is already {term.status}.")

    before = _snapshot(term)
    current = await dao.approved_term(session, term.school_id, term.term_code)
    if current is not None:
        current.status = "superseded"  # amendments supersede the prior version
    term.status = "approved"
    term.approved_by = ctx.user_id
    term.approved_at = datetime.now(UTC)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.term.approved",
        object_type="academic_term",
        object_id=str(term.id),
        before=before,
        after=_snapshot(term),
    )
    await session.commit()
    return term


async def list_terms(session: AsyncSession, school_id: uuid.UUID) -> list[AcademicTerm]:
    return list(await dao.list_terms(session, school_id))


async def require_approved_term(
    session: AsyncSession, school_id: uuid.UUID, term_code: str
) -> AcademicTerm:
    term = await dao.approved_term(session, school_id, term_code)
    if term is None:
        raise HTTPException(
            status_code=409,
            detail=f"No approved academic term '{term_code}' for this School.",
        )
    return term


async def create_section(
    session: AsyncSession,
    ctx: AuthContext,
    program_id: uuid.UUID,
    label: str,
    term_code: str,
) -> object:
    """Timetable Cell term setup (TTM-FR-19). Requires an approved term for the
    owning School so Sections can never predate their calendar."""
    program = await org_service.get_unit(session, program_id)
    if program.type != "program":
        raise HTTPException(status_code=422, detail="Sections are created under a Program.")
    school_id = await org_service.ancestor_of_type(session, program_id, "school")
    if school_id is None:
        raise HTTPException(status_code=422, detail="Program has no owning School.")
    await require_approved_term(session, school_id, term_code)
    return await org_service.create_section_instance(session, ctx, program_id, label, term_code)


async def import_sections(
    session: AsyncSession, ctx: AuthContext, content: bytes, term_code: str
) -> dict[str, object]:
    """Bulk Section instances from the CSV template; partial commit with errors."""
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    body = "\n".join(
        line for line in text_content.splitlines() if not line.lstrip().startswith("#")
    )
    reader = csv.DictReader(io.StringIO(body))
    missing = set(SECTION_CSV_COLUMNS) - set(h.strip() for h in (reader.fieldnames or []))
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header does not match the section template — missing: "
            f"{', '.join(sorted(missing))}.",
        )

    created = 0
    errors: list[dict[str, object]] = []
    for row_number, raw in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        program_path = row.get("program_path", "").lower()
        label = row.get("label", "")
        try:
            if not program_path or not label:
                raise HTTPException(status_code=422, detail="program_path and label are required")
            program = await org_service.get_unit_by_path(session, program_path)
            if program is None:
                raise HTTPException(
                    status_code=422, detail=f"no Program at path '{program_path}'"
                )
            await create_section(session, ctx, program.id, label, term_code)
            created += 1
        except HTTPException as err:
            errors.append(
                {"row_number": row_number, "reason": str(err.detail), "raw_row": str(row)}
            )
    return {"rows_created": created, "rows_rejected": len(errors), "errors": errors}
