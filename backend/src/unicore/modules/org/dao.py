"""Data access for the org module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.org.models import OrgUnit


async def get_by_id(session: AsyncSession, unit_id: uuid.UUID) -> OrgUnit | None:
    return await session.get(OrgUnit, unit_id)


async def get_root(session: AsyncSession) -> OrgUnit | None:
    result = await session.execute(select(OrgUnit).where(OrgUnit.type == "university"))
    return result.scalar_one_or_none()


async def list_children(session: AsyncSession, parent_id: uuid.UUID) -> Sequence[OrgUnit]:
    result = await session.execute(
        select(OrgUnit).where(OrgUnit.parent_id == parent_id).order_by(OrgUnit.code)
    )
    return result.scalars().all()


async def paths_for_ids(
    session: AsyncSession, unit_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not unit_ids:
        return {}
    result = await session.execute(
        select(OrgUnit.id, OrgUnit.path).where(OrgUnit.id.in_(unit_ids))
    )
    return {row.id: row.path for row in result}


async def path_exists(session: AsyncSession, path: str) -> bool:
    result = await session.execute(select(OrgUnit.id).where(OrgUnit.path == path))
    return result.scalar_one_or_none() is not None


async def is_descendant(session: AsyncSession, ancestor_path: str, candidate_path: str) -> bool:
    result = await session.execute(
        text("SELECT CAST(:candidate AS ltree) <@ CAST(:ancestor AS ltree)"),
        {"candidate": candidate_path, "ancestor": ancestor_path},
    )
    return bool(result.scalar_one())


async def move_subtree(
    session: AsyncSession, subtree_root_path: str, old_parent_path: str, new_parent_path: str
) -> None:
    """Re-root every unit under subtree_root_path from old parent to new parent."""
    await session.execute(
        text(
            "UPDATE org_units "
            "SET path = CAST(:new_parent AS ltree) "
            "|| subpath(path, nlevel(CAST(:old_parent AS ltree))) "
            "WHERE path <@ CAST(:subtree AS ltree)"
        ),
        {
            "new_parent": new_parent_path,
            "old_parent": old_parent_path,
            "subtree": subtree_root_path,
        },
    )
