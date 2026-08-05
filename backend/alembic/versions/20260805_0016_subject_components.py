"""School-configurable subject components (theory, lab, field work, clinical)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-05

Subjects carried exactly two hour columns, theory and lab, which cannot express
what Medicine and Nursing actually teach: clinical postings and community field
work. A Nursing subject is routinely 2 theory + 4 clinical *at once*, so one
type per subject would not have helped either — hours belong per component.

Components are defined **university-wide** and each School enables the ones it
teaches (locked 05-08-2026): Nursing sees clinical, Engineering does not, while
credit and workload reporting stay comparable across Schools. A School with no
explicit selection gets the university defaults rather than an empty form.

`kind` (core | elective) is deliberately untouched. It means "does the student
choose this" — it drives elective groups, student selection and seat capacity —
so it is a different question from how a subject is taught.

theory_hours/lab_hours are migrated into rows and dropped: keeping them beside
the new table would be two sources of truth for the same number.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

# (code, name, sequence, on_by_default) — the shipped catalogue. "Default" means
# a School that has never chosen sees these, which keeps existing Schools working.
SEED = [
    ("theory", "Theory", 1, True),
    ("lab", "Laboratory / Practical", 2, True),
    ("field_work", "Field work", 3, False),
    ("clinical", "Clinical posting", 4, False),
    ("project", "Project / Dissertation", 5, False),
]


def upgrade() -> None:
    # Off-campus teaching sites are venues like any other, so clash detection,
    # capacity and the attendance record all keep working for field work.
    op.execute("ALTER TYPE venue_kind ADD VALUE IF NOT EXISTS 'field'")

    op.create_table(
        "subject_components",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="99"),
        sa.Column(
            "default_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="org_unit_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.UniqueConstraint("code", name="uq_subject_component_code"),
    )

    op.create_table(
        "school_subject_components",
        sa.Column("school_id", UUID, sa.ForeignKey("org_units.id"), primary_key=True),
        sa.Column(
            "component_id", UUID, sa.ForeignKey("subject_components.id"), primary_key=True
        ),
    )

    op.create_table(
        "subject_component_hours",
        sa.Column(
            "subject_id",
            UUID,
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "component_id", UUID, sa.ForeignKey("subject_components.id"), primary_key=True
        ),
        sa.Column("hours", sa.Integer(), nullable=False),
        sa.CheckConstraint("hours > 0", name="ck_component_hours_positive"),
    )

    for code, name, sequence, default_enabled in SEED:
        op.execute(
            sa.text(
                "INSERT INTO subject_components "
                "(id, code, name, sequence, default_enabled) "
                "VALUES (gen_random_uuid(), :code, :name, :seq, :dflt) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name, seq=sequence, dflt=default_enabled)
        )

    # Carry existing hours across before the columns go, so nothing is lost.
    for column, code in (("theory_hours", "theory"), ("lab_hours", "lab")):
        op.execute(
            sa.text(
                "INSERT INTO subject_component_hours (subject_id, component_id, hours) "
                f"SELECT s.id, c.id, s.{column} FROM subjects s "
                "CROSS JOIN subject_components c "
                f"WHERE c.code = :code AND s.{column} > 0"
            ).bindparams(code=code)
        )

    op.drop_constraint("ck_subjects_non_negative", "subjects")
    op.drop_column("subjects", "theory_hours")
    op.drop_column("subjects", "lab_hours")
    op.create_check_constraint("ck_subjects_credits_non_negative", "subjects", "credits >= 0")


def downgrade() -> None:
    op.add_column(
        "subjects", sa.Column("theory_hours", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "subjects", sa.Column("lab_hours", sa.Integer(), nullable=False, server_default="0")
    )
    for column, code in (("theory_hours", "theory"), ("lab_hours", "lab")):
        op.execute(
            sa.text(
                f"UPDATE subjects s SET {column} = h.hours "
                "FROM subject_component_hours h JOIN subject_components c ON c.id = h.component_id "
                "WHERE h.subject_id = s.id AND c.code = :code"
            ).bindparams(code=code)
        )
    op.drop_constraint("ck_subjects_credits_non_negative", "subjects")
    op.create_check_constraint(
        "ck_subjects_non_negative",
        "subjects",
        "credits >= 0 AND theory_hours >= 0 AND lab_hours >= 0",
    )
    op.drop_table("subject_component_hours")
    op.drop_table("school_subject_components")
    op.drop_table("subject_components")
    # The 'field' venue kind stays: removing an enum value would need the type
    # rebuilding, and any venue already using it would have nowhere to go.
