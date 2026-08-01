"""ORM tables owned by the onboarding module (aggregated into core.db.Base.metadata)."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from unicore.core.db import Base

BATCH_STATUSES = ("processing", "committed", "needs-review", "rejected")
DELIVERY_STATUSES = ("pending", "delivered", "failed")

# ONB §8 guardrail: a batch changing org mapping / DOB for more than this share of
# its rows pauses for System Admin confirmation instead of committing silently.
RISKY_CHANGE_THRESHOLD = 0.20


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    term_code: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(10), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(
        Enum(*BATCH_STATUSES, name="import_batch_status", create_type=False),
        nullable=False,
        default="processing",
    )
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    # Admission cohorts this run brought into existence (ONB §8). A typo'd
    # admission_year creates a real Batch, so the run must name what it created.
    created_batches: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImportRowError(Base):
    """Powers the downloadable error report (ONB-FR-03): row number, field, reason, raw row."""

    __tablename__ = "import_row_errors"
    __table_args__ = (Index("ix_import_row_errors_batch", "batch_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_row: Mapped[str] = mapped_column(Text, nullable=False)


class Batch(Base):
    """The admission cohort — (Programme x joining year), e.g. BT-CSE-2026.

    "Batch" is reserved for this meaning alone (00-overview.md §3). Auto-created
    on first import (ONB-FR-19); the code comes from a configurable university
    template, so it is stored rather than derived — changing the template must
    never rename cohorts already issued.
    """

    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("program_id", "joining_year", name="uq_batches_program_year"),
        UniqueConstraint("code", name="uq_batches_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    joining_year: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StudentProfile(Base):
    """Student-specific fields hanging off `users` (which owns identity + credentials)."""

    __tablename__ = "student_profiles"
    __table_args__ = (
        # ONB-FR-08: roll numbers unique within a Batch — the (Program, joining
        # year) pairing this constraint already used before the batch was named.
        UniqueConstraint(
            "program_id", "admission_year", "roll_number", name="uq_student_roll_program_year"
        ),
        Index("ix_student_profiles_batch", "batch_id"),
        Index("ix_student_profiles_program_position", "program_id", "position"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    roll_number: Mapped[str] = mapped_column(String(50), nullable=False)
    admission_year: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    # Where the student sits in the ladder: semester n for semester cadence, year
    # n for yearly. One number is authoritative; the other is derived on read so
    # they can never contradict each other (ONB-FR-20).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    credential_delivery: Mapped[str] = mapped_column(
        Enum(*DELIVERY_STATUSES, name="credential_delivery_status", create_type=False),
        nullable=False,
        default="pending",
    )
    delivery_channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SectionMembership(Base):
    """Dated Section membership; history is immutable so past attendance stays attached
    to the Section it was captured in (ONB-FR-10)."""

    __tablename__ = "section_memberships"
    __table_args__ = (
        Index("ix_section_memberships_student", "user_id"),
        Index("ix_section_memberships_section", "section_id"),
        Index(
            "uq_section_membership_open",
            "user_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
