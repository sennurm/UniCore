"""programme category/partner/internship, auto-created marker, Pro-Chancellor

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-28

Reconciles the model with the university's published structure document:
Pro-Chancellor exists (and is NOT a singleton — two holders are documented),
Departments may be auto-created for Schools that have none, and Programmes carry
category / industry partner / internship / lateral-entry data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("org_units", sa.Column("category", sa.String(40)))
    op.add_column("org_units", sa.Column("industry_partner", sa.String(120)))
    op.add_column("org_units", sa.Column("internship_months", sa.Integer()))
    op.add_column("org_units", sa.Column("lateral_entry_semester", sa.Integer()))
    op.add_column(
        "org_units",
        sa.Column("auto_created", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Pro-Chancellor restored (removed 25-07-2026 from the access matrix, but the
    # university's leadership page lists two holders). Deliberately NOT added to
    # the singleton partial index: multiple holders are the documented reality.
    op.execute(
        "INSERT INTO roles (code, name, unit_type, singleton, term_bound, pa_tier) "
        "VALUES ('pro-chancellor', 'Pro-Chancellor', 'university', false, false, 'tier1') "
        "ON CONFLICT (code) DO NOTHING"
    )
    # Leave chain: VC / Registrar -> Pro-Chancellor -> Chancellor (terminal).
    op.execute("DELETE FROM reporting_edges WHERE from_role IN ('vc', 'registrar')")
    op.execute(
        "INSERT INTO reporting_edges (from_role, to_role) VALUES "
        "('vc', 'pro-chancellor'), "
        "('registrar', 'pro-chancellor'), "
        "('pro-chancellor', 'chancellor') "
        "ON CONFLICT (from_role) DO UPDATE SET to_role = EXCLUDED.to_role"
    )


def downgrade() -> None:
    op.execute("DELETE FROM reporting_edges WHERE from_role = 'pro-chancellor'")
    op.execute(
        "UPDATE reporting_edges SET to_role = 'chancellor' WHERE from_role IN ('vc','registrar')"
    )
    op.execute("DELETE FROM roles WHERE code = 'pro-chancellor'")
    for column in (
        "auto_created",
        "lateral_entry_semester",
        "internship_months",
        "industry_partner",
        "category",
    ):
        op.drop_column("org_units", column)
