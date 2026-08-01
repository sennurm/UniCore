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
