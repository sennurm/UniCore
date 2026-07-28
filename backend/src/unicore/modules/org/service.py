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
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.org import dao
from unicore.modules.org.models import PARENT_TYPE_OF, OrgUnit
from unicore.modules.org.schemas import ORG_CSV_COLUMNS, OrgUnitCreate


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

    body = "\n".join(
        line for line in text_content.splitlines() if not line.lstrip().startswith("#")
    )
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
    for unit_type, code_field, name_field in (
        ("faculty_division", "faculty_division_code", "faculty_division_name"),
        ("school", "school_code", "school_name"),
        ("department", "department_code", "department_name"),
        ("program", "programme_code", "programme_name"),
    ):
        code = _required(row, code_field)
        name = _required(row, name_field)
        attrs = _programme_attrs(row) if unit_type == "program" else {}
        parent, outcome = await _upsert_unit(session, ctx, parent, unit_type, code, name, attrs)
        counts[outcome] += 1
    return counts


def _programme_attrs(row: dict[str, str]) -> dict[str, object]:
    from unicore.modules.org.schemas import PROGRAMME_LEVELS, PROGRAMME_MODES

    level = row.get("level", "").strip()
    mode = row.get("mode", "").strip()
    duration = row.get("duration_years", "").strip()
    if level and level not in PROGRAMME_LEVELS:
        raise _OrgRowError("level", f"must be one of: {', '.join(PROGRAMME_LEVELS)}")
    if mode and mode not in PROGRAMME_MODES:
        raise _OrgRowError("mode", f"must be one of: {', '.join(PROGRAMME_MODES)}")
    years: int | None = None
    if duration:
        try:
            years = int(float(duration))
        except ValueError:
            raise _OrgRowError("duration_years", "must be a whole number of years") from None
        if not 1 <= years <= 10:
            raise _OrgRowError("duration_years", "outside the plausible range (1–10)")
    return {"level": level or None, "duration_years": years, "mode": mode or None}


async def _upsert_unit(
    session: AsyncSession,
    ctx: AuthContext,
    parent: OrgUnit,
    unit_type: str,
    code: str,
    name: str,
    attrs: dict[str, object],
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
    for key in ("name", "level", "duration_years", "mode", "campus_code"):
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
