"""Data access for the onboarding module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.onboarding.models import (
    Batch,
    ImportRowError,
    ImportRun,
    SectionMembership,
    StaffProfile,
    StudentElectiveChoice,
    StudentProfile,
)


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> ImportRun | None:
    return await session.get(ImportRun, run_id)


async def list_runs(
    session: AsyncSession, limit: int, uploaded_by: str | None = None
) -> Sequence[ImportRun]:
    """`uploaded_by` filters in the query — a scoped caller's rows never leave the DB."""
    query = select(ImportRun).order_by(ImportRun.created_at.desc()).limit(limit)
    if uploaded_by is not None:
        query = query.where(ImportRun.uploaded_by == uploaded_by)
    result = await session.execute(query)
    return result.scalars().all()


async def run_errors(session: AsyncSession, run_id: uuid.UUID) -> Sequence[ImportRowError]:
    result = await session.execute(
        select(ImportRowError)
        .where(ImportRowError.run_id == run_id)
        .order_by(ImportRowError.row_number)
    )
    return result.scalars().all()


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> StudentProfile | None:
    return await session.get(StudentProfile, user_id)


async def count_students_by_position(
    session: AsyncSession, program_id: uuid.UUID
) -> dict[int, int]:
    """Students of a Programme grouped by position, for Section sizing.

    Counts `imported` as well as `active`: a freshly imported intake has not
    collected its credentials yet, and those are exactly the students Sections
    are being generated for. Only `withdrawn`/`deactivated` are excluded.

    Aggregated in SQL — one row per position rather than per student — because
    Section generation asks this for every Programme in a School at once.
    `users` is reached by raw SQL rather than by importing the user module's
    model: the FK already couples these tables, and the alternative (loading
    every student to filter in Python) is what the module rule exists to prevent.
    """
    result = await session.execute(
        text(
            "SELECT sp.position AS position, COUNT(*) AS n "
            "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
            "WHERE sp.program_id = :program_id AND u.status IN ('active', 'imported') "
            "GROUP BY sp.position"
        ),
        {"program_id": str(program_id)},
    )
    return {row.position: row.n for row in result}


async def get_batch_by_id(session: AsyncSession, batch_id: uuid.UUID) -> Batch | None:
    return await session.get(Batch, batch_id)


async def find_batch(
    session: AsyncSession, program_id: uuid.UUID, joining_year: int
) -> Batch | None:
    result = await session.execute(
        select(Batch).where(Batch.program_id == program_id, Batch.joining_year == joining_year)
    )
    return result.scalar_one_or_none()


async def list_batches_for_programs(
    session: AsyncSession, program_ids: list[uuid.UUID]
) -> Sequence[Batch]:
    if not program_ids:
        return []
    result = await session.execute(
        select(Batch).where(Batch.program_id.in_(program_ids)).order_by(Batch.code)
    )
    return result.scalars().all()


async def get_staff_by_employee_id(
    session: AsyncSession, employee_id: str
) -> StaffProfile | None:
    result = await session.execute(
        select(StaffProfile).where(StaffProfile.employee_id == employee_id)
    )
    return result.scalar_one_or_none()


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


async def elective_choices_for(
    session: AsyncSession, user_id: uuid.UUID, term_code: str
) -> Sequence[StudentElectiveChoice]:
    result = await session.execute(
        select(StudentElectiveChoice).where(
            StudentElectiveChoice.user_id == user_id,
            StudentElectiveChoice.term_code == term_code,
        )
    )
    return result.scalars().all()


async def elective_choice_for_group(
    session: AsyncSession, user_id: uuid.UUID, term_code: str, elective_group: str
) -> StudentElectiveChoice | None:
    result = await session.execute(
        select(StudentElectiveChoice).where(
            StudentElectiveChoice.user_id == user_id,
            StudentElectiveChoice.term_code == term_code,
            StudentElectiveChoice.elective_group == elective_group,
        )
    )
    return result.scalar_one_or_none()
