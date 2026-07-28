"""Data access for the user module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.user.models import Grievance, User


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_by_erp_id(session: AsyncSession, erp_id: str) -> User | None:
    result = await session.execute(select(User).where(User.erp_id == erp_id))
    return result.scalar_one_or_none()


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_grievance(session: AsyncSession, grievance_id: uuid.UUID) -> Grievance | None:
    return await session.get(Grievance, grievance_id)


async def list_grievances(
    session: AsyncSession, user_id: uuid.UUID | None, status: str | None
):
    query = select(Grievance).order_by(Grievance.created_at.desc())
    if user_id is not None:
        query = query.where(Grievance.user_id == user_id)
    if status is not None:
        query = query.where(Grievance.status == status)
    result = await session.execute(query)
    return result.scalars().all()


async def list_users(
    session: AsyncSession, search: str | None, status: str | None, limit: int
) -> Sequence[User]:
    query = select(User).order_by(User.username).limit(limit)
    if status:
        query = query.where(User.status == status)
    if search:
        pattern = f"%{search.lower()}%"
        query = query.where(
            func.lower(User.username).like(pattern)
            | func.lower(User.full_name).like(pattern)
            | func.lower(func.coalesce(User.erp_id, "")).like(pattern)
        )
    result = await session.execute(query)
    return result.scalars().all()
