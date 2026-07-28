"""onboarding: academic terms, import batches, student profiles, section memberships

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TYPE academic_term_status AS ENUM ('draft','approved','superseded')")
    op.execute(
        "CREATE TYPE import_batch_status AS ENUM "
        "('processing','committed','needs-review','rejected')"
    )
    op.execute("CREATE TYPE credential_delivery_status AS ENUM ('pending','delivered','failed')")

    # office-staff role (ONB): School-scoped, designated by the School Incharge.
    op.execute(
        "INSERT INTO roles (code, name, unit_type, singleton, term_bound, pa_tier) "
        "VALUES ('office-staff', 'Admin / Office Staff', 'school', false, false, 'tier2')"
    )

    op.create_table(
        "academic_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id"),
            nullable=False,
        ),
        sa.Column("term_code", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("exam_ranges", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("special_events", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("archival_backstop_date", sa.Date()),
        sa.Column(
            "status",
            postgresql.ENUM(name="academic_term_status", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("uploaded_by", sa.String(100), nullable=False),
        sa.Column("approved_by", sa.String(100)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "school_id", "term_code", "version", name="uq_terms_school_code_version"
        ),
    )

    op.create_table(
        "import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("term_code", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.String(10), nullable=False, server_default="v1"),
        sa.Column(
            "status",
            postgresql.ENUM(name="import_batch_status", create_type=False),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "import_row_errors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("import_batches.id"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(60), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_row", sa.Text(), nullable=False),
    )
    op.create_index("ix_import_row_errors_batch", "import_row_errors", ["batch_id"])

    op.create_table(
        "student_profiles",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True
        ),
        sa.Column(
            "program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id"),
            nullable=False,
        ),
        sa.Column("roll_number", sa.String(50), nullable=False),
        sa.Column("admission_year", sa.Integer(), nullable=False),
        sa.Column("date_of_birth", sa.Date()),
        sa.Column("gender", sa.String(20)),
        sa.Column(
            "credential_delivery",
            postgresql.ENUM(name="credential_delivery_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("delivery_channel", sa.String(10)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "program_id", "admission_year", "roll_number", name="uq_student_roll_program_year"
        ),
    )

    op.create_table(
        "section_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "section_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_section_memberships_student", "section_memberships", ["user_id"])
    op.create_index("ix_section_memberships_section", "section_memberships", ["section_id"])
    op.create_index(
        "uq_section_membership_open",
        "section_memberships",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    for table in (
        "section_memberships",
        "student_profiles",
        "import_row_errors",
        "import_batches",
        "academic_terms",
    ):
        op.drop_table(table)
    op.execute("DELETE FROM roles WHERE code = 'office-staff'")
    for type_name in (
        "credential_delivery_status",
        "import_batch_status",
        "academic_term_status",
    ):
        op.execute(f"DROP TYPE {type_name}")
