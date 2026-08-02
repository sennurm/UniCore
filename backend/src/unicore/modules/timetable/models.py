"""ORM tables owned by the timetable module (aggregated into core.db.Base.metadata).

Milestone-2 slice only: the per-School academic term (TTM-FR-18). Period grids,
drafts, and published timetables arrive with the full TTM milestone.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
