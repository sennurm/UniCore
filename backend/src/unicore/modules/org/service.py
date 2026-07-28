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
    """Path lookup for other modules' bulk importers."""
    return await dao.get_by_path(session, path)


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


# --- CSV import (Super Admin; same rules as the single-unit endpoint) ---------

MAX_ORG_FILE_BYTES = 5 * 1024 * 1024
_DEPTH_ORDER = ("faculty_division", "school", "department", "program")


async def import_csv(
    session: AsyncSession, ctx: AuthContext, filename: str, content: bytes
) -> dict[str, object]:
    """Bulk org creation. Rows reference parents by dotted path and are processed
    shallowest-first, so one file can build a whole subtree in any order.

    Partial commit like the student import: valid rows land, invalid rows come back
    as an error report. Existing units are never duplicated or deleted.
    """
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    if len(content) > MAX_ORG_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 5 MB limit.")
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    # Tolerate the template's leading comment lines.
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

    rows = [
        (index, {k: (v or "").strip() for k, v in raw.items() if k})
        for index, raw in enumerate(reader, start=2)
    ]
    # Shallowest first so parents exist before their children, whatever the file order.
    rows.sort(key=lambda item: _DEPTH_ORDER.index(item[1].get("type", ""))
              if item[1].get("type", "") in _DEPTH_ORDER else len(_DEPTH_ORDER))

    created = updated = unchanged = 0
    errors: list[dict[str, object]] = []

    for row_number, row in rows:
        try:
            outcome = await _import_org_row(session, ctx, row)
        except _OrgRowError as err:
            errors.append(
                {
                    "row_number": row_number,
                    "field": err.field,
                    "reason": err.reason,
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items()),
                }
            )
            continue
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        else:
            unchanged += 1

    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="org.import.completed",
        object_type="org_import",
        object_id=filename,
        after={
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "rejected": len(errors),
        },
    )
    await session.commit()
    return {
        "rows_total": len(rows),
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


async def _import_org_row(
    session: AsyncSession, ctx: AuthContext, row: dict[str, str]
) -> str:
    unit_type = row.get("type", "").strip().lower()
    code = row.get("code", "").strip()
    name = row.get("name", "").strip()
    parent_path = row.get("parent_path", "").strip().lower()

    if unit_type == "section":
        raise _OrgRowError(
            "type", "Sections are created per term by the Timetable Cell (TTM-FR-19)"
        )
    if unit_type not in _DEPTH_ORDER:
        raise _OrgRowError("type", f"must be one of {', '.join(_DEPTH_ORDER)}")
    if not code:
        raise _OrgRowError("code", "mandatory field is missing")
    if not name:
        raise _OrgRowError("name", "mandatory field is missing")
    if not parent_path:
        raise _OrgRowError("parent_path", "mandatory for every non-university row")

    parent = await dao.get_by_path(session, parent_path)
    if parent is None:
        raise _OrgRowError("parent_path", f"no org unit at path '{parent_path}'")
    if parent.type != PARENT_TYPE_OF[unit_type]:
        raise _OrgRowError(
            "parent_path",
            f"a {unit_type} must sit under a {PARENT_TYPE_OF[unit_type]}, not a {parent.type}",
        )
    if parent.status != "active":
        raise _OrgRowError("parent_path", "parent is deactivated")

    path = f"{parent.path}.{_label(code)}"
    existing = await dao.get_by_path(session, path)
    if existing is not None:
        if existing.name == name:
            return "unchanged"
        before = _snapshot(existing)
        existing.name = name
        await audit_service.record(
            session,
            actor=ctx.user_id,
            action="org.unit.renamed",
            object_type="org_unit",
            object_id=str(existing.id),
            scope=existing.path,
            before=before,
            after=_snapshot(existing),
        )
        return "updated"

    unit = OrgUnit(
        type=unit_type,
        name=name,
        code=code,
        parent_id=parent.id,
        path=path,
        campus_code=row.get("campus_code") or parent.campus_code,
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
    return "created"
