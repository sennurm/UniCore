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
