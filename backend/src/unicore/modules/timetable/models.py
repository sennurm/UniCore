"""ORM tables owned by the timetable module (aggregated into core.db.Base.metadata).

Milestone-2 slice only: the per-School academic term (TTM-FR-18). Period grids,
drafts, and published timetables arrive with the full TTM milestone.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
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
