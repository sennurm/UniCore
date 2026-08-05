"""ORM tables owned by the timetable module (aggregated into core.db.Base.metadata).

Milestone-2 slice only: the per-School academic term (TTM-FR-18). Period grids,
drafts, and published timetables arrive with the full TTM milestone.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from unicore.core.db import Base

TERM_STATUSES = ("draft", "approved", "superseded")
TERM_PARITIES = ("odd", "even")


class AcademicTerm(Base):
    """Per-School term calendar: uploaded by School office staff, active only on
    recorded School Incharge approval; amendments create a new version."""

    __tablename__ = "academic_terms"
    __table_args__ = (
        UniqueConstraint("school_id", "term_code", "version", name="uq_terms_school_code_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    term_code: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Soft-signal dates (TTM-FR-18): [{"from": "...", "to": "...", "label": "..."}]
    exam_ranges: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    special_events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # Backstop for AUTH-FR-13 term-bound grant revocation.
    archival_backstop_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Which half of a semester ladder is live this term (TTM-FR-18). Section
    # generation refuses to run without it rather than guessing — the wrong
    # parity creates the wrong half of every Programme in the School.
    parity: Mapped[str | None] = mapped_column(
        Enum(*TERM_PARITIES, name="term_parity", create_type=False), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Enum(*TERM_STATUSES, name="academic_term_status", create_type=False),
        nullable=False,
        default="draft",
    )
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


GRID_STATUSES = ("draft", "active", "superseded")
DRAFT_STATUSES = ("draft", "published", "superseded", "archived")
APPROVAL_STATUSES = ("pending", "approved", "rejected")
# ISO: Monday = 1. Stored as an int so a grid can run any subset of days.
DAYS_OF_WEEK = (1, 2, 3, 4, 5, 6, 7)


class PeriodGrid(Base):
    """A School's teaching day: named Periods with real clock times.

    Versioned, never edited in place once a timetable references it — a change
    would silently move classes for people already holding the published
    schedule (TTM-FR-02).
    """

    __tablename__ = "period_grids"
    __table_args__ = (
        UniqueConstraint("school_id", "version", name="uq_period_grid_school_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*GRID_STATUSES, name="period_grid_status", create_type=False),
        nullable=False,
        default="draft",
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Period(Base):
    """One slot in a grid. The clock times are what clash detection compares, so
    two Schools running different grids still collide correctly on a shared room
    (TTM-FR-04: "by absolute time overlap, not by Period index")."""

    __tablename__ = "periods"
    __table_args__ = (
        UniqueConstraint("grid_id", "sequence", name="uq_period_grid_sequence"),
        Index("ix_periods_grid", "grid_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("period_grids.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class TimetableDraft(Base):
    """One School's timetable for one term, published atomically.

    Entries belong to Sections, but the draft is the unit that is approved and
    published: a timetable is only consistent as a whole, and clash-free per
    Section says nothing about the pair (decision 02-08-2026).
    """

    __tablename__ = "timetable_drafts"
    __table_args__ = (
        UniqueConstraint("school_id", "term_code", "version", name="uq_draft_school_term_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    term_code: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    grid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("period_grids.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*DRAFT_STATUSES, name="timetable_draft_status", create_type=False),
        nullable=False,
        default="draft",
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TimetableEntry(Base):
    """One weekly recurring class: Section x day x Period.

    ATT expands these against the School calendar to create dated Sessions, so a
    term stays a few hundred rows rather than tens of thousands.
    """

    __tablename__ = "timetable_entries"
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "section_id", "day_of_week", "period_id", name="uq_entry_section_slot"
        ),
        Index("ix_entries_draft", "draft_id"),
        Index("ix_entries_faculty", "faculty_user_id"),
        Index("ix_entries_venue", "venue_id"),
        Index("ix_entries_section", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("timetable_drafts.id"), nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("periods.id"), nullable=False)
    offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subject_offerings.id"), nullable=False
    )
    faculty_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TimetableApproval(Base):
    """One HoD's decision on the portion of a draft touching their Department."""

    __tablename__ = "timetable_approvals"
    __table_args__ = (
        UniqueConstraint("draft_id", "department_id", name="uq_approval_draft_department"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("timetable_drafts.id"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*APPROVAL_STATUSES, name="timetable_approval_status", create_type=False),
        nullable=False,
        default="pending",
    )
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- calendar: university holidays + School working days (TTM-FR-26/27) -------

HOLIDAY_KINDS = ("public", "vacation", "local")
HOLIDAY_STATUSES = ("active", "withdrawn")

#: Shipped default for a School that has never declared its week: Monday to
#: Saturday, every Saturday. A School with no pattern must not resolve to zero
#: teaching days — an unconfigured School would silently produce an empty term.
DEFAULT_WORKING_DAYS: dict[str, object] = {"1": True, "2": True, "3": True, "4": True,
                                           "5": True, "6": True}


class UniversityHoliday(Base):
    """A closed date range for the whole university (TTM-FR-26).

    A range rather than a date because a vacation block moves as one edit, not
    fourteen; a single-day holiday is simply a one-day range. `campus_codes`
    empty means university-wide — a regional festival names the campuses that
    observe it, so the others stay open.
    """

    __tablename__ = "university_holidays"
    __table_args__ = (
        CheckConstraint("to_date >= from_date", name="ck_holiday_range_ordered"),
        Index("ix_holidays_dates", "from_date", "to_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum(*HOLIDAY_KINDS, name="holiday_kind", create_type=False), nullable=False
    )
    campus_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        Enum(*HOLIDAY_STATUSES, name="holiday_status", create_type=False),
        nullable=False,
        default="active",
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SchoolWorkingPattern(Base):
    """Which weekdays a School teaches (TTM-FR-27).

    `days` maps an ISO weekday ("1" = Monday) to either `true` — every
    occurrence of that weekday — or a list of occurrence numbers within the
    calendar month, which is how "Saturdays: 1st and 3rd" is expressed without
    entering twelve dates a term. A missing key means the School does not teach
    that weekday.

    `term_code` NULL is the School's **standing** pattern, inherited by every
    term; a row with a term_code overrides it for that term alone.
    """

    __tablename__ = "school_working_patterns"
    __table_args__ = (
        # Postgres treats NULLs as distinct, so the standing row needs its own
        # partial index to stay unique.
        Index(
            "uq_working_pattern_standing", "school_id",
            unique=True, postgresql_where="term_code IS NULL",
        ),
        Index(
            "uq_working_pattern_term", "school_id", "term_code",
            unique=True, postgresql_where="term_code IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    term_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    days: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SchoolCalendarException(Base):
    """One dated override of a School's week (TTM-FR-27).

    Covers both directions: a date the School does not teach though its pattern
    says it would, and a date it does teach though a holiday or its pattern says
    otherwise — the Nursing ward that does not close for Pongal.

    `follows_day_of_week` is what makes a compensatory day work: "14-11-2026 is
    working, follow Monday" runs Monday's timetable on a Saturday. Without it the
    Sessions for a made-up day could not be generated at all.
    """

    __tablename__ = "school_calendar_exceptions"
    __table_args__ = (
        UniqueConstraint("school_id", "on_date", name="uq_calendar_exception_school_date"),
        CheckConstraint(
            "follows_day_of_week IS NULL OR (working AND follows_day_of_week BETWEEN 1 AND 7)",
            name="ck_exception_follows_only_when_working",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    on_date: Mapped[date] = mapped_column(Date, nullable=False)
    working: Mapped[bool] = mapped_column(Boolean, nullable=False)
    follows_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
