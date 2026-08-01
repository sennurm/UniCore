"""Rename import batches to import runs

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-31

"Batch" is reserved for the admission cohort (00-overview.md §3), and since
0008 there is a real `batches` table holding them. One execution of a CSV
import is an **import run**. Leaving both meanings in the schema is how a query
joins the wrong table.

`import_batches.created_batches` keeps its name — it genuinely lists admission
cohorts the run created.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE import_batch_status RENAME TO import_run_status")
    op.rename_table("import_batches", "import_runs")
    op.alter_column("import_row_errors", "batch_id", new_column_name="run_id")
    op.execute("ALTER INDEX ix_import_row_errors_batch RENAME TO ix_import_row_errors_run")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_import_row_errors_run RENAME TO ix_import_row_errors_batch")
    op.alter_column("import_row_errors", "run_id", new_column_name="batch_id")
    op.rename_table("import_runs", "import_batches")
    op.execute("ALTER TYPE import_run_status RENAME TO import_batch_status")
