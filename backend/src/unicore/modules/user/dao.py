"""Data access for the user module. All SQLAlchemy queries for its tables live here."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.user.models import User


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_by_erp_id(session: AsyncSession, erp_id: str) -> User | None:
    result = await session.execute(select(User).where(User.erp_id == erp_id))
    return result.scalar_one_or_none()


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()
