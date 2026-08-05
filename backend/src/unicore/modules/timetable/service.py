"""Business rules for the timetable module. The only layer other modules may call.

Milestone-2 slice: per-School academic terms (TTM-FR-18) and per-term Section
instances (TTM-FR-19). Term dates are per School — one campus hosts semester- and
year-based Schools simultaneously.
"""

import csv
import io
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.logging import get_logger
from unicore.core.security import AuthContext
from unicore.core.templates import strip_comments
from unicore.modules.audit import service as audit_service
from unicore.modules.onboarding import service as onboarding_service
from unicore.modules.org import service as org_service
from unicore.modules.rbac import service as rbac_service
from unicore.modules.timetable import dao
from unicore.modules.timetable.models import (
    DEFAULT_WORKING_DAYS,
    TERM_PARITIES,
    AcademicTerm,
    Period,
    PeriodGrid,
    SchoolCalendarException,
    SchoolWorkingPattern,
    TimetableApproval,
    TimetableDraft,
    TimetableEntry,
    UniversityHoliday,
)
from unicore.modules.timetable.schemas import (
    SECTION_CSV_COLUMNS,
    CalendarExceptionCreate,
    HolidayCreate,
    HolidayUpdate,
    MultiSchoolTermCreate,
    TermCreate,
    WorkingPatternUpdate,
)
from unicore.modules.user import service as user_service


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


# --- period grids (TTM-FR-02) -------------------------------------------------


async def create_grid(
    session: AsyncSession, ctx: AuthContext, school_id: uuid.UUID, name: str,
    periods: list[dict[str, object]],
) -> PeriodGrid:
    """A new grid version for a School. Grids are never edited in place once a
    timetable references them — a change would silently move classes for people
    already holding the published schedule, so a new version plus republish is
    the only path (TTM-FR-02)."""
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="A period grid belongs to a School.")
    if not periods:
        raise HTTPException(status_code=422, detail="A grid needs at least one Period.")

    previous = await dao.latest_grid(session, school_id)
    grid = PeriodGrid(
        school_id=school_id,
        version=(previous.version + 1) if previous else 1,
        name=name,
        status="active",
        created_by=ctx.user_id,
    )
    session.add(grid)
    await session.flush()

    ordered = sorted(periods, key=lambda p: cast(int, p["sequence"]))
    last_end: time | None = None
    for spec in ordered:
        start, end = cast(time, spec["start_time"]), cast(time, spec["end_time"])
        if end <= start:
            raise HTTPException(
                status_code=422,
                detail=f"Period '{spec['name']}' ends at or before it starts.",
            )
        if last_end is not None and start < last_end:
            # Overlapping Periods inside one grid would make every Section in the
            # School clash with itself.
            raise HTTPException(
                status_code=422,
                detail=f"Period '{spec['name']}' starts before the previous one ends.",
            )
        last_end = end
        session.add(
            Period(
                grid_id=grid.id,
                name=cast(str, spec["name"]),
                sequence=cast(int, spec["sequence"]),
                start_time=start,
                end_time=end,
            )
        )

    if previous is not None and previous.status == "active":
        previous.status = "superseded"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.grid.created",
        object_type="period_grid",
        object_id=str(grid.id),
        scope=school.path,
        after={"name": name, "version": grid.version, "periods": len(ordered)},
    )
    await session.commit()
    return grid


async def list_grids(session: AsyncSession, school_id: uuid.UUID) -> list[dict[str, object]]:
    grids = await dao.list_grids(session, school_id)
    return [
        {
            "id": grid.id,
            "school_id": grid.school_id,
            "version": grid.version,
            "name": grid.name,
            "status": grid.status,
            "periods": list(await dao.list_periods(session, grid.id)),
        }
        for grid in grids
    ]


# --- drafts (TTM-FR-03/08/09) -------------------------------------------------


async def create_draft(
    session: AsyncSession, ctx: AuthContext, school_id: uuid.UUID, term_code: str
) -> TimetableDraft:
    """Open a draft for a School's term, on that School's active grid."""
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="A timetable draft belongs to a School.")
    await require_approved_term(session, school_id, term_code)

    grid = await dao.active_grid(session, school_id)
    if grid is None:
        raise HTTPException(
            status_code=409,
            detail=f"{school.name} has no active period grid — define one before drafting.",
        )
    open_draft = await dao.latest_draft_version(session, school_id, term_code)
    if open_draft is not None and open_draft.status == "draft":
        return open_draft

    draft = TimetableDraft(
        school_id=school_id,
        term_code=term_code,
        version=(open_draft.version + 1) if open_draft else 1,
        grid_id=grid.id,
        created_by=ctx.user_id,
    )
    session.add(draft)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.draft.created",
        object_type="timetable_draft",
        object_id=str(draft.id),
        scope=school.path,
        after={"term_code": term_code, "version": draft.version},
    )
    await session.commit()
    return draft


async def _require_open_draft(session: AsyncSession, draft_id: uuid.UUID) -> TimetableDraft:
    draft = await dao.get_draft(session, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Timetable draft not found.")
    if draft.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"This timetable is {draft.status} — republish creates a new version "
            "rather than editing it.",
        )
    return draft


# --- entries and clash detection (TTM-FR-03/04/12) ----------------------------


async def add_entry(
    session: AsyncSession,
    ctx: AuthContext,
    draft_id: uuid.UUID,
    section_id: uuid.UUID,
    day_of_week: int,
    period_id: uuid.UUID,
    offering_id: uuid.UUID,
    faculty_user_id: uuid.UUID,
    venue_id: uuid.UUID,
    acknowledge_capacity: bool = False,
) -> dict[str, object]:
    """Place one class in the grid. Clashes are a hard save-time block (TTM-FR-04).

    Venue capacity is a *soft* warning that needs recorded acknowledgment to
    proceed (TTM-FR-12) — a room slightly too small is a judgement call, whereas
    a double-booked room is never intended.
    """
    draft = await _require_open_draft(session, draft_id)
    period = await dao.get_period(session, period_id)
    if period is None or period.grid_id != draft.grid_id:
        raise HTTPException(
            status_code=422, detail="That Period does not belong to this timetable's grid."
        )
    if day_of_week not in range(1, 8):
        raise HTTPException(status_code=422, detail="day_of_week is 1 (Monday) to 7.")

    section = await org_service.get_unit(session, section_id)
    if section.type != "section":
        raise HTTPException(status_code=422, detail="A timetable entry belongs to a Section.")
    if section.term_code != draft.term_code:
        raise HTTPException(
            status_code=422,
            detail=f"Section '{section.name}' is for term {section.term_code}, "
            f"not {draft.term_code}.",
        )
    school_id = await org_service.ancestor_of_type(session, section_id, "school")
    if school_id != draft.school_id:
        raise HTTPException(
            status_code=422, detail="That Section belongs to another School's timetable."
        )

    # A class on a day the School does not teach produces no Session, ever — so
    # it is a mistake rather than a judgement call, and blocks like a clash
    # (§4 rule 12). Without this an entry saves, holds a room against every
    # other School's clash checks, and is invisible in every view.
    days, is_default = await pattern_for(session, draft.school_id, draft.term_code)
    if not _teaches(days, day_of_week):
        school = await org_service.get_unit(session, draft.school_id)
        raise HTTPException(
            status_code=422,
            detail=(
                f"{school.name} does not teach {_DAY_NAMES[day_of_week]}. "
                f"It teaches {_working_day_names(days)}"
                + (" (no working pattern set — university default)." if is_default else ".")
            ),
        )

    offering = await org_service.get_offering(session, offering_id)
    subject = await org_service.get_subject(session, offering.subject_id)
    # A Programme-bound offering must match the Section's Programme and ladder
    # position; a university-wide one (Open elective) fits any Section.
    if offering.program_id is not None:
        if offering.program_id != section.parent_id:
            raise HTTPException(
                status_code=422,
                detail=f"'{subject.code}' is not offered to this Section's Programme.",
            )
        if section.position is not None and offering.position != section.position:
            raise HTTPException(
                status_code=422,
                detail=f"'{subject.code}' is taught at position {offering.position}; "
                f"this Section is at {section.position}.",
            )

    venue = await org_service.get_venue(session, venue_id)
    faculty = await user_service.get_user(session, faculty_user_id)
    if faculty.kind != "staff":
        raise HTTPException(status_code=422, detail="Only staff can be assigned to teach.")

    clashes = await _describe_clashes(
        session,
        term_code=draft.term_code,
        day_of_week=day_of_week,
        period=period,
        section_id=section_id,
        faculty_user_id=faculty_user_id,
        venue_id=venue_id,
        own_draft_id=draft_id,
    )
    if clashes:
        raise HTTPException(status_code=409, detail={"clashes": clashes})

    headcount = await onboarding_service.section_headcount(session, section_id)
    warnings: list[str] = []
    if headcount > venue.capacity:
        warning = (
            f"{section.name} has {headcount} students but {venue.code} seats "
            f"{venue.capacity}."
        )
        if not acknowledge_capacity:
            raise HTTPException(
                status_code=409,
                detail=f"{warning} Re-submit with acknowledge_capacity to proceed.",
            )
        warnings.append(warning)

    entry = TimetableEntry(
        draft_id=draft_id,
        section_id=section_id,
        day_of_week=day_of_week,
        period_id=period_id,
        offering_id=offering_id,
        faculty_user_id=faculty_user_id,
        venue_id=venue_id,
    )
    session.add(entry)
    await session.flush()
    # Approvals reset: a Department that already signed off has not seen this.
    await _reset_approvals(session, draft_id)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.entry.added",
        object_type="timetable_entry",
        object_id=str(entry.id),
        scope=section.path,
        after={
            "subject": subject.code,
            "day_of_week": day_of_week,
            "period": period.name,
            "venue": venue.code,
            "capacity_warning": warnings or None,
        },
    )
    await session.commit()
    return {"entry": entry, "warnings": warnings}


async def remove_entry(
    session: AsyncSession, ctx: AuthContext, entry_id: uuid.UUID
) -> None:
    entry = await dao.get_entry(session, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Timetable entry not found.")
    await _require_open_draft(session, entry.draft_id)
    await session.delete(entry)
    await _reset_approvals(session, entry.draft_id)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.entry.removed",
        object_type="timetable_entry",
        object_id=str(entry_id),
        before={"section_id": str(entry.section_id), "day_of_week": entry.day_of_week},
    )
    await session.commit()


async def _describe_clashes(
    session: AsyncSession,
    *,
    term_code: str,
    day_of_week: int,
    period: Period,
    section_id: uuid.UUID,
    faculty_user_id: uuid.UUID,
    venue_id: uuid.UUID,
    exclude_entry_id: uuid.UUID | None = None,
    own_draft_id: uuid.UUID | None = None,
) -> list[dict[str, object]]:
    """Turn raw clash rows into sentences naming what collides and where.

    `own_draft_id` only affects wording: a collision inside the timetable being
    edited reads differently from one against someone else's work, and telling
    an author their own entry is "on another draft" sends them looking in the
    wrong place.
    """
    rows = await dao.find_clashes(
        session,
        term_code=term_code,
        day_of_week=day_of_week,
        start_time=period.start_time,
        end_time=period.end_time,
        section_id=section_id,
        faculty_user_id=faculty_user_id,
        venue_id=venue_id,
        exclude_entry_id=exclude_entry_id,
    )
    described: list[dict[str, object]] = []
    for row in rows:
        if row.faculty_user_id == faculty_user_id:
            kind, subject = "faculty", "This Faculty Member"
        elif row.venue_id == venue_id:
            kind, subject = "venue", "This venue"
        else:
            kind, subject = "section", "This Section"
        other = await org_service.get_unit(session, row.section_id)
        described.append(
            {
                "kind": kind,
                "entry_id": str(row.entry_id),
                "section": other.name,
                "period": row.period_name,
                "draft_status": row.draft_status,
                "message": (
                    f"{subject} is already booked for {other.name} in "
                    f"{row.period_name} ({row.start_time:%H:%M}–{row.end_time:%H:%M})"
                    + (
                        " in this timetable."
                        if row.draft_id == own_draft_id
                        else " on a published timetable."
                        if row.draft_status == "published"
                        else " on another School's draft."
                    )
                ),
            }
        )
    return described


# --- approvals and publish (TTM-FR-08/09) -------------------------------------


async def _departments_in_draft(
    session: AsyncSession, draft_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    """Departments whose Sections appear in the draft — the set that must sign off."""
    departments: dict[uuid.UUID, str] = {}
    for entry, _period in await dao.list_entries(session, draft_id):
        department_id = await org_service.ancestor_of_type(
            session, entry.section_id, "department"
        )
        if department_id is not None and department_id not in departments:
            departments[department_id] = (
                await org_service.get_unit(session, department_id)
            ).name
    return departments


async def _reset_approvals(session: AsyncSession, draft_id: uuid.UUID) -> None:
    """Any edit invalidates sign-off: an HoD approved a timetable that no longer
    exists, so their decision cannot carry forward to a different one."""
    for approval in await dao.list_approvals(session, draft_id):
        if approval.status != "pending":
            approval.status = "pending"
            approval.decided_by = None
            approval.decided_at = None
            approval.reason = None


async def draft_status(session: AsyncSession, draft_id: uuid.UUID) -> dict[str, object]:
    """Everything that stands between this draft and publishing."""
    draft = await dao.get_draft(session, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Timetable draft not found.")
    departments = await _departments_in_draft(session, draft_id)
    decisions = {a.department_id: a for a in await dao.list_approvals(session, draft_id)}

    approvals = [
        {
            "department_id": department_id,
            "department_name": name,
            "status": decisions[department_id].status
            if department_id in decisions
            else "pending",
            "reason": decisions[department_id].reason if department_id in decisions else None,
        }
        for department_id, name in sorted(departments.items(), key=lambda kv: kv[1])
    ]
    entries = await dao.list_entries(session, draft_id)
    outstanding = [a for a in approvals if a["status"] != "approved"]
    return {
        "draft_id": draft.id,
        "school_id": draft.school_id,
        "term_code": draft.term_code,
        "version": draft.version,
        "status": draft.status,
        "entry_count": len(entries),
        "approvals": approvals,
        "publishable": draft.status == "draft" and bool(entries) and not outstanding,
        "blocking": (
            []
            if draft.status != "draft"
            else ([] if entries else ["the draft has no entries"])
            + [f"{a['department_name']} has not approved" for a in outstanding]
        ),
    }


async def decide_approval(
    session: AsyncSession,
    ctx: AuthContext,
    draft_id: uuid.UUID,
    department_id: uuid.UUID,
    approve: bool,
    reason: str | None = None,
) -> TimetableApproval:
    """An HoD signs off (or rejects) the portion of a draft touching their Department."""
    await _require_open_draft(session, draft_id)
    departments = await _departments_in_draft(session, draft_id)
    if department_id not in departments:
        raise HTTPException(
            status_code=422,
            detail="That Department has no Sections in this timetable, so it has "
            "nothing to approve.",
        )
    await rbac_service.ensure_scope_covers(
        session, ctx, ("hod", "school-incharge", "system-admin", "super-admin"), department_id
    )
    if not approve and not reason:
        raise HTTPException(status_code=422, detail="Rejecting requires a reason.")

    approval = await dao.find_approval(session, draft_id, department_id)
    if approval is None:
        approval = TimetableApproval(draft_id=draft_id, department_id=department_id)
        session.add(approval)
    approval.status = "approved" if approve else "rejected"
    approval.decided_by = ctx.user_id
    approval.decided_at = datetime.now(UTC)
    approval.reason = reason
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.draft.approved" if approve else "ttm.draft.rejected",
        object_type="timetable_draft",
        object_id=str(draft_id),
        after={"department": departments[department_id], "reason": reason},
    )
    await session.commit()
    return approval


async def publish_draft(
    session: AsyncSession, ctx: AuthContext, draft_id: uuid.UUID
) -> TimetableDraft:
    """Make the draft the term's source of truth for this School (TTM-FR-09).

    Blocked while any covered Department has not approved, or the draft is
    empty. Clashes cannot exist here — they are refused at save time — but the
    check is repeated because another School may have published into a shared
    room since this draft was last touched.
    """
    draft = await _require_open_draft(session, draft_id)
    state = await draft_status(session, draft_id)
    if not state["publishable"]:
        raise HTTPException(
            status_code=409,
            detail="Cannot publish: " + "; ".join(cast(list[str], state["blocking"])) + ".",
        )

    for entry, period in await dao.list_entries(session, draft_id):
        clashes = await _describe_clashes(
            session,
            term_code=draft.term_code,
            day_of_week=entry.day_of_week,
            period=period,
            section_id=entry.section_id,
            faculty_user_id=entry.faculty_user_id,
            venue_id=entry.venue_id,
            exclude_entry_id=entry.id,
            own_draft_id=draft_id,
        )
        # Own-draft rows are not conflicts with themselves; anything else is a
        # collision that appeared while this draft sat waiting for approval.
        external = [c for c in clashes if c["entry_id"] != str(entry.id)]
        if external:
            raise HTTPException(status_code=409, detail={"clashes": external})

    previous = await dao.published_draft(session, draft.school_id, draft.term_code)
    if previous is not None:
        previous.status = "superseded"
    draft.status = "published"
    draft.published_by = ctx.user_id
    draft.published_at = datetime.now(UTC)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.draft.published",
        object_type="timetable_draft",
        object_id=str(draft.id),
        after={
            "term_code": draft.term_code,
            "version": draft.version,
            "entries": state["entry_count"],
            "superseded": str(previous.id) if previous else None,
        },
    )
    await session.commit()
    get_logger().info(
        "timetable published",
        school_id=str(draft.school_id),
        term_code=draft.term_code,
        version=draft.version,
        entries=state["entry_count"],
    )
    return draft


async def timetable_view(
    session: AsyncSession, draft_id: uuid.UUID
) -> list[dict[str, object]]:
    """The grid as rows: what is taught, by whom, where, when."""
    rows: list[dict[str, object]] = []
    for entry, period in await dao.list_entries(session, draft_id):
        section = await org_service.get_unit(session, entry.section_id)
        offering = await org_service.get_offering(session, entry.offering_id)
        subject = await org_service.get_subject(session, offering.subject_id)
        venue = await org_service.get_venue(session, entry.venue_id)
        faculty = await user_service.get_user(session, entry.faculty_user_id)
        rows.append(
            {
                "entry_id": entry.id,
                "section_id": entry.section_id,
                "section_name": section.name,
                "day_of_week": entry.day_of_week,
                "period_name": period.name,
                "start_time": period.start_time,
                "end_time": period.end_time,
                "subject_code": subject.code,
                "subject_name": subject.name,
                "faculty_user_id": entry.faculty_user_id,
                "faculty_name": faculty.full_name,
                "venue_code": venue.code,
            }
        )
    return rows


# --- personal views (TTM-FR-13) -----------------------------------------------


async def my_timetable(
    session: AsyncSession, ctx: AuthContext, term_code: str
) -> dict[str, object]:
    """The signed-in person's own published timetable for a term.

    Own-data endpoint: the subject is resolved from the AuthContext, never from
    a client-supplied id (project security rule), so nobody can read another
    person's week by guessing an id.

    A **student** sees their Section's classes with electives merged: an
    elective entry appears only if they chose *that* offering, because two
    alternatives in one group are taught in the same slot and only one of them
    is theirs. A **Faculty Member** sees their own load across every School.
    Drafts are never visible to either.
    """
    user_id = uuid.UUID(ctx.user_id)
    profile = await onboarding_service.student_profile(session, user_id)

    if profile is not None:
        membership = await onboarding_service.membership_as_of(session, user_id, date.today())
        if membership is None:
            return {"role": "student", "section_name": None, "rows": [], "note":
                    "You are not allotted to a Section for this term yet."}
        pairs = await dao.published_entries_for_section(
            session, membership.section_id, term_code
        )
        chosen = {
            choice.offering_id
            for choice in await onboarding_service.elective_choices(session, user_id, term_code)
        }
        section = await org_service.get_unit(session, membership.section_id)
        rows = await _personal_rows(session, pairs, chosen_offerings=chosen)
        return {"role": "student", "section_name": section.name, "rows": rows, "note": None}

    pairs = await dao.published_entries_for_faculty(session, user_id, term_code)
    rows = await _personal_rows(session, pairs, chosen_offerings=None)
    return {"role": "faculty", "section_name": None, "rows": rows, "note": None}


async def _personal_rows(
    session: AsyncSession,
    pairs: Sequence[tuple[TimetableEntry, Period]],
    *,
    chosen_offerings: set[uuid.UUID] | None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry, period in pairs:
        offering = await org_service.get_offering(session, entry.offering_id)
        subject = await org_service.get_subject(session, offering.subject_id)
        if chosen_offerings is not None and subject.kind == "elective":
            # Two alternatives in one group run in the same slot; only the one
            # this student picked is on their timetable.
            if entry.offering_id not in chosen_offerings:
                continue
        section = await org_service.get_unit(session, entry.section_id)
        venue = await org_service.get_venue(session, entry.venue_id)
        faculty = await user_service.get_user(session, entry.faculty_user_id)
        rows.append(
            {
                "day_of_week": entry.day_of_week,
                "period_name": period.name,
                "start_time": period.start_time,
                "end_time": period.end_time,
                "subject_code": subject.code,
                "subject_name": subject.name,
                "elective_group": subject.elective_group,
                "section_name": section.name,
                "faculty_name": faculty.full_name,
                "venue_code": venue.code,
            }
        )
    return rows


# --- calendar: holidays, working days, resolution (TTM-FR-26/27/28) ----------

#: ATT owns attendance, and this module must not import it — so ATT registers a
#: probe at startup, exactly as org takes a position reader from ONB. Until ATT
#: exists the default answers "nothing captured", which makes the guard in §4
#: rule 13 live and testable now and correct the day ATT lands.
_DAY_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
              5: "Friday", 6: "Saturday", 7: "Sunday"}


AttendanceProbe = Callable[
    [AsyncSession, uuid.UUID | None, date, date], Awaitable[list[str]]
]


async def _no_attendance(
    session: AsyncSession, school_id: uuid.UUID | None, start: date, end: date
) -> list[str]:
    return []


_attendance_probe: AttendanceProbe = _no_attendance


def register_attendance_probe(probe: AttendanceProbe) -> None:
    """Called once at startup by whoever owns captured attendance."""
    global _attendance_probe
    _attendance_probe = probe


async def _refuse_if_attendance_captured(
    session: AsyncSession, school_id: uuid.UUID | None, start: date, end: date, what: str
) -> None:
    """Narrowing the calendar never voids attendance (§4 rule 13).

    Attendance gates exam eligibility under UGC minimum-attendance norms, so an
    administrative edit that would erase teaching days out from under it is
    refused and names what it found.
    """
    captured = await _attendance_probe(session, school_id, start, end)
    if captured:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot {what}: attendance has already been captured for "
                f"{len(captured)} session(s) in that range — "
                f"{', '.join(captured[:5])}"
                f"{'…' if len(captured) > 5 else ''}. "
                f"Correct the attendance first."
            ),
        )


def _occurrence_in_month(on_date: date) -> int:
    """Which occurrence of its own weekday this date is within its month — the
    2nd Saturday is 2. Counted within the calendar month, never by ISO week
    number, which drifts across month boundaries."""
    return (on_date.day - 1) // 7 + 1


def _teaches(days: dict[str, Any], day_of_week: int, on_date: date | None = None) -> bool:
    """Does this pattern teach that weekday — and, for an nth-weekday rule, on
    that particular date? With no date, a qualified day counts as taught: the
    question is then "is this weekday ever taught", which is what the authoring
    guard asks of a weekly recurring entry."""
    rule = days.get(str(day_of_week))
    if rule is None or rule is False:
        return False
    if rule is True:
        return True
    if on_date is None:
        return True  # taught on some occurrences, so the weekday is in use
    return _occurrence_in_month(on_date) in cast(list[int], rule)


def _working_day_names(days: dict[str, Any]) -> str:
    names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    parts = []
    for n in range(1, 8):
        rule = days.get(str(n))
        if rule is True:
            parts.append(names[n])
        elif isinstance(rule, list):
            suffix = {1: "st", 2: "nd", 3: "rd"}
            ordinals = ", ".join(f"{o}{suffix.get(o, 'th')}" for o in rule)
            parts.append(f"{names[n]} ({ordinals} of the month)")
    return ", ".join(parts) or "no days"


async def pattern_for(
    session: AsyncSession, school_id: uuid.UUID, term_code: str | None = None
) -> tuple[dict[str, Any], bool]:
    """The days a School teaches, plus whether that is the shipped default.

    A School that has never declared its week falls back to Monday–Saturday
    rather than to nothing — an unconfigured School must not silently produce a
    term with no classes — and the fallback is reported so the caller can show
    it rather than imply a decision was made.
    """
    row = await dao.working_pattern(session, school_id, term_code)
    if row is None:
        return dict(DEFAULT_WORKING_DAYS), True
    return cast(dict[str, Any], row.days), False


def _holiday_applies(holiday: UniversityHoliday, campus_code: str | None) -> bool:
    """An untagged holiday closes the whole university; a tagged one closes only
    the campuses it names, so a School on another campus stays open. A School
    with no campus recorded is in no list, so tagged holidays pass it by."""
    if not holiday.campus_codes:
        return True
    return campus_code is not None and campus_code in holiday.campus_codes


async def resolve_days(
    session: AsyncSession,
    school_id: uuid.UUID,
    start: date,
    end: date,
    term_code: str | None = None,
) -> list[dict[str, object]]:
    """Is each date a teaching day for this School, and which weekday does it run?

    Most-specific-wins (§4 rule 11): a dated exception, then the School working
    through a university holiday, then the holiday, then the weekly pattern.
    Every answer names the layer that decided it, so "why do I have class on
    Sunday?" has an answer rather than a shrug.

    Everything is fetched once for the whole range — ATT expands a term against
    this, so a query per date would not survive contact with a real term.
    """
    if end < start:
        raise HTTPException(status_code=422, detail="The range ends before it starts.")
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="Working days are a School attribute.")

    days, is_default = await pattern_for(session, school_id, term_code)
    holidays = [
        h for h in await dao.holidays_between(session, start, end)
        if _holiday_applies(h, school.campus_code)
    ]
    exceptions = {
        e.on_date: e for e in await dao.exceptions_between(session, school_id, start, end)
    }

    resolved: list[dict[str, object]] = []
    cursor = start
    while cursor <= end:
        holiday = next(
            (h for h in holidays if h.from_date <= cursor <= h.to_date), None
        )
        exception = exceptions.get(cursor)
        if exception is not None and not exception.working:
            resolved.append({
                "on_date": cursor, "teaching": False, "effective_day_of_week": None,
                "decided_by": "school-exception", "detail": exception.reason,
            })
        elif exception is not None:
            effective = exception.follows_day_of_week or cursor.isoweekday()
            resolved.append({
                "on_date": cursor, "teaching": True, "effective_day_of_week": effective,
                # Working through a declared holiday is a different act from an
                # ordinary working exception, and the answer says which it was.
                "decided_by": "school-override" if holiday else "school-exception",
                "detail": (
                    f"{exception.reason}"
                    + (f" (runs {_DAY_NAMES[effective]}'s timetable)"
                       if exception.follows_day_of_week else "")
                    + (f" — despite '{holiday.label}'" if holiday else "")
                ),
            })
        elif holiday is not None:
            resolved.append({
                "on_date": cursor, "teaching": False, "effective_day_of_week": None,
                "decided_by": "university-holiday",
                "detail": f"{holiday.label} ({holiday.kind})",
            })
        else:
            teaching = _teaches(days, cursor.isoweekday(), cursor)
            resolved.append({
                "on_date": cursor,
                "teaching": teaching,
                "effective_day_of_week": cursor.isoweekday() if teaching else None,
                "decided_by": "school-pattern-default" if is_default else "school-pattern",
                "detail": (
                    f"{school.name} teaches {_working_day_names(days)}"
                    + (" (no pattern set — university default)" if is_default else "")
                ),
            })
        cursor += timedelta(days=1)
    return resolved


async def resolve_day(
    session: AsyncSession, school_id: uuid.UUID, on_date: date, term_code: str | None = None
) -> dict[str, object]:
    return (await resolve_days(session, school_id, on_date, on_date, term_code))[0]


# --- university holiday calendar (TTM-FR-26) ---------------------------------


async def create_holiday(
    session: AsyncSession, ctx: AuthContext, data: HolidayCreate
) -> UniversityHoliday:
    await _refuse_if_attendance_captured(
        session, None, data.from_date, data.to_date, f"declare '{data.label}' a holiday"
    )
    holiday = UniversityHoliday(
        from_date=data.from_date,
        to_date=data.to_date,
        label=data.label,
        kind=data.kind,
        campus_codes=data.campus_codes,
        created_by=ctx.user_id,
    )
    session.add(holiday)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.holiday.created",
        object_type="university_holiday",
        object_id=str(holiday.id),
        after=_holiday_snapshot(holiday),
    )
    await session.commit()
    return holiday


def _holiday_snapshot(holiday: UniversityHoliday) -> dict[str, object]:
    return {
        "label": holiday.label,
        "kind": holiday.kind,
        "from_date": holiday.from_date.isoformat(),
        "to_date": holiday.to_date.isoformat(),
        "campus_codes": list(holiday.campus_codes),
        "status": holiday.status,
    }


async def update_holiday(
    session: AsyncSession, ctx: AuthContext, holiday_id: uuid.UUID, data: HolidayUpdate
) -> UniversityHoliday:
    holiday = await dao.get_holiday(session, holiday_id)
    if holiday is None:
        raise HTTPException(status_code=404, detail="Holiday not found.")
    before = _holiday_snapshot(holiday)
    new_from = data.from_date or holiday.from_date
    new_to = data.to_date or holiday.to_date
    if new_to < new_from:
        raise HTTPException(status_code=422, detail="to_date cannot be before from_date.")
    # Only dates the entry does not already cover are newly closed; re-checking
    # the dates it already covered would refuse a pure relabel.
    if new_from < holiday.from_date:
        await _refuse_if_attendance_captured(
            session, None, new_from, holiday.from_date, "extend that holiday"
        )
    if new_to > holiday.to_date:
        await _refuse_if_attendance_captured(
            session, None, holiday.to_date, new_to, "extend that holiday"
        )
    holiday.from_date, holiday.to_date = new_from, new_to
    if data.label is not None:
        holiday.label = data.label
    if data.kind is not None:
        holiday.kind = data.kind
    if data.campus_codes is not None:
        holiday.campus_codes = data.campus_codes
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.holiday.updated",
        object_type="university_holiday",
        object_id=str(holiday.id),
        before=before,
        after=_holiday_snapshot(holiday),
    )
    await session.commit()
    return holiday


async def withdraw_holiday(
    session: AsyncSession, ctx: AuthContext, holiday_id: uuid.UUID
) -> UniversityHoliday:
    """Withdrawing a holiday *widens* the calendar, so it needs no guard — but
    the entry is kept rather than deleted, because a date that used to be closed
    is exactly the kind of thing an audit later asks about."""
    holiday = await dao.get_holiday(session, holiday_id)
    if holiday is None:
        raise HTTPException(status_code=404, detail="Holiday not found.")
    before = _holiday_snapshot(holiday)
    holiday.status = "withdrawn"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.holiday.withdrawn",
        object_type="university_holiday",
        object_id=str(holiday.id),
        before=before,
        after=_holiday_snapshot(holiday),
    )
    await session.commit()
    return holiday


async def list_holidays(
    session: AsyncSession, start: date | None, end: date | None, limit: int = 500
) -> Sequence[UniversityHoliday]:
    return await dao.list_holidays(session, start, end, limit)


# --- School working pattern and exceptions (TTM-FR-27) -----------------------


async def set_working_pattern(
    session: AsyncSession, ctx: AuthContext, school_id: uuid.UUID, data: WorkingPatternUpdate
) -> SchoolWorkingPattern:
    """Declare which weekdays a School teaches.

    Set directly rather than drafted and approved: the School Incharge is the
    approver for every other School-scoped decision, so a workflow here would
    only have them approving themselves. The before/after goes to audit instead.
    """
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(
            status_code=422,
            detail="Working days are a School attribute — no per-Department patterns.",
        )
    await rbac_service.ensure_scope_covers(
        session, ctx, ("school-incharge", "system-admin", "super-admin"), school_id
    )

    current, _ = await pattern_for(session, school_id, data.term_code)
    # Withdrawing a taught weekday would orphan whatever is published on it;
    # widening never can, so only the days being turned off are checked.
    for day in range(1, 8):
        if _teaches(current, day) and not _teaches(data.days, day):
            orphans = await dao.published_entries_on_weekday(session, school_id, day)
            if orphans:
                where = sorted({f"{o.term_code} {o.period_name}" for o in orphans})
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot stop teaching {_DAY_NAMES[day]}: {len(orphans)} published "
                        f"class(es) are scheduled then ({', '.join(where)[:200]}). "
                        f"Republish those timetables without {_DAY_NAMES[day]} first."
                    ),
                )

    row = await dao.working_pattern(session, school_id, data.term_code)
    # `working_pattern` falls back to the standing row, so a first-time term
    # override must not be mistaken for it and edited in place.
    if row is not None and row.term_code != data.term_code:
        row = None
    before = dict(row.days) if row is not None else None
    if row is None:
        row = SchoolWorkingPattern(
            school_id=school_id, term_code=data.term_code, days=dict(data.days),
            updated_by=ctx.user_id,
        )
        session.add(row)
    else:
        row.days = dict(data.days)
        row.updated_by = ctx.user_id
        row.updated_at = datetime.now(UTC)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.working-pattern.set",
        object_type="school_working_pattern",
        object_id=str(row.id),
        scope=school.path,
        before={"days": before} if before else None,
        after={"days": dict(data.days), "term_code": data.term_code},
    )
    await session.commit()
    return row


async def add_calendar_exception(
    session: AsyncSession, ctx: AuthContext, school_id: uuid.UUID,
    data: CalendarExceptionCreate, term_code: str | None = None,
) -> SchoolCalendarException:
    """One dated override — a closed day, or a day worked anyway."""
    school = await org_service.get_unit(session, school_id)
    if school.type != "school":
        raise HTTPException(status_code=422, detail="Calendar exceptions belong to a School.")
    await rbac_service.ensure_scope_covers(
        session, ctx, ("school-incharge", "system-admin", "super-admin"), school_id
    )

    if not data.working:
        await _refuse_if_attendance_captured(
            session, school_id, data.on_date, data.on_date,
            f"close {data.on_date:%d-%m-%Y}",
        )
    else:
        # A working day that runs nothing is not a working day. Whether it
        # follows another weekday or its own, that weekday has to be taught.
        days, _ = await pattern_for(session, school_id, term_code)
        effective = data.follows_day_of_week or data.on_date.isoweekday()
        if not _teaches(days, effective):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{school.name} does not teach {_DAY_NAMES[effective]}, so that date "
                    f"would run an empty timetable. It teaches "
                    f"{_working_day_names(days)}."
                ),
            )

    existing = await dao.get_exception(session, school_id, data.on_date)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{data.on_date:%d-%m-%Y} already has an exception for this School.",
        )
    row = SchoolCalendarException(
        school_id=school_id,
        on_date=data.on_date,
        working=data.working,
        follows_day_of_week=data.follows_day_of_week,
        reason=data.reason,
        created_by=ctx.user_id,
    )
    session.add(row)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.calendar-exception.added",
        object_type="school_calendar_exception",
        object_id=str(row.id),
        scope=school.path,
        after={
            "on_date": data.on_date.isoformat(),
            "working": data.working,
            "follows_day_of_week": data.follows_day_of_week,
            "reason": data.reason,
        },
    )
    await session.commit()
    return row


async def remove_calendar_exception(
    session: AsyncSession, ctx: AuthContext, school_id: uuid.UUID, on_date: date
) -> None:
    school = await org_service.get_unit(session, school_id)
    await rbac_service.ensure_scope_covers(
        session, ctx, ("school-incharge", "system-admin", "super-admin"), school_id
    )
    row = await dao.get_exception(session, school_id, on_date)
    if row is None:
        raise HTTPException(status_code=404, detail="No exception on that date.")
    # Removing a *working* exception narrows the calendar back to the pattern,
    # which can withdraw a day attendance was captured on.
    if row.working:
        await _refuse_if_attendance_captured(
            session, school_id, on_date, on_date, f"remove the working day {on_date:%d-%m-%Y}"
        )
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="ttm.calendar-exception.removed",
        object_type="school_calendar_exception",
        object_id=str(row.id),
        scope=school.path,
        before={"on_date": on_date.isoformat(), "working": row.working, "reason": row.reason},
    )
    await session.delete(row)
    await session.commit()


async def list_calendar_exceptions(
    session: AsyncSession, school_id: uuid.UUID, start: date, end: date
) -> Sequence[SchoolCalendarException]:
    return await dao.exceptions_between(session, school_id, start, end)
