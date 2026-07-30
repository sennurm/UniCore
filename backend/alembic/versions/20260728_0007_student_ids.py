"""SIF id (renamed from erp_id) + enrollment id

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-28

Students carry two one-to-one identifiers with different lifecycles:
  * SIF id      — issued when admission completes; present from day one, so it
                  is the import join key (previously modelled as `erp_id`).
  * Enrollment id — issued weeks/months later; university-wide unique, optional
                  until issued, correctable afterwards with audit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_users_erp_id", table_name="users")
    op.alter_column("users", "erp_id", new_column_name="sif_id")
    op.create_index(
        "uq_users_sif_id",
        "users",
        ["sif_id"],
        unique=True,
        postgresql_where=sa.text("sif_id IS NOT NULL"),
    )

    op.add_column("users", sa.Column("enrollment_id", sa.String(100)))
    # University-wide unique when present; NULL until the number is issued.
    op.create_index(
        "uq_users_enrollment_id",
        "users",
        ["enrollment_id"],
        unique=True,
        postgresql_where=sa.text("enrollment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_enrollment_id", table_name="users")
    op.drop_column("users", "enrollment_id")
    op.drop_index("uq_users_sif_id", table_name="users")
    op.alter_column("users", "sif_id", new_column_name="erp_id")
    op.create_index(
        "uq_users_erp_id",
        "users",
        ["erp_id"],
        unique=True,
        postgresql_where=sa.text("erp_id IS NOT NULL"),
    )
