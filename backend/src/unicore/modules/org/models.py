"""ORM tables owned by the org module (aggregated into core.db.Base.metadata)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from unicore.core.db import Base

UNIT_TYPES = ("university", "faculty_division", "school", "department", "program", "section")
CADENCES = ("semester", "yearly")

# Positions per academic year, by cadence. A 4-year semester Programme has an
# 8-rung ladder; a 4-year yearly Programme has 4.
POSITIONS_PER_YEAR: dict[str, int] = {"semester": 2, "yearly": 1}

# Legal parent type for each child type (AUTH-FR-19 hierarchy).
PARENT_TYPE_OF: dict[str, str | None] = {
    "university": None,
    "faculty_division": "university",
    "school": "faculty_division",
    "department": "school",
    "program": "department",
    "section": "program",
}


class Ltree(UserDefinedType[str]):
    """PostgreSQL ltree column (materialized-path hierarchy)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "ltree"


# Shipped defaults for `university_settings`. The migration inserts these
# literally (migrations must not import app code); this is what the running
# system — and the test reset — considers the baseline.
DEFAULT_UNIVERSITY_SETTINGS: dict[str, str] = {
    "class_size_cap": "60",
    "batch_name_template": "{programme_code}-{joining_year}",
    "section_label_template_semester": "{position_roman} Semester - {letter}",
    "section_label_template_yearly": "{position_roman} Year - {letter}",
}


class UniversitySetting(Base):
    """University-wide configuration a Super Admin may change without a deploy:
    the class-size default, the batch-code template, the Section-label templates.

    Deliberately key/value rather than columns — these are naming and threshold
    knobs the university expects to retune, and each one arriving as a migration
    would be friction with no safety benefit.
    """

    __tablename__ = "university_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OrgUnit(Base):
    __tablename__ = "org_units"
    __table_args__ = (
        UniqueConstraint("parent_id", "code", name="uq_org_units_parent_code"),
        Index("ix_org_units_path", "path", postgresql_using="gist"),
        Index("ix_org_units_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(
        Enum(*UNIT_TYPES, name="org_unit_type", create_type=False), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org_units.id"), nullable=True)
    path: Mapped[str] = mapped_column(Ltree(), nullable=False, unique=True)
    campus_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "deactivated", name="org_unit_status", create_type=False),
        nullable=False,
        default="active",
    )
    # Per-term Section instances only (TTM-FR-19); NULL for all other unit types.
    term_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Section instances carry their place in the ladder + division letter, so the
    # display label is rendered from a template rather than parsed back out.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    division_letter: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # Curriculum cadence (AUTH-FR-19): mandatory on a School, an optional override
    # on a Programme. NULL everywhere else — see the placement check constraint.
    cadence: Mapped[str | None] = mapped_column(
        Enum(*CADENCES, name="curriculum_cadence", create_type=False), nullable=True
    )
    # True while a School's cadence is the migration's guess rather than a
    # School Incharge's decision.
    cadence_unconfirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # School-level override of the university-wide class-size cap (TTM-FR-24).
    class_size_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Programme attributes (Program units only) — from the university course catalogue.
    level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    industry_partner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Internship sits outside timetabled Sessions (ATT/PRM care); lateral entrants
    # join mid-programme (ONB cares).
    internship_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lateral_entry_semester: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the importer created this unit to satisfy the hierarchy rather
    # than because the university named it (see AUTH-FR-19 default Departments).
    auto_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


SUBJECT_KINDS = ("core", "elective")
# A student chooses one subject within each group they are offered.
ELECTIVE_GROUPS = ("general", "professional", "open")
VENUE_KINDS = ("classroom", "lab", "seminar", "auditorium", "workshop")


class Subject(Base):
    """A taught subject, owned by the Department that teaches it.

    Deliberately not owned by a Programme: one subject is offered to several
    Programmes (the Maths department's MA101 goes to every B.Tech), and
    duplicating it per Programme would fragment syllabus coverage and the
    question bank along with it. Where it is *taught* is a SubjectOffering.
    """

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("code", name="uq_subjects_code"),
        Index("ix_subjects_department", "department_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum(*SUBJECT_KINDS, name="subject_kind", create_type=False),
        nullable=False,
        default="core",
    )
    # Set on electives only — it is what a student chooses *within*.
    elective_group: Mapped[str | None] = mapped_column(
        Enum(*ELECTIVE_GROUPS, name="elective_group", create_type=False), nullable=True
    )
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    theory_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lab_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        Enum("active", "deactivated", name="org_unit_status", create_type=False),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SubjectOffering(Base):
    """Where a subject is taught.

    Either **Programme-bound** — (subject, Programme, position) — or
    **university-wide**, with both NULL, which is how an Open elective is
    offered: common to the whole university, choosable by any student in any
    term. Uniqueness is two partial indexes rather than one constraint, because
    Postgres treats NULLs as distinct and would admit the same open subject
    twice (see migration 0015)."""

    __tablename__ = "subject_offerings"
    __table_args__ = (Index("ix_offerings_program_position", "program_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subjects.id"), nullable=False)
    # NULL on both = university-wide: an Open elective is common to the whole
    # university, so it has no owning Programme and no ladder position. Either
    # both are set (Programme-bound) or neither is (open to everyone).
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("org_units.id"), nullable=True
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Seats available when students choose this elective (TTM-FR-14). NULL is
    # unlimited — a limit is an explicit decision, never a default.
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "deactivated", name="org_unit_status", create_type=False),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Venue(Base):
    """A physical room. University-level with a campus code, because clash
    detection is university-wide — a School-owned room could not express a
    shared block, and cross-School double-booking would go undetected."""

    __tablename__ = "venues"
    __table_args__ = (UniqueConstraint("code", name="uq_venues_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    campus_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    building: Mapped[str | None] = mapped_column(String(100), nullable=True)
    room: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum(*VENUE_KINDS, name="venue_kind", create_type=False),
        nullable=False,
        default="classroom",
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "deactivated", name="org_unit_status", create_type=False),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
