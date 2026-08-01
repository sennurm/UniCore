"""Record which Batches an import run created

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-31

ONB §8: a typo'd admission_year silently creates a real admission cohort, so the
run summary must name the Batches it brought into existence — "so an unexpected
one is visible immediately". That has to survive the request, because the run
dashboard shows past runs too, hence a column rather than a response-only field.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_batches",
        sa.Column(
            "created_batches",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("import_batches", "created_batches")
