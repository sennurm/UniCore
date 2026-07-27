"""Business rules for the org module. The only layer other modules may call.

Covers AUTH-FR-19: Super Admin CRUD over Faculty Division/School/Department/
Program; deactivate-never-delete; every change audited in-transaction.
Section instances are NOT created here (TTM-FR-19) — TTM's term setup will call
`create_section_instance` in its own milestone.
"""

import re
import uuid
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.org import dao
from unicore.modules.org.models import PARENT_TYPE_OF, OrgUnit
from unicore.modules.org.schemas import OrgUnitCreate


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


async def get_unit(session: AsyncSession, unit_id: uuid.UUID) -> OrgUnit:
    return await _get_or_404(session, unit_id)


async def list_children(session: AsyncSession, parent_id: uuid.UUID) -> Sequence[OrgUnit]:
    return await dao.list_children(session, parent_id)


async def _get_or_404(session: AsyncSession, unit_id: uuid.UUID) -> OrgUnit:
    unit = await dao.get_by_id(session, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Org unit not found.")
    return unit
