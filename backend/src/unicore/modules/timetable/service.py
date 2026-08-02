"""Business rules for the timetable module. The only layer other modules may call.

Milestone-2 slice: per-School academic terms (TTM-FR-18) and per-term Section
instances (TTM-FR-19). Term dates are per School — one campus hosts semester- and
year-based Schools simultaneously.
"""

import csv
import io
import uuid
from datetime import UTC, datetime, time
from typing import cast

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
    TERM_PARITIES,
    AcademicTerm,
    Period,
    PeriodGrid,
    TimetableApproval,
    TimetableDraft,
    TimetableEntry,
)
from unicore.modules.timetable.schemas import (
    SECTION_CSV_COLUMNS,
    MultiSchoolTermCreate,
    TermCreate,
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
