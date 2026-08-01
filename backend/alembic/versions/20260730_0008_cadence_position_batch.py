"""Curriculum cadence, class-size cap, term parity, student position, batches

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-30

Four related additions locked 30-07-2026:

  * `cadence` on org units — mandatory on a School, optional override on a
    Programme. It decides a Programme's position ladder, so it must exist before
    any position can be validated. Schools seeded before this rule are backfilled
    to `semester` and flagged `cadence_unconfirmed`: a wrong cadence silently
    doubles or halves every position calculation downstream, so the guess is
    visible rather than assumed correct.
  * `class_size_cap` on org units (Schools) over a university-wide default —
    School-configurable like every other threshold, never hardcoded.
  * `parity` on academic terms — odd/even, which half of a semester ladder is
    live in this term.
  * `position` on student profiles and a `batches` table — the admission cohort
    (Programme x joining year). Roll-number uniqueness moves onto the batch,
    which is the pairing the old constraint already used unnamed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIVERSITY_DEFAULT_CLASS_SIZE = 60


def upgrade() -> None:
    cadence = sa.Enum("semester", "yearly", name="curriculum_cadence")
    cadence.create(op.get_bind(), checkfirst=True)
    parity = sa.Enum("odd", "even", name="term_parity")
    parity.create(op.get_bind(), checkfirst=True)

    # --- org: cadence + class-size cap ---------------------------------------
    op.add_column("org_units", sa.Column("cadence", cadence, nullable=True))
    op.add_column(
        "org_units",
        sa.Column(
            "cadence_unconfirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("org_units", sa.Column("class_size_cap", sa.Integer(), nullable=True))
    # Section instances store where they sit in the ladder and which parallel
    # division they are, so the display label renders from a template (TTM-FR-23).
    op.add_column("org_units", sa.Column("position", sa.Integer(), nullable=True))
    op.add_column("org_units", sa.Column("division_letter", sa.String(4), nullable=True))
    op.create_index(
        "ix_org_units_section_position",
        "org_units",
        ["parent_id", "term_code", "position"],
        postgresql_where=sa.text("type = 'section'"),
    )

    # Backfill: existing Schools get `semester`, flagged so a School Incharge
    # confirms rather than inheriting a guess silently.
    op.execute(
        "UPDATE org_units SET cadence = 'semester', cadence_unconfirmed = true "
        "WHERE type = 'school' AND cadence IS NULL"
    )
    # A School must always carry a cadence; Programmes carry one only to override.
    op.create_check_constraint(
        "ck_org_units_school_has_cadence",
        "org_units",
        "type <> 'school' OR cadence IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_org_units_cadence_placement",
        "org_units",
        "cadence IS NULL OR type IN ('school', 'program')",
    )
    op.create_check_constraint(
        "ck_org_units_class_size_cap_positive",
        "org_units",
        "class_size_cap IS NULL OR class_size_cap > 0",
    )

    # --- university-wide settings --------------------------------------------
    op.create_table(
        "university_settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        "INSERT INTO university_settings (key, value) VALUES "
        f"('class_size_cap', '{UNIVERSITY_DEFAULT_CLASS_SIZE}'), "
        "('batch_name_template', '{programme_code}-{joining_year}'), "
        "('section_label_template_semester', '{position_roman} Semester - {letter}'), "
        "('section_label_template_yearly', '{position_roman} Year - {letter}')"
    )

    # --- timetable: term parity ----------------------------------------------
    op.add_column("academic_terms", sa.Column("parity", parity, nullable=True))

    # --- onboarding: batches + position --------------------------------------
    op.create_table(
        "batches",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "program_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org_units.id"),
            nullable=False,
        ),
        sa.Column("joining_year", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("program_id", "joining_year", name="uq_batches_program_year"),
        sa.UniqueConstraint("code", name="uq_batches_code"),
    )

    op.add_column("student_profiles", sa.Column("position", sa.Integer(), nullable=True))
    op.add_column(
        "student_profiles",
        sa.Column(
            "batch_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_student_profiles_batch", "student_profiles", ["batch_id"])
    # Generation counts students by (programme, position); without this the count
    # is a sequential scan of every student in the university per position.
    op.create_index(
        "ix_student_profiles_program_position", "student_profiles", ["program_id", "position"]
    )
    op.execute("UPDATE student_profiles SET position = 1 WHERE position IS NULL")
    op.alter_column("student_profiles", "position", nullable=False)
    op.create_check_constraint(
        "ck_student_profiles_position_positive", "student_profiles", "position >= 1"
    )


def downgrade() -> None:
    op.drop_constraint("ck_student_profiles_position_positive", "student_profiles")
    op.drop_index("ix_student_profiles_program_position", table_name="student_profiles")
    op.drop_index("ix_student_profiles_batch", table_name="student_profiles")
    op.drop_column("student_profiles", "batch_id")
    op.drop_column("student_profiles", "position")
    op.drop_table("batches")
    op.drop_column("academic_terms", "parity")
    op.drop_table("university_settings")
    op.drop_constraint("ck_org_units_class_size_cap_positive", "org_units")
    op.drop_index("ix_org_units_section_position", table_name="org_units")
    op.drop_column("org_units", "division_letter")
    op.drop_column("org_units", "position")
    op.drop_constraint("ck_org_units_cadence_placement", "org_units")
    op.drop_constraint("ck_org_units_school_has_cadence", "org_units")
    op.drop_column("org_units", "class_size_cap")
    op.drop_column("org_units", "cadence_unconfirmed")
    op.drop_column("org_units", "cadence")
    sa.Enum(name="term_parity").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="curriculum_cadence").drop(op.get_bind(), checkfirst=True)
