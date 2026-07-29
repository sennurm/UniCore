"""ORM tables owned by the rbac module (aggregated into core.db.Base.metadata)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from unicore.core.db import Base

# Singleton-per-unit leadership roles (AUTH-FR-16). Mirrored in the partial
# unique indexes of migration 0002 — change both together.
UNIT_SINGLETON_ROLES = ("hod", "school-incharge", "faculty-dean", "class-incharge")
# `pro-chancellor` is deliberately absent: the university's leadership page
# lists TWO holders, so multiple active grants are the documented reality
# (locked 28-07-2026 — see docs/reviews/org-structure-vs-university-document.md).
UNIVERSITY_SINGLETON_ROLES = (
    "chancellor",
    "vc",
    "registrar",
    "dean-academic-affairs",
    "controller-of-examination",
)

REVOKE_CAUSES = ("manual", "superseded", "term-closure")


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Org-unit type a grant of this role binds to; university-scope roles are
    # granted with org_unit_id NULL by convention (service-enforced).
    unit_type: Mapped[str] = mapped_column(
        Enum(
            "university",
            "faculty_division",
            "school",
            "department",
            "program",
            "section",
            name="org_unit_type",
            create_type=False,
        ),
        nullable=False,
    )
    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    term_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pa_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 'tier1' | 'tier2'


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (
        Index("ix_grants_user_active", "user_id", postgresql_where="status = 'active'"),
        Index("ix_grants_role_unit", "role_code", "org_unit_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code"), nullable=False)
    org_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("org_units.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "revoked", name="grant_status", create_type=False),
        nullable=False,
        default="active",
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    term_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    additional_charge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    granted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    revoke_cause: Mapped[str | None] = mapped_column(
        Enum(*REVOKE_CAUSES, name="grant_revoke_cause", create_type=False), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReportingEdge(Base):
    """AUTH-FR-18: role-level reporting chain (acyclic; Chancellor terminal —
    the terminal role simply has no outgoing edge)."""

    __tablename__ = "reporting_edges"

    from_role: Mapped[str] = mapped_column(ForeignKey("roles.code"), primary_key=True)
    to_role: Mapped[str] = mapped_column(ForeignKey("roles.code"), nullable=False)
