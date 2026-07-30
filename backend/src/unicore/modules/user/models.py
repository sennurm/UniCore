"""ORM tables owned by the user module (aggregated into core.db.Base.metadata)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from unicore.core.db import Base

USER_KINDS = ("student", "staff")
# Locked lifecycle (ONB/AUTH): IMPORTED -> ACTIVE -> DEACTIVATED | WITHDRAWN.
USER_STATUSES = ("imported", "active", "deactivated", "withdrawn")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # SIF id is the identity join key — issued when admission completes, so it
        # exists from day one. Unique when present (staff have none).
        Index(
            "uq_users_sif_id",
            "sif_id",
            unique=True,
            postgresql_where=text("sif_id IS NOT NULL"),
        ),
        # Enrollment id arrives later; university-wide unique once issued.
        Index(
            "uq_users_enrollment_id",
            "enrollment_id",
            unique=True,
            postgresql_where=text("enrollment_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    sif_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enrollment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    kind: Mapped[str] = mapped_column(
        Enum(*USER_KINDS, name="user_kind", create_type=False), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(*USER_STATUSES, name="user_status", create_type=False),
        nullable=False,
        default="active",
    )
    # Credential fields are populated by the auth module in Phase 3.
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    force_password_change: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


GRIEVANCE_KINDS = ("correction", "erasure")
GRIEVANCE_STATUSES = ("open", "resolved")

STATUTORY_EXEMPTION_NOTE = (
    "Academic records are retained under statutory retention mandates and are "
    "exempt from erasure while the retention period applies (DPDP grievance "
    "response per AUTH \u00a75). The request and this response are logged."
)


class Grievance(Base):
    """DPDP correction/erasure grievances (AUTH-FR-10)."""

    __tablename__ = "grievances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum(*GRIEVANCE_KINDS, name="grievance_kind", create_type=False), nullable=False
    )
    details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*GRIEVANCE_STATUSES, name="grievance_status", create_type=False),
        nullable=False,
        default="open",
    )
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
