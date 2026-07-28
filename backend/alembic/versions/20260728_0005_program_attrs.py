"""programme attributes on org units (level, duration, mode)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Programme attributes from the university course catalogue; Program units only.
    op.add_column("org_units", sa.Column("level", sa.String(40)))
    op.add_column("org_units", sa.Column("duration_years", sa.Integer()))
    op.add_column("org_units", sa.Column("mode", sa.String(20)))


def downgrade() -> None:
    for column in ("mode", "duration_years", "level"):
        op.drop_column("org_units", column)
