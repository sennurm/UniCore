"""Data access for the auth module. All SQLAlchemy queries for its tables live here."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.auth.models import ConsentRecord, Device, DeviceChangeRequest, OtpChallenge


async def get_challenge(session: AsyncSession, challenge_id: uuid.UUID) -> OtpChallenge | None:
    return await session.get(OtpChallenge, challenge_id)


async def active_device(session: AsyncSession, user_id: uuid.UUID) -> Device | None:
    result = await session.execute(
        select(Device).where(Device.user_id == user_id, Device.status == "active")
    )
    return result.scalar_one_or_none()


async def get_change_request(
    session: AsyncSession, request_id: uuid.UUID
) -> DeviceChangeRequest | None:
    return await session.get(DeviceChangeRequest, request_id)


async def latest_consent(session: AsyncSession, user_id: uuid.UUID) -> ConsentRecord | None:
    result = await session.execute(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id)
        .order_by(ConsentRecord.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
