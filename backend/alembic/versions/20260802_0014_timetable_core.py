"""Period grids, timetable drafts, entries and per-Department approvals

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-02

The TTM spine (TTM-FR-02/03/04/08/09/12). Decisions locked 02-08-2026:

* A **period grid** belongs to a School and is versioned. Periods carry real
  clock times, not just an index — that is what lets clash detection span two
  Schools running different grids (TTM-FR-04 says "by absolute time overlap, not
  by Period index"). A grid change mid-term needs a republish, so grids are
  never edited in place once a timetable references them.
* A **draft** covers one School for one term and publishes atomically. Entries
  belong to Sections, but each HoD approves the portion touching their
  Department, and publish unblocks only when every touched Department has
  signed off. A timetable is only consistent as a whole — clash-free per
  Section says nothing about the pair.
* An **entry** is weekly recurring: day-of-week x Period. ATT expands it
  against the School calendar to produce dated Sessions, which keeps a term to
  a few hundred rows rather than tens of thousands.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    for name, values in (
        ("period_grid_status", ("draft", "active", "superseded")),
        (
            "timetable_draft_status",
            ("draft", "published", "superseded", "archived"),
        ),
        ("timetable_approval_status", ("pending", "approved", "rejected")),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    grid_status = postgresql.ENUM(name="period_grid_status", create_type=False)
    draft_status = postgresql.ENUM(name="timetable_draft_status", create_type=False)
    approval_status = postgresql.ENUM(name="timetable_approval_status", create_type=False)

    op.create_table(
        "period_grids",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("school_id", UUID, sa.ForeignKey("org_units.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", grid_status, nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("school_id", "version", name="uq_period_grid_school_version"),
    )

    op.create_table(
        "periods",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("grid_id", UUID, sa.ForeignKey("period_grids.id"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        # Real clock times: clash detection compares these, so two Schools with
        # different grids still collide correctly on a shared room.
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.UniqueConstraint("grid_id", "sequence", name="uq_period_grid_sequence"),
        sa.CheckConstraint("end_time > start_time", name="ck_period_times_ordered"),
    )
    op.create_index("ix_periods_grid", "periods", ["grid_id"])

    op.create_table(
        "timetable_drafts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("school_id", UUID, sa.ForeignKey("org_units.id"), nullable=False),
        sa.Column("term_code", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("grid_id", UUID, sa.ForeignKey("period_grids.id"), nullable=False),
        sa.Column("status", draft_status, nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("published_by", sa.String(100), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "school_id", "term_code", "version", name="uq_draft_school_term_version"
        ),
    )

    op.create_table(
        "timetable_entries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("draft_id", UUID, sa.ForeignKey("timetable_drafts.id"), nullable=False),
        sa.Column("section_id", UUID, sa.ForeignKey("org_units.id"), nullable=False),
        # ISO day-of-week: Monday = 1.
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("period_id", UUID, sa.ForeignKey("periods.id"), nullable=False),
        sa.Column("offering_id", UUID, sa.ForeignKey("subject_offerings.id"), nullable=False),
        sa.Column("faculty_user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("venue_id", UUID, sa.ForeignKey("venues.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # A Section cannot be in two places in its own timetable.
        sa.UniqueConstraint(
            "draft_id", "section_id", "day_of_week", "period_id", name="uq_entry_section_slot"
        ),
        sa.CheckConstraint("day_of_week BETWEEN 1 AND 7", name="ck_entry_day_of_week"),
    )
    op.create_index("ix_entries_draft", "timetable_entries", ["draft_id"])
    op.create_index("ix_entries_faculty", "timetable_entries", ["faculty_user_id"])
    op.create_index("ix_entries_venue", "timetable_entries", ["venue_id"])
    op.create_index("ix_entries_section", "timetable_entries", ["section_id"])

    op.create_table(
        "timetable_approvals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("draft_id", UUID, sa.ForeignKey("timetable_drafts.id"), nullable=False),
        sa.Column("department_id", UUID, sa.ForeignKey("org_units.id"), nullable=False),
        sa.Column("status", approval_status, nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("draft_id", "department_id", name="uq_approval_draft_department"),
    )


def downgrade() -> None:
    op.drop_table("timetable_approvals")
    op.drop_table("timetable_entries")
    op.drop_table("timetable_drafts")
    op.drop_table("periods")
    op.drop_table("period_grids")
    for name in (
        "timetable_approval_status",
        "timetable_draft_status",
        "period_grid_status",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
