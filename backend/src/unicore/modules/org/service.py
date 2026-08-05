"""Business rules for the org module. The only layer other modules may call.

Covers AUTH-FR-19: Super Admin CRUD over Faculty Division/School/Department/
Program; deactivate-never-delete; every change audited in-transaction.
Section instances are NOT created here (TTM-FR-19) — TTM's term setup will call
`create_section_instance` in its own milestone.
"""

import csv
import io
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.security import AuthContext
from unicore.core.templates import strip_comments
from unicore.modules.audit import service as audit_service
from unicore.modules.org import dao
from unicore.modules.org.models import (
    CADENCES,
    PARENT_TYPE_OF,
    POSITIONS_PER_YEAR,
    OrgUnit,
    Subject,
    SubjectComponent,
    SubjectOffering,
    Venue,
)
from unicore.modules.org.schemas import (
    ORG_CSV_COLUMNS,
    PROGRAMME_CATEGORIES,
    SUBJECT_CSV_COLUMNS,
    VENUE_CSV_COLUMNS,
    VENUE_KINDS,
    OrgUnitCreate,
)
from unicore.modules.org.schemas import SubjectCreate as OrgUnitSubjectCreate


def _label(code: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_]", "_", code).lower()
    if not label or label[0].isdigit():
        label = "u_" + label
    return label


def normalize_path(path: str) -> str:
    """Apply the same label rule to every segment of a dotted path, so users may
    write natural codes (UNI.FET.SOCE.CSE.BT-CSE) even though stored ltree labels
    replace punctuation with underscores."""
    return ".".join(_label(segment) for segment in path.split(".") if segment)


def _snapshot(unit: OrgUnit) -> dict[str, str | None]:
    return {
        "type": unit.type,
        "name": unit.name,
        "code": unit.code,
        "path": unit.path,
        "status": unit.status,
    }


async def create_unit(session: AsyncSession, ctx: AuthContext, data: OrgUnitCreate) -> OrgUnit:
    if data.type == "section":
        raise HTTPException(
            status_code=422,
            detail="Section instances are created by Timetable Cell term setup (TTM-FR-19), "
            "not org administration.",
        )
    if data.type not in PARENT_TYPE_OF:
        raise HTTPException(status_code=422, detail=f"Unknown org unit type '{data.type}'.")

    expected_parent = PARENT_TYPE_OF[data.type]
    if expected_parent is None:
        if data.parent_id is not None:
            raise HTTPException(status_code=422, detail="A university cannot have a parent.")
        if await dao.get_root(session) is not None:
            raise HTTPException(status_code=409, detail="A university root already exists.")
        path = _label(data.code)
    else:
        if data.parent_id is None:
            raise HTTPException(
                status_code=422, detail=f"A {data.type} requires a {expected_parent} parent."
            )
        parent = await dao.get_by_id(session, data.parent_id)
        if parent is None or parent.type != expected_parent:
            raise HTTPException(
                status_code=422, detail=f"Parent of a {data.type} must be a {expected_parent}."
            )
        if parent.status != "active":
            raise HTTPException(
                status_code=409, detail="Cannot create units under a deactivated parent."
            )
        path = f"{parent.path}.{_label(data.code)}"

    if await dao.path_exists(session, path):
        raise HTTPException(status_code=409, detail=f"Path '{path}' already exists.")

    unit = OrgUnit(
        type=data.type,
        name=data.name,
        code=data.code,
        parent_id=data.parent_id,
        path=path,
        campus_code=data.campus_code,
        cadence=data.cadence,
        class_size_cap=data.class_size_cap,
    )
    session.add(unit)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.created",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        after=_snapshot(unit),
    )
    await session.commit()
    return unit


async def rename_unit(
    session: AsyncSession, ctx: AuthContext, unit_id: uuid.UUID, new_name: str
) -> OrgUnit:
    unit = await _get_or_404(session, unit_id)
    before = _snapshot(unit)
    unit.name = new_name
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.renamed",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        before=before,
        after=_snapshot(unit),
    )
    await session.commit()
    return unit


async def deactivate_unit(
    session: AsyncSession, ctx: AuthContext, unit_id: uuid.UUID
) -> OrgUnit:
    unit = await _get_or_404(session, unit_id)
    if unit.status == "deactivated":
        return unit
    before = _snapshot(unit)
    unit.status = "deactivated"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.deactivated",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        before=before,
        after=_snapshot(unit),
    )
    await session.commit()
    return unit


async def reparent_unit(
    session: AsyncSession, ctx: AuthContext, unit_id: uuid.UUID, new_parent_id: uuid.UUID
) -> OrgUnit:
    unit = await _get_or_404(session, unit_id)
    if unit.parent_id is None:
        raise HTTPException(status_code=422, detail="The university root cannot be re-parented.")
    new_parent = await dao.get_by_id(session, new_parent_id)
    expected_parent = PARENT_TYPE_OF[unit.type]
    if new_parent is None or new_parent.type != expected_parent:
        raise HTTPException(
            status_code=422, detail=f"New parent of a {unit.type} must be a {expected_parent}."
        )
    if new_parent.status != "active":
        raise HTTPException(status_code=409, detail="Cannot move under a deactivated parent.")
    if await dao.is_descendant(session, unit.path, new_parent.path):
        raise HTTPException(status_code=422, detail="Cannot move a unit into its own subtree.")

    old_parent = await dao.get_by_id(session, unit.parent_id)
    assert old_parent is not None
    before = _snapshot(unit)
    await dao.move_subtree(session, unit.path, old_parent.path, new_parent.path)
    unit.parent_id = new_parent.id
    await session.flush()
    await session.refresh(unit)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.reparented",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        before=before,
        after=_snapshot(unit),
    )
    await session.commit()
    return unit


async def ancestor_of_type(
    session: AsyncSession, unit_id: uuid.UUID, unit_type: str
) -> uuid.UUID | None:
    """Nearest ancestor of a given type — used by other modules' scope logic."""
    return await dao.ancestor_of_type(session, unit_id, unit_type)


async def resolve_by_code(
    session: AsyncSession, code: str, unit_type: str, scope_paths: list[str] | None
) -> list[OrgUnit]:
    """Units of `unit_type` with `code`, restricted to the caller's scope subtrees."""
    return await dao.find_by_code_in_scope(session, code, unit_type, scope_paths)


async def find_section(
    session: AsyncSession, program_id: uuid.UUID, label: str, term_code: str
) -> OrgUnit | None:
    return await dao.find_section(session, program_id, label, term_code)


async def get_unit_by_path(session: AsyncSession, path: str) -> OrgUnit | None:
    """Path lookup for other modules' bulk importers; accepts natural codes."""
    return await dao.get_by_path(session, normalize_path(path))


async def get_unit_paths(
    session: AsyncSession, unit_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Batch id->ltree-path lookup for other modules (rbac scope checks)."""
    return await dao.paths_for_ids(session, unit_ids)


async def get_root(session: AsyncSession) -> OrgUnit | None:
    return await dao.get_root(session)


async def get_unit(session: AsyncSession, unit_id: uuid.UUID) -> OrgUnit:
    return await _get_or_404(session, unit_id)


async def list_children(session: AsyncSession, parent_id: uuid.UUID) -> Sequence[OrgUnit]:
    return await dao.list_children(session, parent_id)


async def list_descendants_of_type(
    session: AsyncSession, ancestor_id: uuid.UUID, unit_type: str
) -> Sequence[OrgUnit]:
    """Active units of a type beneath one unit — TTM asks this for the Programmes
    and Sections of a School during term setup."""
    ancestor = await _get_or_404(session, ancestor_id)
    return await dao.descendants_of_type(session, ancestor.path, unit_type)


# --- cadence, ladder and class size (AUTH-FR-19, TTM-FR-22/24) ---------------


async def effective_cadence(session: AsyncSession, programme: OrgUnit) -> str:
    """A Programme's own cadence if it overrides, else its School's.

    The School is authoritative and the Programme is the exception — a School of
    Pharmacy runs B.Pharm on semesters alongside PhD programmes that do not.
    """
    if programme.cadence is not None:
        return programme.cadence
    school_id = await dao.ancestor_of_type(session, programme.id, "school")
    school = await dao.get_by_id(session, school_id) if school_id else None
    if school is None or school.cadence is None:
        raise HTTPException(
            status_code=409,
            detail=f"No curriculum cadence set for '{programme.name}' or its School.",
        )
    return school.cadence


def position_ladder(cadence: str, duration_years: int | None) -> list[int]:
    """Every position a Programme has: 1..(years x positions-per-year).

    An unknown duration yields an empty ladder rather than a guess — inventing
    four years would create Sections for terms the Programme does not have.
    """
    if not duration_years:
        return []
    return list(range(1, duration_years * POSITIONS_PER_YEAR[cadence] + 1))


def live_positions(cadence: str, duration_years: int | None, parity: str) -> list[int]:
    """The positions a Programme actually runs in a term of this parity.

    Semester programmes run half their ladder per term — odd positions in an odd
    term, even in an even one. Yearly programmes run every position every term,
    so parity does not apply to them.
    """
    ladder = position_ladder(cadence, duration_years)
    if cadence == "yearly":
        return ladder
    wanted = 1 if parity == "odd" else 0
    return [p for p in ladder if p % 2 == wanted]


def year_of(cadence: str, position: int) -> int:
    """The academic year a position falls in — derived, never stored (ONB-FR-20)."""
    per_year = POSITIONS_PER_YEAR[cadence]
    return (position - 1) // per_year + 1


async def class_size_cap(session: AsyncSession, school: OrgUnit) -> int:
    """The School's override if set, else the university default."""
    if school.class_size_cap is not None:
        return school.class_size_cap
    return int(await get_setting(session, "class_size_cap"))


async def _assert_cadence_change_safe(
    session: AsyncSession, unit: OrgUnit, new_cadence: str
) -> None:
    """Refuse a cadence change that would strand students off the new ladder.

    Switching a 4-year School from semester to yearly shrinks every Programme's
    ladder from 8 rungs to 4; a student at semester 7 would silently occupy a
    position that no longer exists. Rather than rewrite real students' positions,
    the change is refused and named.
    """
    programmes = (
        [unit]
        if unit.type == "program"
        else list(await dao.descendants_of_type(session, unit.path, "program"))
    )
    for programme in programmes:
        if programme.cadence is not None and programme.id != unit.id:
            continue  # this Programme overrides; the School's value does not reach it
        ladder = position_ladder(new_cadence, programme.duration_years)
        highest = max(ladder) if ladder else 0
        occupied = await onboarding_positions(session, programme.id)
        stranded = sorted(p for p in occupied if p > highest)
        if stranded:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{programme.name}' has students at position "
                    f"{', '.join(str(p) for p in stranded)}, which '{new_cadence}' cadence "
                    f"does not have (its ladder ends at {highest}). Move them first."
                ),
            )


PositionReader = Callable[[AsyncSession, uuid.UUID], Awaitable[set[int]]]

_position_reader: PositionReader | None = None


def register_position_reader(reader: PositionReader) -> None:
    """Let org ask which positions hold students without importing onboarding.

    onboarding.service already imports org.service, so the reverse import would
    be circular. Registration at startup keeps the dependency one-way, the same
    inversion core/ uses for the token verifier.
    """
    global _position_reader
    _position_reader = reader


async def onboarding_positions(session: AsyncSession, program_id: uuid.UUID) -> set[int]:
    """Positions currently occupied in a Programme; empty if nothing registered."""
    if _position_reader is None:
        return set()
    return await _position_reader(session, program_id)


async def get_setting(session: AsyncSession, key: str) -> str:
    value = await dao.get_setting(session, key)
    if value is None:
        raise HTTPException(status_code=500, detail=f"Missing university setting '{key}'.")
    return value


async def set_setting(
    session: AsyncSession, ctx: AuthContext, key: str, value: str
) -> str:
    before = await dao.get_setting(session, key)
    await dao.set_setting(session, key, value, ctx.user_id)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.setting.updated",
        object_type="university_setting",
        object_id=key,
        before={"value": before},
        after={"value": value},
    )
    await session.commit()
    return value


async def _get_or_404(session: AsyncSession, unit_id: uuid.UUID) -> OrgUnit:
    unit = await dao.get_by_id(session, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Org unit not found.")
    return unit


async def create_section_instance(
    session: AsyncSession,
    ctx: AuthContext,
    program_id: uuid.UUID,
    label: str,
    term_code: str,
    position: int | None = None,
    division_letter: str | None = None,
) -> OrgUnit:
    """Per-term Section instance (TTM-FR-19). Called by TTM term setup — not org admin.

    The (program, term, label) triple is a distinct org unit; labels may repeat
    across terms.
    """
    program = await _get_or_404(session, program_id)
    if program.type != "program":
        raise HTTPException(status_code=422, detail="Sections are created under a program.")
    if program.status != "active":
        raise HTTPException(
            status_code=409, detail="Cannot create sections under a deactivated program."
        )
    code = f"{term_code}-{label}"
    path = f"{program.path}.{_label(code)}"
    if await dao.path_exists(session, path):
        raise HTTPException(
            status_code=409, detail=f"Section '{label}' already exists for {term_code}."
        )
    section = OrgUnit(
        type="section",
        name=label,
        code=code,
        parent_id=program.id,
        path=path,
        campus_code=program.campus_code,
        term_code=term_code,
        position=position,
        division_letter=division_letter,
    )
    session.add(section)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.section.created",
        object_type="org_unit",
        object_id=str(section.id),
        scope=section.path,
        after=_snapshot(section),
    )
    await session.commit()
    return section


# --- CSV import: flat course catalogue (Super Admin) --------------------------

MAX_ORG_FILE_BYTES = 5 * 1024 * 1024


async def import_csv(
    session: AsyncSession, ctx: AuthContext, filename: str, content: bytes
) -> dict[str, object]:
    """One row per Programme, ancestors as columns. Missing Faculty Divisions,
    Schools and Departments are created on the way down, so a catalogue export
    imports without any hierarchy encoding. Partial commit: valid rows land,
    invalid rows come back as an error report.
    """
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    if len(content) > MAX_ORG_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 5 MB limit.")
    try:
        text_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    body = strip_comments(text_content)
    reader = csv.DictReader(io.StringIO(body))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = set(ORG_CSV_COLUMNS) - set(header)
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Header does not match the org template — missing: "
            f"{', '.join(sorted(missing))}.",
        )

    root = await dao.get_root(session)
    if root is None:
        raise HTTPException(
            status_code=409,
            detail="No university exists yet — run bootstrap before importing structure.",
        )

    created = updated = unchanged = 0
    errors: list[dict[str, object]] = []

    for row_number, raw in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue  # tolerate blank spacer rows
        try:
            outcome = await _import_catalogue_row(session, ctx, root, row)
        except _OrgRowError as err:
            errors.append(
                {
                    "row_number": row_number,
                    "field": err.field,
                    "reason": err.reason,
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items() if v),
                }
            )
            continue
        created += outcome["created"]
        updated += outcome["updated"]
        unchanged += outcome["unchanged"]

    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.import.completed",
        object_type="org_import",
        object_id=filename,
        after={
            "units_created": created,
            "units_updated": updated,
            "units_unchanged": unchanged,
            "rows_rejected": len(errors),
        },
    )
    await session.commit()
    return {
        "rows_total": created + updated + unchanged + len(errors),
        "rows_created": created,
        "rows_updated": updated,
        "rows_unchanged": unchanged,
        "rows_rejected": len(errors),
        "errors": errors,
    }


class _OrgRowError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _required(row: dict[str, str], field: str) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise _OrgRowError(field, "mandatory field is missing")
    return value


async def _import_catalogue_row(
    session: AsyncSession, ctx: AuthContext, root: OrgUnit, row: dict[str, str]
) -> dict[str, int]:
    """Walk University → Faculty Division → School → Department → Program,
    creating or updating each level. Returns per-level counts."""
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    parent = root

    parent, outcome = await _upsert_unit(
        session, ctx, parent, "faculty_division",
        _required(row, "faculty_division_code"), _required(row, "faculty_division_name"), {},
    )
    counts[outcome] += 1

    # A School must declare its cadence — it decides every descendant Programme's
    # position ladder, so an import that omits it would create Schools no student
    # can be positioned in.
    cadence = row.get("cadence", "").strip().lower()
    if cadence not in CADENCES:
        raise _OrgRowError(
            "cadence", f"required for a School — must be one of: {', '.join(CADENCES)}"
        )
    parent, outcome = await _upsert_unit(
        session, ctx, parent, "school", _required(row, "school_code"),
        _required(row, "school_name"), {"cadence": cadence},
    )
    counts[outcome] += 1

    # Department is OPTIONAL (locked 28-07-2026): 12 of the university's 14
    # Schools have none, so a blank department column synthesises a default
    # Department mirroring the School, flagged auto_created so the org table can
    # show it as a structural placeholder rather than a real academic unit.
    school = parent
    dept_code = row.get("department_code", "").strip()
    dept_name = row.get("department_name", "").strip()
    if bool(dept_code) != bool(dept_name):
        raise _OrgRowError(
            "department_code" if not dept_code else "department_name",
            "give both department columns or neither (blank creates a default Department)",
        )
    auto = not dept_code
    if auto:
        dept_code, dept_name = school.code, school.name
    parent, outcome = await _upsert_unit(
        session, ctx, school, "department", dept_code, dept_name, {}, auto_created=auto
    )
    counts[outcome] += 1

    parent, outcome = await _upsert_unit(
        session, ctx, parent, "program", _required(row, "programme_code"),
        _required(row, "programme_name"), _programme_attrs(row),
    )
    counts[outcome] += 1
    return counts


def _programme_attrs(row: dict[str, str]) -> dict[str, object]:
    from unicore.modules.org.schemas import PROGRAMME_LEVELS, PROGRAMME_MODES

    level = row.get("level", "").strip()
    mode = row.get("mode", "").strip()
    category = row.get("category", "").strip()
    if level and level not in PROGRAMME_LEVELS:
        raise _OrgRowError("level", f"must be one of: {', '.join(PROGRAMME_LEVELS)}")
    if mode and mode not in PROGRAMME_MODES:
        raise _OrgRowError("mode", f"must be one of: {', '.join(PROGRAMME_MODES)}")
    if category and category not in PROGRAMME_CATEGORIES:
        raise _OrgRowError("category", f"must be one of: {', '.join(PROGRAMME_CATEGORIES)}")
    return {
        "level": level or None,
        "mode": mode or None,
        "category": category or None,
        "industry_partner": row.get("industry_partner", "").strip() or None,
        "duration_years": _whole_number(row, "duration_years", 1, 10),
        "internship_months": _whole_number(row, "internship_months", 0, 36),
        "lateral_entry_semester": _whole_number(row, "lateral_entry_semester", 1, 12),
    }


def _whole_number(row: dict[str, str], field: str, low: int, high: int) -> int | None:
    raw = row.get(field, "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        raise _OrgRowError(field, "must be a whole number") from None
    if not low <= value <= high:
        raise _OrgRowError(field, f"outside the plausible range ({low}–{high})")
    return value


async def _upsert_unit(
    session: AsyncSession,
    ctx: AuthContext,
    parent: OrgUnit,
    unit_type: str,
    code: str,
    name: str,
    attrs: dict[str, object],
    auto_created: bool = False,
) -> tuple[OrgUnit, str]:
    path = f"{parent.path}.{_label(code)}"
    existing = await dao.get_by_path(session, path)
    if existing is not None:
        if existing.type != unit_type:
            raise _OrgRowError(
                f"{unit_type}_code",
                f"code '{code}' already exists here as a {existing.type}",
            )
        before = _snapshot(existing)
        changed = False
        if existing.name != name:
            existing.name = name
            changed = True
        for key, value in attrs.items():
            if value is not None and getattr(existing, key) != value:
                setattr(existing, key, value)
                changed = True
        if attrs.get("cadence") and existing.cadence_unconfirmed:
            # An import stating the cadence is a decision, not the migration's guess.
            existing.cadence_unconfirmed = False
            changed = True
        if not changed:
            return existing, "unchanged"
        await audit_service.record(
            session,
            actor=ctx.user_id,
            action="org.unit.updated",
            object_type="org_unit",
            object_id=str(existing.id),
            scope=existing.path,
            before=before,
            after=_snapshot(existing),
        )
        return existing, "updated"

    if parent.status != "active":
        raise _OrgRowError(f"{unit_type}_code", "parent is deactivated")
    unit = OrgUnit(
        type=unit_type,
        name=name,
        code=code,
        parent_id=parent.id,
        path=path,
        campus_code=parent.campus_code,
        auto_created=auto_created,
        **attrs,
    )
    session.add(unit)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.created",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        after=_snapshot(unit),
    )
    return unit, "created"


async def update_unit(
    session: AsyncSession, ctx: AuthContext, unit_id: uuid.UUID, changes: dict[str, object]
) -> OrgUnit:
    """Inline edit from the org table. Code and parent are immutable here — both
    are embedded in descendant paths (use reparent for moves)."""
    unit = await _get_or_404(session, unit_id)
    before = _snapshot(unit)
    applied = False

    # Cadence and class-size cap live on Schools (and cadence may override on a
    # Programme), so they take the School/Programme path rather than the
    # Programmes-only path below.
    if changes.get("cadence") is not None and unit.cadence != changes["cadence"]:
        if unit.type not in ("school", "program"):
            raise HTTPException(
                status_code=422,
                detail=f"Curriculum cadence applies to Schools and Programmes, not a {unit.type}.",
            )
        await _assert_cadence_change_safe(session, unit, cast(str, changes["cadence"]))
        unit.cadence = cast(str, changes["cadence"])
        # An explicit decision replaces the migration's guess.
        unit.cadence_unconfirmed = False
        applied = True
    if changes.get("class_size_cap") is not None and unit.class_size_cap != changes[
        "class_size_cap"
    ]:
        if unit.type != "school":
            raise HTTPException(
                status_code=422,
                detail=f"The class-size cap is set per School, not on a {unit.type}.",
            )
        unit.class_size_cap = cast(int, changes["class_size_cap"])
        applied = True

    for key in (
        "name",
        "level",
        "duration_years",
        "mode",
        "category",
        "industry_partner",
        "internship_months",
        "lateral_entry_semester",
        "campus_code",
    ):
        if key in changes and changes[key] is not None and getattr(unit, key) != changes[key]:
            if key != "name" and unit.type != "program":
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} applies to Programmes only, not a {unit.type}.",
                )
            setattr(unit, key, changes[key])
            applied = True
    if not applied:
        return unit
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.updated",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        before=before,
        after=_snapshot(unit),
    )
    await session.commit()
    return unit


async def reactivate_unit(
    session: AsyncSession, ctx: AuthContext, unit_id: uuid.UUID
) -> OrgUnit:
    unit = await _get_or_404(session, unit_id)
    if unit.status == "active":
        return unit
    parent = await dao.get_by_id(session, unit.parent_id) if unit.parent_id else None
    if parent is not None and parent.status != "active":
        raise HTTPException(
            status_code=409, detail="Reactivate the parent unit first."
        )
    before = _snapshot(unit)
    unit.status = "active"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.unit.reactivated",
        object_type="org_unit",
        object_id=str(unit.id),
        scope=unit.path,
        before=before,
        after=_snapshot(unit),
    )
    await session.commit()
    return unit


async def list_units(
    session: AsyncSession,
    unit_type: str | None,
    search: str | None,
    include_inactive: bool,
    limit: int,
) -> list[OrgUnit]:
    """Flat, filterable listing powering the org table."""
    return list(await dao.list_units(session, unit_type, search, include_inactive, limit))


# --- subjects, offerings and venues (master data for TTM) --------------------


async def create_subject(
    session: AsyncSession, ctx: AuthContext, data: OrgUnitSubjectCreate
) -> Subject:
    department = await _get_or_404(session, data.department_id)
    if department.type != "department":
        raise HTTPException(
            status_code=422,
            detail=f"A subject is owned by a Department, not a {department.type}.",
        )
    if await dao.get_subject_by_code(session, data.code):
        raise HTTPException(status_code=409, detail=f"Subject '{data.code}' already exists.")

    subject = Subject(
        code=data.code,
        name=data.name,
        department_id=data.department_id,
        kind=data.kind,
        elective_group=data.elective_group,
        credits=data.credits,
    )
    session.add(subject)
    await session.flush()
    await _write_component_hours(session, subject, data.hours)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.subject.created",
        object_type="subject",
        object_id=str(subject.id),
        scope=department.path,
        after={"code": subject.code, "name": subject.name, "kind": subject.kind},
    )
    await session.commit()
    return subject


async def list_subjects(
    session: AsyncSession,
    department_id: uuid.UUID | None = None,
    kind: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
    limit: int = 500,
) -> list[dict[str, object]]:
    """Subjects with their component hours attached, in one round trip."""
    subjects = await dao.list_subjects(
        session, department_id, kind, search, include_inactive, limit
    )
    hours = await subject_hours(session, [s.id for s in subjects])
    return [
        {
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "department_id": s.department_id,
            "kind": s.kind,
            "elective_group": s.elective_group,
            "credits": s.credits,
            "hours": hours.get(s.id, {}),
            "status": s.status,
        }
        for s in subjects
    ]


async def update_subject(
    session: AsyncSession, ctx: AuthContext, subject_id: uuid.UUID, changes: dict[str, object]
) -> Subject:
    """Code, owning Department, kind and elective group are immutable: each is
    referenced by offerings, student choices, syllabus records and question
    banks, so a change would silently re-point history rather than correct it.
    Component hours are replaced wholesale when supplied."""
    subject = await dao.get_subject(session, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    before = {"name": subject.name, "credits": subject.credits}
    applied = False
    hours = cast("dict[str, int] | None", changes.get("hours"))
    if hours is not None:
        await _write_component_hours(session, subject, hours)
        applied = True
    for key in ("name", "credits"):
        value = changes.get(key)
        if value is not None and getattr(subject, key) != value:
            setattr(subject, key, value)
            applied = True
    if not applied:
        return subject
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.subject.updated",
        object_type="subject",
        object_id=str(subject.id),
        before=before,
        after={"name": subject.name, "credits": subject.credits},
    )
    await session.commit()
    return subject


async def create_offering(
    session: AsyncSession, ctx: AuthContext, subject_id: uuid.UUID,
    program_id: uuid.UUID | None, position: int | None, capacity: int | None = None,
) -> SubjectOffering:
    """Place a subject, either at (Programme, position) or university-wide.

    An **Open** elective is common to the whole university (locked 02-08-2026),
    so it is offered once with no Programme and no position rather than once per
    Programme — 113 rows that would immediately drift apart. Idempotent either
    way: re-offering the same thing returns the existing row.
    """
    subject = await dao.get_subject(session, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found.")

    university_wide = program_id is None
    if university_wide:
        if position is not None:
            raise HTTPException(
                status_code=422,
                detail="A university-wide offering has no ladder position — it is open to "
                "any student in any term.",
            )
        if subject.elective_group != "open":
            raise HTTPException(
                status_code=422,
                detail=f"Only Open electives are university-wide; '{subject.code}' is "
                f"{subject.kind}"
                + (f"/{subject.elective_group}" if subject.elective_group else "")
                + " and must name a Programme.",
            )
    else:
        if position is None:
            raise HTTPException(
                status_code=422, detail="A Programme-bound offering needs a ladder position."
            )
        programme = await _get_or_404(session, cast(uuid.UUID, program_id))
        if programme.type != "program":
            raise HTTPException(
                status_code=422,
                detail=f"Subjects are offered to a Programme, not a {programme.type}.",
            )
        if subject.elective_group == "open":
            raise HTTPException(
                status_code=422,
                detail=f"'{subject.code}' is an Open elective — it is offered university-wide, "
                "not to a single Programme.",
            )
        cadence = await effective_cadence(session, programme)
        ladder = position_ladder(cadence, programme.duration_years)
        if ladder and position not in ladder:
            raise HTTPException(
                status_code=422,
                detail=f"Position {position} is outside 1..{ladder[-1]} for '{programme.code}'.",
            )

    existing = await dao.find_offering(session, subject_id, program_id, position)
    if existing is not None:
        return existing

    offering = SubjectOffering(
        subject_id=subject_id, program_id=program_id, position=position, capacity=capacity
    )
    session.add(offering)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.offering.created",
        object_type="subject_offering",
        object_id=str(offering.id),
        after={
            "subject": subject.code,
            "programme": None if university_wide else str(program_id),
            "position": position,
        },
    )
    await session.commit()
    return offering


async def list_offerings(
    session: AsyncSession,
    program_id: uuid.UUID,
    position: int | None = None,
    kind: str | None = None,
    term_code: str | None = None,
) -> list[dict[str, object]]:
    """A Programme's curriculum: offerings paired with their subject.

    `term_code` adds the seats taken this term, which is what makes a capacity
    meaningful to whoever is looking at it.
    """
    rows = await dao.list_offerings(session, program_id, position, kind)
    hours = await subject_hours(session, [subject.id for _, subject in rows])
    taken: dict[uuid.UUID, int] = {}
    if term_code:
        taken = await dao.count_offering_takers(
            session, [offering.id for offering, _ in rows], term_code
        )
    return [
        {
            "id": offering.id,
            "subject_id": offering.subject_id,
            "program_id": offering.program_id,
            "position": offering.position,
            "capacity": offering.capacity,
            "seats_taken": taken.get(offering.id, 0),
            "status": offering.status,
            "subject": _subject_row(subject, hours.get(subject.id, {})),
        }
        for offering, subject in rows
    ]


async def set_offering_capacity(
    session: AsyncSession, ctx: AuthContext, offering_id: uuid.UUID, capacity: int | None,
    term_code: str | None = None,
) -> SubjectOffering:
    """Set (or clear) the seat limit. Refuses to drop it below the students who
    have already chosen — that would leave the offering over-subscribed with no
    honest way to decide whose place to withdraw."""
    offering = await get_offering(session, offering_id)
    if capacity is not None and term_code:
        taken = (
            await dao.count_offering_takers(session, [offering_id], term_code)
        ).get(offering_id, 0)
        if capacity < taken:
            raise HTTPException(
                status_code=409,
                detail=f"{taken} students have already chosen this elective for "
                f"{term_code}; capacity cannot be set below that.",
            )
    before = offering.capacity
    offering.capacity = capacity
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.offering.capacity-set",
        object_type="subject_offering",
        object_id=str(offering.id),
        before={"capacity": before},
        after={"capacity": capacity},
    )
    await session.commit()
    return offering


async def seats_taken(
    session: AsyncSession, offering_id: uuid.UUID, term_code: str | None
) -> int:
    """How many students hold this offering for a term. Works for university-wide
    offerings, which belong to no Programme and so cannot be found by listing one."""
    if not term_code:
        return 0
    return (await dao.count_offering_takers(session, [offering_id], term_code)).get(
        offering_id, 0
    )


async def claim_elective_seat(
    session: AsyncSession, offering_id: uuid.UUID, term_code: str, releasing: uuid.UUID | None
) -> None:
    """Reserve a seat on an offering, or raise if it is full.

    Locks the offering row first: capacity cannot be enforced by counting and
    then inserting, because two students taking the last seat concurrently
    would both read one free and both commit. `releasing` is the offering the
    student is switching away from — their old seat is discounted so a swap
    inside a full group is not blocked by their own occupancy.
    """
    offering = await dao.lock_offering(session, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Subject offering not found.")
    if offering.capacity is None:
        return
    taken = (await dao.count_offering_takers(session, [offering_id], term_code)).get(
        offering_id, 0
    )
    if releasing == offering_id:
        taken -= 1
    if taken >= offering.capacity:
        raise HTTPException(
            status_code=409,
            detail=f"This elective is full ({offering.capacity} seats). Choose another.",
        )


async def get_offering(session: AsyncSession, offering_id: uuid.UUID) -> SubjectOffering:
    offering = await dao.get_offering(session, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Subject offering not found.")
    return offering


async def get_subject(session: AsyncSession, subject_id: uuid.UUID) -> Subject:
    subject = await dao.get_subject(session, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    return subject


async def get_venue(session: AsyncSession, venue_id: uuid.UUID) -> Venue:
    venue = await dao.get_venue(session, venue_id)
    if venue is None:
        raise HTTPException(status_code=404, detail="Venue not found.")
    return venue


async def create_venue(
    session: AsyncSession, ctx: AuthContext, data: dict[str, object]
) -> Venue:
    code = cast(str, data["code"])
    if await dao.get_venue_by_code(session, code):
        raise HTTPException(status_code=409, detail=f"Venue '{code}' already exists.")
    venue = Venue(**data)
    session.add(venue)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.venue.created",
        object_type="venue",
        object_id=str(venue.id),
        after={"code": venue.code, "name": venue.name, "capacity": venue.capacity},
    )
    await session.commit()
    return venue


async def update_venue(
    session: AsyncSession, ctx: AuthContext, venue_id: uuid.UUID, changes: dict[str, object]
) -> Venue:
    venue = await dao.get_venue(session, venue_id)
    if venue is None:
        raise HTTPException(status_code=404, detail="Venue not found.")
    before = {"name": venue.name, "capacity": venue.capacity, "kind": venue.kind}
    applied = False
    for key in ("name", "capacity", "kind", "campus_code", "building", "room"):
        value = changes.get(key)
        if value is not None and getattr(venue, key) != value:
            setattr(venue, key, value)
            applied = True
    if not applied:
        return venue
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.venue.updated",
        object_type="venue",
        object_id=str(venue.id),
        before=before,
        after={"name": venue.name, "capacity": venue.capacity, "kind": venue.kind},
    )
    await session.commit()
    return venue


async def list_venues(
    session: AsyncSession,
    kind: str | None = None,
    search: str | None = None,
    include_inactive: bool = False,
    limit: int = 500,
) -> Sequence[Venue]:
    return await dao.list_venues(session, kind, search, include_inactive, limit)


async def import_subjects(
    session: AsyncSession, ctx: AuthContext, filename: str, content: bytes
) -> dict[str, object]:
    """One row per *offering*. A subject shared by three Programmes appears on
    three rows; it is defined by the first row that names it and merely placed
    by the rest, so its credits and hours cannot disagree between Programmes."""
    rows, errors = _read_csv(content, SUBJECT_CSV_COLUMNS)
    subjects_created = offerings_created = unchanged = 0

    for row_number, row in rows:
        try:
            # Resolve everything the row needs BEFORE creating anything: a row
            # naming an unknown Programme must not leave an orphan subject behind.
            code = _required(row, "subject_code")
            programme_code = row.get("programme_code", "").strip()
            position = _whole_number(row, "position", 1, 12)

            # Blank Programme means university-wide, which only an Open elective
            # may be. Everything is resolved before anything is created so a bad
            # row cannot leave an orphan subject behind.
            program_id: uuid.UUID | None = None
            if programme_code:
                programmes = await dao.find_by_code_in_scope(
                    session, programme_code, "program", None
                )
                if not programmes:
                    raise _OrgRowError("programme_code", f"unknown Programme '{programme_code}'")
                program_id = programmes[0].id
                if position is None:
                    raise _OrgRowError("position", "required — where the subject is taught")
            elif position is not None:
                raise _OrgRowError(
                    "position",
                    "a university-wide (Open) offering has no position — leave it blank",
                )

            subject = await dao.get_subject_by_code(session, code)
            if subject is None:
                subject = await _subject_from_row(session, ctx, row, code)
                subjects_created += 1

            before = await dao.find_offering(session, subject.id, program_id, position)
            await create_offering(session, ctx, subject.id, program_id, position)
            if before is None:
                offerings_created += 1
            else:
                unchanged += 1
        except _OrgRowError as err:
            errors.append(
                {
                    "row_number": row_number,
                    "field": err.field,
                    "reason": err.reason,
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items() if v),
                }
            )
        except HTTPException as err:
            errors.append(
                {
                    "row_number": row_number,
                    "field": "row",
                    "reason": str(err.detail),
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items() if v),
                }
            )

    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.subjects.imported",
        object_type="org_import",
        object_id=filename,
        after={
            "subjects_created": subjects_created,
            "offerings_created": offerings_created,
            "rows_rejected": len(errors),
        },
    )
    await session.commit()
    return {
        "rows_total": len(rows),
        "subjects_created": subjects_created,
        "offerings_created": offerings_created,
        "rows_unchanged": unchanged,
        "rows_rejected": len(errors),
        "errors": errors,
    }


def _component_hours_from_row(row: dict[str, str]) -> dict[str, int]:
    """Any `hours_<component>` column becomes that component's hours.

    Read from the row rather than a fixed list, so a component added to the
    university catalogue is importable at once — only the shipped template lags.
    """
    hours: dict[str, int] = {}
    for column, raw in row.items():
        if not column.startswith("hours_") or not raw.strip():
            continue
        try:
            value = int(raw)
        except ValueError:
            raise _OrgRowError(column, f"'{raw}' is not a number") from None
        if value < 0:
            raise _OrgRowError(column, "hours cannot be negative")
        if value:
            hours[column.removeprefix("hours_")] = value
    return hours


async def _subject_from_row(
    session: AsyncSession, ctx: AuthContext, row: dict[str, str], code: str
) -> Subject:
    department_code = _required(row, "department_code")
    departments = await dao.find_by_code_in_scope(session, department_code, "department", None)
    if not departments:
        raise _OrgRowError("department_code", f"unknown Department '{department_code}'")
    kind = (row.get("kind") or "core").strip().lower()
    group = (row.get("elective_group") or "").strip().lower() or None
    try:
        payload = OrgUnitSubjectCreate(
            code=code,
            name=_required(row, "subject_name"),
            department_id=departments[0].id,
            kind=kind,  # type: ignore[arg-type]
            elective_group=group,  # type: ignore[arg-type]
            credits=_whole_number(row, "credits", 0, 30) or 0,
            hours=_component_hours_from_row(row),
        )
    except ValueError as err:
        raise _OrgRowError("kind", str(err)) from None
    return await create_subject(session, ctx, payload)


async def import_venues(
    session: AsyncSession, ctx: AuthContext, filename: str, content: bytes
) -> dict[str, object]:
    rows, errors = _read_csv(content, VENUE_CSV_COLUMNS)
    created = updated = 0

    for row_number, row in rows:
        try:
            code = _required(row, "code")
            capacity = _whole_number(row, "capacity", 1, 2000)
            if capacity is None:
                raise _OrgRowError("capacity", "required — a room seats a number of people")
            fields: dict[str, object] = {
                "name": _required(row, "name"),
                "capacity": capacity,
                "kind": (row.get("kind") or "classroom").strip().lower(),
                "campus_code": row.get("campus_code", "").strip() or None,
                "building": row.get("building", "").strip() or None,
                "room": row.get("room", "").strip() or None,
            }
            if fields["kind"] not in VENUE_KINDS:
                raise _OrgRowError("kind", f"must be one of: {', '.join(VENUE_KINDS)}")

            existing = await dao.get_venue_by_code(session, code)
            if existing is None:
                await create_venue(session, ctx, {"code": code, **fields})
                created += 1
            else:
                await update_venue(session, ctx, existing.id, fields)
                updated += 1
        except _OrgRowError as err:
            errors.append(
                {
                    "row_number": row_number,
                    "field": err.field,
                    "reason": err.reason,
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items() if v),
                }
            )

    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.venues.imported",
        object_type="org_import",
        object_id=filename,
        after={"created": created, "updated": updated, "rows_rejected": len(errors)},
    )
    await session.commit()
    return {
        "rows_total": len(rows),
        "rows_created": created,
        "rows_updated": updated,
        "rows_rejected": len(errors),
        "errors": errors,
    }


def _read_csv(
    content: bytes, columns: tuple[str, ...]
) -> tuple[list[tuple[int, dict[str, str]]], list[dict[str, object]]]:
    """Shared parse for the master-data importers: header check, `#` notes
    stripped, rows numbered from 2 so they match the spreadsheet."""
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    reader = csv.DictReader(io.StringIO(strip_comments(text_content)))
    missing = set(columns) - {h.strip() for h in (reader.fieldnames or [])}
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header does not match the template — missing: {', '.join(sorted(missing))}.",
        )
    rows = [
        (index, {k: (v or "").strip() for k, v in raw.items() if k})
        for index, raw in enumerate(reader, start=2)
    ]
    return rows, []


# --- subject components (how a subject is taught) -----------------------------


async def list_components(session: AsyncSession) -> Sequence[SubjectComponent]:
    """The university-wide catalogue of ways a subject can be taught."""
    return await dao.list_components(session)


async def components_for_school(
    session: AsyncSession, school_id: uuid.UUID
) -> list[dict[str, object]]:
    """Components with a flag for whether this School teaches in them.

    A School that has never chosen falls back to the shipped defaults rather
    than an empty form — otherwise adding this feature would have silently
    emptied every existing School's subject form.
    """
    components = await dao.list_components(session)
    chosen = set(await dao.school_component_ids(session, school_id))
    return [
        {
            "id": component.id,
            "code": component.code,
            "name": component.name,
            "enabled": (component.id in chosen) if chosen else component.default_enabled,
        }
        for component in components
    ]


async def set_school_components(
    session: AsyncSession, ctx: AuthContext, school_id: uuid.UUID, codes: list[str]
) -> list[dict[str, object]]:
    """Choose which components a School teaches in (locked 05-08-2026)."""
    school = await _get_or_404(session, school_id)
    if school.type != "school":
        raise HTTPException(
            status_code=422, detail="Subject components are enabled per School."
        )
    components = {c.code: c for c in await dao.list_components(session)}
    unknown = [code for code in codes if code not in components]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown component(s): {', '.join(unknown)}. Available: "
            f"{', '.join(sorted(components))}.",
        )
    await dao.set_school_components(session, school_id, [components[c].id for c in codes])
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.school-components.set",
        object_type="org_unit",
        object_id=str(school_id),
        scope=school.path,
        after={"components": sorted(codes)},
    )
    await session.commit()
    return await components_for_school(session, school_id)


async def _write_component_hours(
    session: AsyncSession, subject: Subject, hours: dict[str, int] | None
) -> None:
    """Replace a subject's taught hours. Keys are component codes.

    Validated against the School that owns the subject: recording clinical hours
    on an Engineering subject is a mistake, not a preference, and catching it
    here keeps the catalogue meaningful.
    """
    if not hours:
        return
    components = {c.code: c for c in await dao.list_components(session)}
    unknown = [code for code in hours if code not in components]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown component(s): {', '.join(unknown)}. Available: "
            f"{', '.join(sorted(components))}.",
        )

    school_id = await dao.ancestor_of_type(session, subject.department_id, "school")
    if school_id is not None:
        enabled = {
            cast(str, row["code"])
            for row in await components_for_school(session, school_id)
            if row["enabled"]
        }
        disallowed = [code for code, value in hours.items() if value > 0 and code not in enabled]
        if disallowed:
            school = await _get_or_404(session, school_id)
            raise HTTPException(
                status_code=422,
                detail=f"{school.name} does not teach in: {', '.join(sorted(disallowed))}. "
                "Enable it for the School first.",
            )

    await dao.replace_component_hours(
        session, subject.id, {components[code].id: value for code, value in hours.items()}
    )


def _subject_row(subject: Subject, hours: dict[str, int]) -> dict[str, object]:
    return {
        "id": subject.id,
        "code": subject.code,
        "name": subject.name,
        "department_id": subject.department_id,
        "kind": subject.kind,
        "elective_group": subject.elective_group,
        "credits": subject.credits,
        "hours": hours,
        "status": subject.status,
    }


async def subject_hours(
    session: AsyncSession, subject_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """Component code -> hours, per subject."""
    raw = await dao.component_hours(session, subject_ids)
    return {
        subject_id: {component.code: hours for component, hours in rows}
        for subject_id, rows in raw.items()
    }
