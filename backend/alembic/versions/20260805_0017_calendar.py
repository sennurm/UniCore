"""University holiday calendar and per-School working days

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-05

TTM-FR-01 put a combined holiday/working-day calendar at *campus* scope. That
cannot describe this university: a School of Nursing runs clinical postings on
Saturday and Sunday while the Engineering School on the same campus is closed,
and one campus calendar has to be wrong for one of them.

So it splits in two (locked 05-08-2026). Holidays are university-owned date
ranges — a vacation block is one row, not fourteen — optionally tagged to the
campuses that observe them. Working days are a **School** attribute: seven
weekday flags plus nth-weekday rules, so "Saturdays: 1st and 3rd" needs no dated
rows at all. Dated exceptions cover the rest, including the compensatory day
that follows another weekday's timetable.

No backfill: nothing recorded working days before this, and a School with no
pattern falls back to Monday–Saturday in the resolver rather than to an empty
week. Purely additive — no existing column or table changes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    holiday_kind = postgresql.ENUM(
        "public", "vacation", "local", name="holiday_kind", create_type=False
    )
    holiday_status = postgresql.ENUM(
        "active", "withdrawn", name="holiday_status", create_type=False
    )
    holiday_kind.create(op.get_bind(), checkfirst=True)
    holiday_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "university_holidays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("kind", holiday_kind, nullable=False),
        # Empty means university-wide. A regional festival names its campuses.
        sa.Column(
            "campus_codes",
            postgresql.ARRAY(sa.String(50)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", holiday_status, nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("to_date >= from_date", name="ck_holiday_range_ordered"),
    )
    op.create_index("ix_holidays_dates", "university_holidays", ["from_date", "to_date"])

    op.create_table(
        "school_working_patterns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id"),
            nullable=False,
        ),
        # NULL is the School's standing pattern; a term_code overrides it for
        # that term alone.
        sa.Column("term_code", sa.String(50), nullable=True),
        sa.Column("days", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Postgres treats NULLs as distinct, so the standing row needs its own
    # partial index to be unique at all.
    op.create_index(
        "uq_working_pattern_standing", "school_working_patterns", ["school_id"],
        unique=True, postgresql_where=sa.text("term_code IS NULL"),
    )
    op.create_index(
        "uq_working_pattern_term", "school_working_patterns", ["school_id", "term_code"],
        unique=True, postgresql_where=sa.text("term_code IS NOT NULL"),
    )

    op.create_table(
        "school_calendar_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id"),
            nullable=False,
        ),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("working", sa.Boolean(), nullable=False),
        # Only meaningful on a working exception: "this Saturday runs Monday".
        sa.Column("follows_day_of_week", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("school_id", "on_date", name="uq_calendar_exception_school_date"),
        sa.CheckConstraint(
            "follows_day_of_week IS NULL OR (working AND follows_day_of_week BETWEEN 1 AND 7)",
            name="ck_exception_follows_only_when_working",
        ),
    )


def downgrade() -> None:
    op.drop_table("school_calendar_exceptions")
    op.drop_index("uq_working_pattern_term", table_name="school_working_patterns")
    op.drop_index("uq_working_pattern_standing", table_name="school_working_patterns")
    op.drop_table("school_working_patterns")
    op.drop_index("ix_holidays_dates", table_name="university_holidays")
    op.drop_table("university_holidays")
    op.execute("DROP TYPE IF EXISTS holiday_status")
    op.execute("DROP TYPE IF EXISTS holiday_kind")
