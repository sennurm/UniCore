"""Elective offering capacity

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-31

Student elective self-selection shipped in 0012 without the capacity limit
TTM-FR-14 has always specified, so nothing stopped every student picking the
same elective. Capacity lives on the **offering** — "Machine Learning for
BT-CSE at semester 5 seats 60" — because the same subject offered to another
Programme is a different room-load entirely.

NULL means unlimited, which is what the offerings created before this migration
carry: adding a limit is an explicit act, not something a migration guesses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subject_offerings", sa.Column("capacity", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_offering_capacity_positive",
        "subject_offerings",
        "capacity IS NULL OR capacity > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_offering_capacity_positive", "subject_offerings")
    op.drop_column("subject_offerings", "capacity")
