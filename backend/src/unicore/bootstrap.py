"""Idempotent first-run bootstrap: university root + Super Admin account.

    python -m unicore.bootstrap --university-name "..." --university-code UNI \
        --admin-username sadmin --admin-full-name "..."

The super-admin ROLE GRANT is issued in Phase 2 when the grant engine exists;
until then the account is provisioned and flagged in logs. Re-running is safe.
"""

import argparse
import asyncio
import uuid

from unicore.core.db import get_sessionmaker
from unicore.core.logging import configure_logging, get_logger
from unicore.core.security import AuthContext
from unicore.modules.org import dao as org_dao
from unicore.modules.org import service as org_service
from unicore.modules.org.schemas import OrgUnitCreate
from unicore.modules.user import dao as user_dao
from unicore.modules.user import service as user_service
from unicore.modules.user.schemas import UserCreate

_BOOTSTRAP_CTX = AuthContext(
    user_id="bootstrap", session_id="bootstrap", role_names=("super-admin",)
)


async def run(
    university_name: str,
    university_code: str,
    admin_username: str,
    admin_full_name: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    log = get_logger()
    async with get_sessionmaker()() as session:
        root = await org_dao.get_root(session)
        if root is None:
            root = await org_service.create_unit(
                session,
                _BOOTSTRAP_CTX,
                OrgUnitCreate(type="university", name=university_name, code=university_code),
            )
            log.info("university root created", org_unit_id=str(root.id))
        else:
            log.info("university root already exists", org_unit_id=str(root.id))

        admin = await user_dao.get_by_username(session, admin_username)
        if admin is None:
            admin = await user_service.provision_user(
                session,
                _BOOTSTRAP_CTX,
                UserCreate(username=admin_username, full_name=admin_full_name, kind="staff"),
            )
            log.info("super admin account provisioned", user_id=str(admin.id))
        else:
            log.info("super admin account already exists", user_id=str(admin.id))

    await _ensure_super_admin_grant(admin.id)
    await _ensure_initial_password(admin.id)
    return root.id, admin.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--university-name", required=True)
    parser.add_argument("--university-code", required=True)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--admin-full-name", required=True)
    args = parser.parse_args()
    configure_logging("unicore-bootstrap")
    asyncio.run(
        run(
            args.university_name,
            args.university_code,
            args.admin_username,
            args.admin_full_name,
        )
    )


async def _ensure_super_admin_grant(admin_id: uuid.UUID) -> None:
    """Issue the super-admin grant directly (bootstrap predates any granter)."""
    from unicore.modules.rbac import dao as rbac_dao
    from unicore.modules.rbac import service as rbac_service
    from unicore.modules.rbac.models import Grant

    async with get_sessionmaker()() as session:
        existing = [
            g
            for g in await rbac_dao.grants_for_user(session, admin_id)
            if g.role_code == "super-admin" and g.status == "active"
        ]
        if existing:
            return
        session.add(Grant(user_id=admin_id, role_code="super-admin", granted_by="bootstrap"))
        await session.commit()
        rbac_service.invalidate_user(admin_id)
        get_logger().info("super admin grant issued", user_id=str(admin_id))


async def _ensure_initial_password(admin_id: uuid.UUID) -> None:
    """One-time initial credential, printed to the operator (forced change on login)."""
    import secrets

    from unicore.modules.auth import service as auth_service
    from unicore.modules.user import dao as user_dao_2

    async with get_sessionmaker()() as session:
        admin = await user_dao_2.get_by_id(session, admin_id)
        if admin is None or admin.password_hash is not None:
            return
        temp = secrets.token_urlsafe(9)
        admin.password_hash = auth_service.hash_password(temp)
        admin.force_password_change = True
        await session.commit()
    # Printed once for the operator running bootstrap — never logged.
    print(f"INITIAL SUPER ADMIN PASSWORD (change on first login): {temp}")  # noqa: T201


if __name__ == "__main__":
    main()
