"""initial schema: org_units (ltree), users, audit_events

Revision ID: 0001
Revises:
Create Date: 2026-07-25

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class Ltree(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "ltree"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute(
        "CREATE TYPE org_unit_type AS ENUM "
        "('university','faculty_division','school','department','program','section')"
    )
    op.execute("CREATE TYPE org_unit_status AS ENUM ('active','deactivated')")
    op.execute("CREATE TYPE user_kind AS ENUM ('student','staff')")
    op.execute(
        "CREATE TYPE user_status AS ENUM ('imported','active','deactivated','withdrawn')"
    )

    op.create_table(
        "org_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "type",
            postgresql.ENUM(name="org_unit_type", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id")),
        sa.Column("path", Ltree(), nullable=False, unique=True),
        sa.Column("campus_code", sa.String(50)),
        sa.Column(
            "status",
            postgresql.ENUM(name="org_unit_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("term_code", sa.String(50)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("parent_id", "code", name="uq_org_units_parent_code"),
    )
    op.create_index("ix_org_units_path", "org_units", ["path"], postgresql_using="gist")
    op.create_index("ix_org_units_parent_id", "org_units", ["parent_id"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("erp_id", sa.String(100)),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("mobile", sa.String(20)),
        sa.Column("kind", postgresql.ENUM(name="user_kind", create_type=False), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="user_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("password_hash", sa.String(300)),
        sa.Column(
            "force_password_change", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_users_erp_id",
        "users",
        ["erp_id"],
        unique=True,
        postgresql_where=sa.text("erp_id IS NOT NULL"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("object_type", sa.String(100), nullable=False),
        sa.Column("object_id", sa.String(100), nullable=False),
        sa.Column("scope", sa.String(300)),
        sa.Column("before", postgresql.JSONB),
        sa.Column("after", postgresql.JSONB),
        sa.Column("reason", sa.Text()),
    )
    op.create_index("ix_audit_events_object", "audit_events", ["object_type", "object_id"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("users")
    op.drop_table("org_units")
    for type_name in ("user_status", "user_kind", "org_unit_status", "org_unit_type"):
        op.execute(f"DROP TYPE {type_name}")
