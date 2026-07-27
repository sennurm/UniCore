"""Data access for the rbac module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.rbac.models import Grant, Role


async def get_role(session: AsyncSession, code: str) -> Role | None:
    return await session.get(Role, code)


async def get_grant(session: AsyncSession, grant_id: uuid.UUID) -> Grant | None:
    return await session.get(Grant, grant_id)


async def active_grants_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[tuple[Grant, Role]]:
    """Active grants with their role; unit paths resolve via org.service."""
    result = await session.execute(
        select(Grant, Role)
        .join(Role, Role.code == Grant.role_code)
        .where(Grant.user_id == user_id, Grant.status == "active")
    )
    return [(g, r) for g, r in result.all()]


async def active_grants_for_role_unit(
    session: AsyncSession, role_code: str, org_unit_id: uuid.UUID | None
) -> Sequence[Grant]:
    result = await session.execute(
        select(Grant).where(
            Grant.role_code == role_code,
            Grant.org_unit_id == org_unit_id if org_unit_id is not None
            else Grant.org_unit_id.is_(None),
            Grant.status == "active",
        )
    )
    return result.scalars().all()


async def grants_for_user(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Grant]:
    result = await session.execute(
        select(Grant).where(Grant.user_id == user_id).order_by(Grant.created_at)
    )
    return result.scalars().all()


async def term_bound_grants_on_units(
    session: AsyncSession, org_unit_ids: list[uuid.UUID], status: str, revoke_cause: str | None
) -> Sequence[Grant]:
    query = (
        select(Grant)
        .join(Role, Role.code == Grant.role_code)
        .where(Grant.org_unit_id.in_(org_unit_ids), Role.term_bound, Grant.status == status)
    )
    if revoke_cause is not None:
        query = query.where(Grant.revoke_cause == revoke_cause)
    result = await session.execute(query)
    return result.scalars().all()
