"""Seed the Student role

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-31

Students were provisioned as users but held no role at all, so every permission
check failed for them: they could sign in and do nothing. AUTH §1 puts students
inside the RBAC model ("every user … authorization is role-based"), and §8
reasons about a Faculty Member "also pursuing a program as Student" — a boundary
that cannot exist without the role.

Scope is the **Programme**, not the Section (locked 31-07-2026). A Section is a
per-term instance, so a Section-scoped student grant would need revoking and
reissuing for every student at every term closure — ~15,000 writes twice a year,
duplicating the dated Section membership ONB already owns. Programme is stable
for the whole degree and the membership stays the single source of truth.

No PA tier: students get no email/tasks client (00-overview.md §4).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO roles (code, name, unit_type, singleton, term_bound, pa_tier) "
            "VALUES ('student', 'Student', 'program', false, false, NULL) "
            "ON CONFLICT (code) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM grants WHERE role_code = 'student'"))
    op.execute(sa.text("DELETE FROM roles WHERE code = 'student'"))
