"""rbac: roles catalog + grants with singleton partial indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIT_SINGLETONS = "('hod','school-incharge','faculty-dean','class-incharge')"
UNIVERSITY_SINGLETONS = (
    "('chancellor','vc','registrar','dean-academic-affairs','controller-of-examination')"
)

# code, name, unit_type, singleton, term_bound, pa_tier — from the AUTH role
# registry (source: requirements/sources/module_access_matrix.xlsx).
ROLE_SEED: list[tuple[str, str, str, bool, bool, str | None]] = [
    ("super-admin", "University Super Admin", "university", False, False, None),
    ("system-admin", "System Admin (IT cell)", "university", False, False, None),
    ("chancellor", "Chancellor", "university", True, False, "tier1"),
    ("vc", "Vice Chancellor", "university", True, False, "tier1"),
    ("registrar", "Registrar", "university", True, False, "tier1"),
    ("dean-academic-affairs", "Dean Academic Affairs", "university", True, False, "tier1"),
    ("faculty-dean", "Faculty Dean", "faculty_division", True, False, "tier1"),
    ("school-incharge", "School Incharge", "school", True, False, "tier1"),
    ("hod", "Head of Department", "department", True, False, "tier2"),
    ("class-incharge", "Class In-charge", "section", True, True, None),
    ("professor", "Professor", "department", False, False, "tier2"),
    ("associate-professor", "Associate Professor", "department", False, False, "tier2"),
    ("assistant-professor", "Assistant Professor", "department", False, False, "tier2"),
    ("tutor", "Tutor", "department", False, False, "tier2"),
    ("assistant-teaching-staff", "Assistant Teaching Staff", "department", False, False, "tier2"),
    ("timetable-cell", "Timetable Cell", "university", False, False, None),
    ("exam-cell", "Exam Cell", "university", False, False, None),
    ("controller-of-examination", "Controller of Examination", "university", True, False, "tier1"),
    ("school-admin", "School Admin (config)", "school", False, False, None),
    ("subject-coordinator", "Subject Coordinator", "department", False, False, None),
    ("subject-author", "Subject Author", "department", False, False, None),
    ("school-exam-coordinator", "School Exam Coordinator", "school", False, False, None),
    ("hr-designate", "HR Designate", "university", False, False, None),
]


def upgrade() -> None:
    op.execute("CREATE TYPE grant_status AS ENUM ('active','revoked')")
    op.execute("CREATE TYPE grant_revoke_cause AS ENUM ('manual','superseded','term-closure')")

    roles = op.create_table(
        "roles",
        sa.Column("code", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "unit_type", postgresql.ENUM(name="org_unit_type", create_type=False), nullable=False
        ),
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("term_bound", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pa_tier", sa.String(10)),
    )
    op.bulk_insert(
        roles,
        [
            {
                "code": code,
                "name": name,
                "unit_type": unit_type,
                "singleton": singleton,
                "term_bound": term_bound,
                "pa_tier": pa_tier,
            }
            for code, name, unit_type, singleton, term_bound, pa_tier in ROLE_SEED
        ],
    )

    op.create_table(
        "grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("role_code", sa.String(50), sa.ForeignKey("roles.code"), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("org_units.id")),
        sa.Column(
            "status",
            postgresql.ENUM(name="grant_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("term_code", sa.String(50)),
        sa.Column(
            "additional_charge", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("granted_by", sa.String(100), nullable=False),
        sa.Column("revoke_cause", postgresql.ENUM(name="grant_revoke_cause", create_type=False)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_grants_user_active",
        "grants",
        ["user_id"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_grants_role_unit", "grants", ["role_code", "org_unit_id"])
    # AUTH-FR-16: at most one active holder per unit for singleton leadership roles.
    op.execute(
        "CREATE UNIQUE INDEX uq_grants_singleton_unit ON grants (role_code, org_unit_id) "
        f"WHERE status = 'active' AND role_code IN {UNIT_SINGLETONS}"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_grants_singleton_university ON grants (role_code) "
        f"WHERE status = 'active' AND org_unit_id IS NULL AND role_code IN {UNIVERSITY_SINGLETONS}"
    )


def downgrade() -> None:
    op.drop_table("grants")
    op.drop_table("roles")
    op.execute("DROP TYPE grant_revoke_cause")
    op.execute("DROP TYPE grant_status")
