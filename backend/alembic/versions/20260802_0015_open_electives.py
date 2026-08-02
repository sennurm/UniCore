"""University-wide Open electives

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-02

An Open elective is common to the whole university (locked 02-08-2026), so it
cannot be a per-Programme offering: expressing "open to everyone" as one row per
Programme would mean 113 rows that immediately drift apart.

A university-wide offering carries `program_id IS NULL` and `position IS NULL` —
any student, any term. General and Professional electives stay Programme-bound,
because those are discipline-specific by definition.

Postgres treats NULLs as distinct in a UNIQUE constraint, so the existing
(subject, programme, position) key would happily admit the same open subject
twice. Two partial indexes replace it: one for Programme-bound offerings, one
for university-wide ones.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("subject_offerings", "program_id", nullable=True)
    op.alter_column("subject_offerings", "position", nullable=True)

    op.drop_constraint("uq_offering_subject_program_position", "subject_offerings")
    op.create_index(
        "uq_offering_programme_bound",
        "subject_offerings",
        ["subject_id", "program_id", "position"],
        unique=True,
        postgresql_where=sa.text("program_id IS NOT NULL"),
    )
    op.create_index(
        "uq_offering_university_wide",
        "subject_offerings",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text("program_id IS NULL"),
    )
    # Either fully Programme-bound or fully university-wide; never half of each,
    # which would leave "which students may take this" undecidable.
    op.create_check_constraint(
        "ck_offering_scope_coherent",
        "subject_offerings",
        "(program_id IS NULL) = (position IS NULL)",
    )
    op.drop_constraint("ck_offering_position_positive", "subject_offerings")
    op.create_check_constraint(
        "ck_offering_position_positive",
        "subject_offerings",
        "position IS NULL OR position >= 1",
    )


def downgrade() -> None:
    op.execute("DELETE FROM subject_offerings WHERE program_id IS NULL")
    op.drop_constraint("ck_offering_position_positive", "subject_offerings")
    op.create_check_constraint(
        "ck_offering_position_positive", "subject_offerings", "position >= 1"
    )
    op.drop_constraint("ck_offering_scope_coherent", "subject_offerings")
    op.drop_index("uq_offering_university_wide", table_name="subject_offerings")
    op.drop_index("uq_offering_programme_bound", table_name="subject_offerings")
    op.create_unique_constraint(
        "uq_offering_subject_program_position",
        "subject_offerings",
        ["subject_id", "program_id", "position"],
    )
    op.alter_column("subject_offerings", "position", nullable=False)
    op.alter_column("subject_offerings", "program_id", nullable=False)
