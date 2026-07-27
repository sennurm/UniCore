"""auth sessions/OTP/devices/consent, grievances, outbox, reporting chain

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# AUTH-FR-18 chain (25-07-2026): Chancellor terminal (no outgoing edge).
CHAIN = [
    ("class-incharge", "hod"),
    ("hod", "school-incharge"),
    ("school-incharge", "faculty-dean"),
    ("faculty-dean", "dean-academic-affairs"),
    ("dean-academic-affairs", "vc"),
    ("vc", "chancellor"),
    ("registrar", "chancellor"),
    ("controller-of-examination", "registrar"),
]


def upgrade() -> None:
    op.execute("CREATE TYPE otp_purpose AS ENUM ('login','password-reset','device-change')")
    op.execute("CREATE TYPE device_status AS ENUM ('active','invalidated')")
    op.execute("CREATE TYPE device_request_status AS ENUM ('pending','approved','rejected')")
    op.execute("CREATE TYPE grievance_kind AS ENUM ('correction','erasure')")
    op.execute("CREATE TYPE grievance_status AS ENUM ('open','resolved')")

    op.create_table(
        "otp_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "purpose", postgresql.ENUM(name="otp_purpose", create_type=False), nullable=False
        ),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="device_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_devices_one_active",
        "devices",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "device_change_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("new_fingerprint", sa.String(200), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="device_request_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(100)),
    )
    op.create_index(
        "uq_device_requests_one_pending",
        "device_change_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("notice_version", sa.String(20), nullable=False),
        sa.Column(
            "geolocation_consent", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "grievances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "kind", postgresql.ENUM(name="grievance_kind", create_type=False), nullable=False
        ),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="grievance_status", create_type=False),
            nullable=False,
            server_default="open",
        ),
        sa.Column("response", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "domain_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_domain_events_pending",
        "domain_events",
        ["created_at"],
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )

    edges = op.create_table(
        "reporting_edges",
        sa.Column("from_role", sa.String(50), sa.ForeignKey("roles.code"), primary_key=True),
        sa.Column("to_role", sa.String(50), sa.ForeignKey("roles.code"), nullable=False),
    )
    op.bulk_insert(edges, [{"from_role": f, "to_role": t} for f, t in CHAIN])


def downgrade() -> None:
    for table in (
        "reporting_edges",
        "domain_events",
        "grievances",
        "consent_records",
        "device_change_requests",
        "devices",
        "otp_challenges",
    ):
        op.drop_table(table)
    for type_name in (
        "grievance_status",
        "grievance_kind",
        "device_request_status",
        "device_status",
        "otp_purpose",
    ):
        op.execute(f"DROP TYPE {type_name}")
