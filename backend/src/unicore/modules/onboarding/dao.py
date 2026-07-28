"""Data access for the onboarding module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.onboarding.models import (
    ImportBatch,
    ImportRowError,
    SectionMembership,
    StudentProfile,
)


async def get_batch(session: AsyncSession, batch_id: uuid.UUID) -> ImportBatch | None:
    return await session.get(ImportBatch, batch_id)


async def list_batches(session: AsyncSession, limit: int) -> Sequence[ImportBatch]:
    result = await session.execute(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


async def batch_errors(session: AsyncSession, batch_id: uuid.UUID) -> Sequence[ImportRowError]:
    result = await session.execute(
        select(ImportRowError)
        .where(ImportRowError.batch_id == batch_id)
        .order_by(ImportRowError.row_number)
    )
    return result.scalars().all()


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> StudentProfile | None:
    return await session.get(StudentProfile, user_id)


async def roll_number_holder(
    session: AsyncSession, program_id: uuid.UUID, admission_year: int, roll_number: str
) -> StudentProfile | None:
    result = await session.execute(
        select(StudentProfile).where(
            StudentProfile.program_id == program_id,
            StudentProfile.admission_year == admission_year,
            StudentProfile.roll_number == roll_number,
        )
    )
    return result.scalar_one_or_none()


async def list_pending_delivery(session: AsyncSession) -> Sequence[StudentProfile]:
    result = await session.execute(
        select(StudentProfile).where(StudentProfile.credential_delivery == "pending")
    )
    return result.scalars().all()


async def open_membership(
    session: AsyncSession, user_id: uuid.UUID
) -> SectionMembership | None:
    result = await session.execute(
        select(SectionMembership).where(
            SectionMembership.user_id == user_id, SectionMembership.effective_to.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def membership_as_of(
    session: AsyncSession, user_id: uuid.UUID, as_of: date
) -> SectionMembership | None:
    """Membership-as-of-date read consumed by TTM/ATT (ONB-FR-10).

    Intervals are half-open: [effective_from, effective_to). A membership closed
    on date D does not cover D — the successor membership starting on D does, so
    a move never leaves the student in two Sections (or none) on the switch day.
    """
    result = await session.execute(
        select(SectionMembership)
        .where(
            SectionMembership.user_id == user_id,
            SectionMembership.effective_from <= as_of,
        )
        .order_by(SectionMembership.effective_from.desc())
    )
    for membership in result.scalars().all():
        if membership.effective_to is None or membership.effective_to > as_of:
            return membership
    return None


async def section_roster_as_of(
    session: AsyncSession, section_id: uuid.UUID, as_of: date
) -> Sequence[SectionMembership]:
    result = await session.execute(
        select(SectionMembership).where(
            SectionMembership.section_id == section_id,
            SectionMembership.effective_from <= as_of,
        )
    )
    return [
        m
        for m in result.scalars().all()
        if m.effective_to is None or m.effective_to > as_of  # half-open [from, to)
    ]


async def memberships_for(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[SectionMembership]:
    result = await session.execute(
        select(SectionMembership)
        .where(SectionMembership.user_id == user_id)
        .order_by(SectionMembership.effective_from)
    )
    return result.scalars().all()
