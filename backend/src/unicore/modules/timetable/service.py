"""Business rules for the timetable module. The only layer other modules may call.

Milestone-2 slice: per-School academic terms (TTM-FR-18) and per-term Section
instances (TTM-FR-19). Term dates are per School — one campus hosts semester- and
year-based Schools simultaneously.
"""

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.logging import get_logger
from unicore.core.security import AuthContext
from unicore.core.templates import strip_comments
from unicore.modules.audit import service as audit_service
from unicore.modules.onboarding import service as onboarding_service
from unicore.modules.org import service as org_service
from unicore.modules.timetable import dao
from unicore.modules.timetable.models import TERM_PARITIES, AcademicTerm
from unicore.modules.timetable.schemas import (
    SECTION_CSV_COLUMNS,
    MultiSchoolTermCreate,
    TermCreate,
)


def _snapshot(term: AcademicTerm) -> dict[str, str | int | None]:
    return {
        "term_code": term.term_code,
        "version": term.version,
        "status": term.status,
        "start_date": term.start_date.isoformat(),
        "end_date": term.end_date.isoformat(),
        "parity": term.parity,
    }


async def _add_term_version(
    session: AsyncSession, ctx: AuthContext, school: org_service.OrgUnit, data: TermCreate
) -> AcademicTerm:
    """Append a draft version of a term for one School. Does not commit — the
    caller decides the transaction boundary (one School, or a multi-School fan-out)."""
    previous = await dao.latest_version(session, school.id, data.term_code)
    term = AcademicTerm(
        school_id=school.id,
        term_code=data.term_code,
        version=(previous.version + 1) if previous else 1,
        start_date=data.start_date,
        end_date=data.end_date,
        parity=data.parity,
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
    return term


async def upload_term(
    session: AsyncSession, ctx: AuthContext, data: TermCreate
) -> AcademicTerm:
    """School office staff upload; the term is inactive until approved."""
    school = await org_service.get_unit(session, data.school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="Academic terms belong to a School.")
    if school.status != "active":
        raise HTTPException(status_code=409, detail="School is deactivated.")

    term = await _add_term_version(session, ctx, school, data)
    await session.commit()
    return term


async def upload_term_multi(
    session: AsyncSession, ctx: AuthContext, data: MultiSchoolTermCreate
) -> list[dict[str, object]]:
    """Apply one set of dates to several Schools (TTM-FR-25).

    Fans out into an independent draft per School rather than one shared record.
    That is the whole point: calendar approval is a School-scoped power, so a
    shared calendar would let one School Incharge's approval bind another School.
    Each School Incharge still approves — and may amend — their own.
    """
    results: list[dict[str, object]] = []
    for school_id in dict.fromkeys(data.school_ids):  # de-dupe, keep order
        school = await org_service.get_unit(session, school_id)
        if school.type != "school":
            results.append(
                {
                    "school_id": school_id,
                    "school_name": school.name,
                    "outcome": "skipped",
                    "detail": f"not a School (it is a {school.type})",
                }
            )
            continue
        if school.status != "active":
            results.append(
                {
                    "school_id": school_id,
                    "school_name": school.name,
                    "outcome": "skipped",
                    "detail": "School is deactivated",
                }
            )
            continue

        previous = await dao.latest_version(session, school_id, data.term_code)
        term = await _add_term_version(
            session,
            ctx,
            school,
            TermCreate(
                school_id=school_id,
                term_code=data.term_code,
                start_date=data.start_date,
                end_date=data.end_date,
                parity=data.parity,
                exam_ranges=data.exam_ranges,
                special_events=data.special_events,
                archival_backstop_date=data.archival_backstop_date,
            ),
        )
        results.append(
            {
                "school_id": school_id,
                "school_name": school.name,
                # "versioned" tells the caller this School already had the term:
                # the new draft supersedes only once its own Incharge approves it.
                "outcome": "versioned" if previous else "created",
                "version": term.version,
                "detail": None,
            }
        )

    await session.commit()
    get_logger().info(
        "multi-school calendar applied",
        term_code=data.term_code,
        schools=len(results),
        created=sum(1 for r in results if r["outcome"] == "created"),
        versioned=sum(1 for r in results if r["outcome"] == "versioned"),
    )
    return results


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


async def set_term_parity(
    session: AsyncSession, ctx: AuthContext, term_id: uuid.UUID, parity: str
) -> AcademicTerm:
    """Fill in parity on a term that predates the field — a backfill, not an amendment.

    Parity was added after calendars already existed, so those terms carry NULL and
    Section generation refuses to run against them. Setting it is allowed exactly
    once, while it is still empty: *changing* a stated parity would silently move
    which half of every ladder is live, and that goes through the normal versioned
    amendment path (TTM-FR-18) so the School Incharge re-approves it.
    """
    term = await dao.get_term(session, term_id)
    if term is None:
        raise HTTPException(status_code=404, detail="Term not found.")
    if term.parity is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Parity is already '{term.parity}' for {term.term_code}. Changing it "
                "means amending the calendar — upload a new version for re-approval."
            ),
        )
    if parity not in TERM_PARITIES:
        raise HTTPException(status_code=422, detail="Parity must be 'odd' or 'even'.")

    before = _snapshot(term)
    term.parity = parity
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.term.parity-set",
        object_type="academic_term",
        object_id=str(term.id),
        before=before,
        after=_snapshot(term),
    )
    await session.commit()
    return term


async def list_terms(session: AsyncSession, school_id: uuid.UUID) -> list[AcademicTerm]:
    return list(await dao.list_terms(session, school_id))


async def list_all_terms(session: AsyncSession) -> list[AcademicTerm]:
    return list(await dao.list_all_terms(session))


async def section_plan(
    session: AsyncSession, school_id: uuid.UUID, term_code: str
) -> list[dict[str, object]]:
    """Every Programme in a School beside the Sections it has open for one term.

    Term setup is the act of closing the gap between the two, so the screen needs
    them together — a Programme with no Sections this term is the thing the
    Timetable Cell is looking for, and it cannot be seen in a list of Sections.
    """
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="Section plans are per School.")

    programmes = await org_service.list_descendants_of_type(session, school_id, "program")
    sections = await org_service.list_descendants_of_type(session, school_id, "section")

    by_programme: dict[uuid.UUID | None, list[object]] = {}
    for section in sections:
        if section.term_code == term_code:
            by_programme.setdefault(section.parent_id, []).append(section)

    return [
        {
            "programme": programme,
            "sections": sorted(
                by_programme.get(programme.id, []),
                key=lambda s: getattr(s, "name", ""),
            ),
        }
        for programme in programmes
    ]


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


# --- Section generation (TTM-FR-22/23/24) ------------------------------------

_ROMAN = (
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def _roman(n: int) -> str:
    out = ""
    for value, numeral in _ROMAN:
        while n >= value:
            out += numeral
            n -= value
    return out


def _letter(index: int) -> str:
    """A, B, … Z, AA, AB — division letters never run out."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


async def _label_template(session: AsyncSession, cadence: str) -> str:
    return await org_service.get_setting(session, f"section_label_template_{cadence}")


def _render_label(template: str, position: int, letter: str) -> str:
    return template.format(
        position=position, position_roman=_roman(position), letter=letter
    )


async def generation_plan(
    session: AsyncSession,
    ctx: AuthContext,
    school_id: uuid.UUID,
    term_code: str,
    expected_intake: dict[str, int] | None = None,
) -> dict[str, object]:
    """Propose the term's Section ladder for a School — a proposal, never a commit.

    For every active Programme, walk the positions that are *live* this term
    (parity halves a semester ladder) and divide the headcount at each by the
    School's class-size cap. Existing Sections are reported, never touched.
    """
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="Section generation is per School.")

    term = await require_approved_term(session, school_id, term_code)
    if term.parity is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Term '{term_code}' has no parity set for {school.name}. Parity decides "
                "which half of every semester ladder is live — set it on the calendar."
            ),
        )

    cap = await org_service.class_size_cap(session, school)
    programmes = await org_service.list_descendants_of_type(session, school_id, "program")
    sections = await org_service.list_descendants_of_type(session, school_id, "section")
    intake = expected_intake or {}

    by_key: dict[tuple[uuid.UUID | None, int | None], list[org_service.OrgUnit]] = {}
    for section in sections:
        if section.term_code == term_code:
            by_key.setdefault((section.parent_id, section.position), []).append(section)

    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for programme in programmes:
        try:
            cadence = await org_service.effective_cadence(session, programme)
        except HTTPException:
            warnings.append(f"{programme.name}: no cadence on the Programme or its School")
            continue
        if not programme.duration_years:
            # Guessing a duration would create Sections for terms that do not exist.
            warnings.append(f"{programme.name}: no duration set — skipped")
            continue

        template = await _label_template(session, cadence)
        headcounts = await onboarding_service.headcount_by_position(session, programme.id)
        for position in org_service.live_positions(
            cadence, programme.duration_years, term.parity
        ):
            existing = sorted(
                by_key.get((programme.id, position), []), key=lambda s: s.name
            )
            roster = headcounts.get(position, 0)
            key = f"{programme.id}:{position}"
            if roster:
                headcount, source = roster, "roster"
            elif key in intake:
                headcount, source = intake[key], "expected"
            else:
                headcount, source = 0, "none"

            # A position with no headcount still needs somewhere to put students,
            # so it never proposes zero.
            required = max(1, -(-headcount // cap))
            to_create = [
                _render_label(template, position, _letter(len(existing) + i))
                for i in range(max(0, required - len(existing)))
            ]
            rows.append(
                {
                    "programme_id": programme.id,
                    "programme_name": programme.name,
                    "programme_code": programme.code,
                    "cadence": cadence,
                    "position": position,
                    "year": org_service.year_of(cadence, position),
                    "headcount": headcount,
                    "headcount_source": source,
                    "class_size_cap": cap,
                    "required": required,
                    "existing": existing,
                    "to_create": to_create,
                }
            )

    return {
        "term_code": term_code,
        "parity": term.parity,
        "school_id": school.id,
        "school_name": school.name,
        "rows": rows,
        "warnings": warnings,
    }


async def generate_sections(
    session: AsyncSession,
    ctx: AuthContext,
    school_id: uuid.UUID,
    term_code: str,
    expected_intake: dict[str, int] | None = None,
) -> dict[str, object]:
    """Commit the proposal. Idempotent: a re-run creates only what is missing."""
    plan = await generation_plan(session, ctx, school_id, term_code, expected_intake)
    created = []
    existing_count = 0
    for row in cast(list[dict[str, object]], plan["rows"]):
        already = len(cast(list[org_service.OrgUnit], row["existing"]))
        existing_count += already
        position = cast(int, row["position"])
        for offset, label in enumerate(cast(list[str], row["to_create"])):
            section = await org_service.create_section_instance(
                session,
                ctx,
                cast(uuid.UUID, row["programme_id"]),
                label,
                term_code,
                position=position,
                # Continue the letter run past what already exists, so a
                # re-generation never reuses A when A is already taken.
                division_letter=_letter(already + offset),
            )
            created.append(section)

    get_logger().info(
        "sections generated",
        school_id=str(school_id),
        term_code=term_code,
        created=len(created),
        existing=existing_count,
    )
    await session.commit()
    return {
        "created": created,
        "existing": existing_count,
        "warnings": plan["warnings"],
    }


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

    body = strip_comments(text_content)
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
