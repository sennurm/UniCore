"""Data access for the timetable module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.timetable.models import AcademicTerm


async def get_term(session: AsyncSession, term_id: uuid.UUID) -> AcademicTerm | None:
    return await session.get(AcademicTerm, term_id)


async def latest_version(
    session: AsyncSession, school_id: uuid.UUID, term_code: str
) -> AcademicTerm | None:
    result = await session.execute(
        select(AcademicTerm)
        .where(AcademicTerm.school_id == school_id, AcademicTerm.term_code == term_code)
        .order_by(AcademicTerm.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def approved_term(
    session: AsyncSession, school_id: uuid.UUID, term_code: str
) -> AcademicTerm | None:
    result = await session.execute(
        select(AcademicTerm)
        .where(
            AcademicTerm.school_id == school_id,
            AcademicTerm.term_code == term_code,
            AcademicTerm.status == "approved",
        )
        .order_by(AcademicTerm.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_terms(session: AsyncSession, school_id: uuid.UUID) -> Sequence[AcademicTerm]:
    result = await session.execute(
        select(AcademicTerm)
        .where(AcademicTerm.school_id == school_id)
        .order_by(AcademicTerm.term_code, AcademicTerm.version.desc())
    )
    return result.scalars().all()
