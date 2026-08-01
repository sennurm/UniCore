"""Subjects, offerings, elective choices, venues

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-31

The three master-data gaps that block TTM. Nothing owned the subject catalogue
— TTM's own doc says it "arrives from ERP or academic setup outside this
module" and no other module claimed it — so a Period had nothing to point at,
and there were no rooms to put it in or faculty to teach it.

A **Subject** is owned by the Department that teaches it (Maths owns MA101),
not by the Programme that consumes it: one subject is offered to several
Programmes, which is how Indian curricula actually work and what keeps syllabus
coverage and question banks from fragmenting per Programme.

An **offering** places a subject at (Programme, position). Elective offerings
carry one of three groups — General, Professional, Open — and a student chooses
exactly one subject per group per term.

**Venues** are physical, so they hang off the University with a campus code
rather than off a School: clash detection is university-wide ("a venue cannot
host two sessions"), which a School-owned room could not express.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # Create the types once, then reference them with create_type=False —
    # otherwise create_table() emits CREATE TYPE a second time and fails.
    for name, values in (
        ("subject_kind", ("core", "elective")),
        ("elective_group", ("general", "professional", "open")),
        ("venue_kind", ("classroom", "lab", "seminar", "auditorium", "workshop")),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    subject_kind = postgresql.ENUM(name="subject_kind", create_type=False)
    elective_group = postgresql.ENUM(name="elective_group", create_type=False)
    venue_kind = postgresql.ENUM(name="venue_kind", create_type=False)
    # Existing since 0001 — reference it, never re-create it.
    unit_status = postgresql.ENUM(name="org_unit_status", create_type=False)

    op.create_table(
        "subjects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        # The Department that owns and teaches it — not the Programme consuming it.
        sa.Column("department_id", UUID, sa.ForeignKey("org_units.id"), nullable=False),
        sa.Column("kind", subject_kind, nullable=False, server_default="core"),
        sa.Column("elective_group", elective_group, nullable=True),
        sa.Column("credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("theory_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lab_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            unit_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("code", name="uq_subjects_code"),
        # A group is what a student chooses *within*, so it is meaningless on a
        # core subject and mandatory on an elective.
        sa.CheckConstraint(
            "(kind = 'elective') = (elective_group IS NOT NULL)",
            name="ck_subjects_elective_group",
        ),
        sa.CheckConstraint("credits >= 0 AND theory_hours >= 0 AND lab_hours >= 0",
                           name="ck_subjects_non_negative"),
    )
    op.create_index("ix_subjects_department", "subjects", ["department_id"])

    op.create_table(
        "subject_offerings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("subject_id", UUID, sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("program_id", UUID, sa.ForeignKey("org_units.id"), nullable=False),
        # Semester n for semester cadence, year n for yearly — the same ladder
        # position a student carries (ONB-FR-20).
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            unit_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "subject_id", "program_id", "position", name="uq_offering_subject_program_position"
        ),
        sa.CheckConstraint("position >= 1", name="ck_offering_position_positive"),
    )
    op.create_index(
        "ix_offerings_program_position", "subject_offerings", ["program_id", "position"]
    )

    op.create_table(
        "student_elective_choices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("offering_id", UUID, sa.ForeignKey("subject_offerings.id"), nullable=False),
        sa.Column("term_code", sa.String(50), nullable=False),
        # Denormalised from the subject so "one choice per group per term" can be
        # a database constraint rather than a hopeful service check.
        sa.Column("elective_group", elective_group, nullable=False),
        sa.Column(
            "chosen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "term_code", "elective_group", name="uq_elective_choice_per_group"
        ),
    )
    op.create_index(
        "ix_elective_choices_offering", "student_elective_choices", ["offering_id"]
    )

    op.create_table(
        "venues",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("campus_code", sa.String(50), nullable=True),
        sa.Column("building", sa.String(100), nullable=True),
        sa.Column("room", sa.String(50), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("kind", venue_kind, nullable=False, server_default="classroom"),
        sa.Column(
            "status",
            unit_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("code", name="uq_venues_code"),
        sa.CheckConstraint("capacity > 0", name="ck_venues_capacity_positive"),
    )

    # Staff counterpart of student_profiles: the employee id an ERP/HR feed
    # matches on, and the designation that decides which role they hold.
    op.create_table(
        "staff_profiles",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("employee_id", sa.String(50), nullable=False),
        sa.Column("department_id", UUID, sa.ForeignKey("org_units.id"), nullable=True),
        sa.Column("designation", sa.String(60), nullable=False),
        sa.Column("date_of_joining", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("employee_id", name="uq_staff_employee_id"),
    )


def downgrade() -> None:
    op.drop_table("staff_profiles")
    op.drop_table("venues")
    op.drop_table("student_elective_choices")
    op.drop_table("subject_offerings")
    op.drop_index("ix_subjects_department", table_name="subjects")
    op.drop_table("subjects")
    for name in ("venue_kind", "elective_group", "subject_kind"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
