"""Data access for the timetable module. All SQLAlchemy queries for its tables live here."""

import uuid
from collections.abc import Sequence
from datetime import time
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.timetable.models import (
    AcademicTerm,
    Period,
    PeriodGrid,
    TimetableApproval,
    TimetableDraft,
    TimetableEntry,
)


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


async def list_all_terms(session: AsyncSession) -> Sequence[AcademicTerm]:
    """Every School's terms in one shot — the term-setup screen shows calendar
    status beside each School, which would otherwise be one request per School."""
    result = await session.execute(
        select(AcademicTerm).order_by(
            AcademicTerm.term_code.desc(), AcademicTerm.version.desc()
        )
    )
    return result.scalars().all()


async def list_terms(session: AsyncSession, school_id: uuid.UUID) -> Sequence[AcademicTerm]:
    result = await session.execute(
        select(AcademicTerm)
        .where(AcademicTerm.school_id == school_id)
        .order_by(AcademicTerm.term_code, AcademicTerm.version.desc())
    )
    return result.scalars().all()


# --- period grids -------------------------------------------------------------


async def latest_grid(session: AsyncSession, school_id: uuid.UUID) -> PeriodGrid | None:
    result = await session.execute(
        select(PeriodGrid)
        .where(PeriodGrid.school_id == school_id)
        .order_by(PeriodGrid.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def active_grid(session: AsyncSession, school_id: uuid.UUID) -> PeriodGrid | None:
    result = await session.execute(
        select(PeriodGrid).where(
            PeriodGrid.school_id == school_id, PeriodGrid.status == "active"
        )
    )
    return result.scalar_one_or_none()


async def get_grid(session: AsyncSession, grid_id: uuid.UUID) -> PeriodGrid | None:
    return await session.get(PeriodGrid, grid_id)


async def list_grids(session: AsyncSession, school_id: uuid.UUID) -> Sequence[PeriodGrid]:
    result = await session.execute(
        select(PeriodGrid)
        .where(PeriodGrid.school_id == school_id)
        .order_by(PeriodGrid.version.desc())
    )
    return result.scalars().all()


async def list_periods(session: AsyncSession, grid_id: uuid.UUID) -> Sequence[Period]:
    result = await session.execute(
        select(Period).where(Period.grid_id == grid_id).order_by(Period.sequence)
    )
    return result.scalars().all()


async def get_period(session: AsyncSession, period_id: uuid.UUID) -> Period | None:
    return await session.get(Period, period_id)


# --- drafts, entries, approvals ----------------------------------------------


async def get_draft(session: AsyncSession, draft_id: uuid.UUID) -> TimetableDraft | None:
    return await session.get(TimetableDraft, draft_id)


async def latest_draft_version(
    session: AsyncSession, school_id: uuid.UUID, term_code: str
) -> TimetableDraft | None:
    result = await session.execute(
        select(TimetableDraft)
        .where(TimetableDraft.school_id == school_id, TimetableDraft.term_code == term_code)
        .order_by(TimetableDraft.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def published_draft(
    session: AsyncSession, school_id: uuid.UUID, term_code: str
) -> TimetableDraft | None:
    result = await session.execute(
        select(TimetableDraft).where(
            TimetableDraft.school_id == school_id,
            TimetableDraft.term_code == term_code,
            TimetableDraft.status == "published",
        )
    )
    return result.scalar_one_or_none()


async def list_entries(
    session: AsyncSession, draft_id: uuid.UUID
) -> Sequence[tuple[TimetableEntry, Period]]:
    result = await session.execute(
        select(TimetableEntry, Period)
        .join(Period, Period.id == TimetableEntry.period_id)
        .where(TimetableEntry.draft_id == draft_id)
        .order_by(TimetableEntry.day_of_week, Period.sequence)
    )
    return [(entry, period) for entry, period in result.all()]


async def get_entry(session: AsyncSession, entry_id: uuid.UUID) -> TimetableEntry | None:
    return await session.get(TimetableEntry, entry_id)


async def find_clashes(
    session: AsyncSession,
    *,
    term_code: str,
    day_of_week: int,
    start_time: time,
    end_time: time,
    section_id: uuid.UUID,
    faculty_user_id: uuid.UUID,
    venue_id: uuid.UUID,
    exclude_entry_id: uuid.UUID | None = None,
) -> Sequence[Row[Any]]:
    """Entries that collide with a candidate slot (TTM-FR-04).

    Three ways to collide: the same Faculty Member, the same venue, or the same
    Section — each meaning one of them would have to be in two places at once.

    Overlap is by **absolute clock time**, not Period index, so two Schools
    running different grids still collide on a shared room. Half-open
    comparison: a class ending at 10:00 does not clash with one starting at
    10:00.

    Drafts collide with drafts as well as with published timetables (decision
    02-08-2026): a room taken by another School's unpublished draft is a real
    conflict, and finding it now is cheaper than at publish time. Superseded and
    archived timetables are ignored — they are history.
    """
    query = (
        select(
            TimetableEntry.id.label("entry_id"),
            TimetableEntry.section_id,
            TimetableEntry.faculty_user_id,
            TimetableEntry.venue_id,
            TimetableDraft.id.label("draft_id"),
            TimetableDraft.status.label("draft_status"),
            TimetableDraft.school_id,
            Period.name.label("period_name"),
            Period.start_time,
            Period.end_time,
        )
        .join(TimetableDraft, TimetableDraft.id == TimetableEntry.draft_id)
        .join(Period, Period.id == TimetableEntry.period_id)
        .where(
            TimetableDraft.term_code == term_code,
            TimetableDraft.status.in_(("draft", "published")),
            TimetableEntry.day_of_week == day_of_week,
            Period.start_time < end_time,
            Period.end_time > start_time,
            (TimetableEntry.faculty_user_id == faculty_user_id)
            | (TimetableEntry.venue_id == venue_id)
            | (TimetableEntry.section_id == section_id),
        )
    )
    if exclude_entry_id is not None:
        query = query.where(TimetableEntry.id != exclude_entry_id)
    result = await session.execute(query)
    return result.all()


async def list_approvals(
    session: AsyncSession, draft_id: uuid.UUID
) -> Sequence[TimetableApproval]:
    result = await session.execute(
        select(TimetableApproval).where(TimetableApproval.draft_id == draft_id)
    )
    return result.scalars().all()


async def find_approval(
    session: AsyncSession, draft_id: uuid.UUID, department_id: uuid.UUID
) -> TimetableApproval | None:
    result = await session.execute(
        select(TimetableApproval).where(
            TimetableApproval.draft_id == draft_id,
            TimetableApproval.department_id == department_id,
        )
    )
    return result.scalar_one_or_none()
