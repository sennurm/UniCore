"""Business rules for the user module. The only layer other modules may call.

Covers AUTH-FR-01: accounts are provisioned only (admin action here; ONB bulk
import arrives in milestone 2). AUTH business rule 1: re-joining users are
REACTIVATED, never duplicated — matched on ERP ID.
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.user import dao
from unicore.modules.user.models import STATUTORY_EXEMPTION_NOTE, Grievance, User
from unicore.modules.user.schemas import UserCreate


def _snapshot(user: User) -> dict[str, str | None]:
    return {
        "username": user.username,
        "erp_id": user.erp_id,
        "full_name": user.full_name,
        "kind": user.kind,
        "status": user.status,
    }


async def provision_user(session: AsyncSession, ctx: AuthContext, data: UserCreate) -> User:
    if data.erp_id is not None:
        existing = await dao.get_by_erp_id(session, data.erp_id)
        if existing is not None:
            if existing.status in ("deactivated", "withdrawn"):
                return await _reactivate(session, ctx, existing, data)
            raise HTTPException(
                status_code=409, detail=f"An active user with ERP ID {data.erp_id} exists."
            )
    if await dao.get_by_username(session, data.username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken.")

    user = User(
        username=data.username,
        erp_id=data.erp_id,
        full_name=data.full_name,
        email=data.email,
        mobile=data.mobile,
        kind=data.kind,
        status="active",
        force_password_change=True,
    )
    session.add(user)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="user.provisioned",
        object_type="user",
        object_id=str(user.id),
        after=_snapshot(user),
    )
    await session.commit()
    return user


async def _reactivate(
    session: AsyncSession, ctx: AuthContext, user: User, data: UserCreate
) -> User:
    before = _snapshot(user)
    user.status = "active"
    user.full_name = data.full_name
    user.email = data.email or user.email
    user.mobile = data.mobile or user.mobile
    user.force_password_change = True
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="user.reactivated",
        object_type="user",
        object_id=str(user.id),
        before=before,
        after=_snapshot(user),
    )
    await session.commit()
    return user


async def deactivate_user(session: AsyncSession, ctx: AuthContext, user_id: uuid.UUID) -> User:
    user = await get_user(session, user_id)
    if user.status == "deactivated":
        return user
    before = _snapshot(user)
    user.status = "deactivated"
    from unicore.modules.auth import service as auth_service  # lazy: avoids import cycle

    await auth_service.revoke_user_sessions(user.id)  # AUTH-FR-07: immediate revocation
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="user.deactivated",
        object_type="user",
        object_id=str(user.id),
        before=before,
        after=_snapshot(user),
    )
    await session.commit()
    return user


async def provision_student(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    username: str,
    full_name: str,
    erp_id: str,
    email: str | None = None,
    mobile: str | None = None,
) -> User:
    """Provision from an import row: no commit (the batch owns the transaction),
    state IMPORTED until credential delivery activates the account (ONB-FR-06).

    Takes primitives rather than a schema object so other modules never import
    this module's schemas (ARCHITECTURE.md: service-to-service only).
    """
    if await dao.get_by_username(session, username) is not None:
        username = f"{username}.{uuid.uuid4().hex[:4]}"
    user = User(
        username=username,
        erp_id=erp_id,
        full_name=full_name,
        email=email,
        mobile=mobile,
        kind="student",
        status="imported",
        force_password_change=True,
    )
    session.add(user)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="user.imported",
        object_type="user",
        object_id=str(user.id),
        after=_snapshot(user),
    )
    return user


async def list_users(
    session: AsyncSession, search: str | None, status: str | None, limit: int
) -> list[User]:
    """Filterable listing for the combined Users & roles directory."""
    return list(await dao.list_users(session, search, status, limit))


async def get_by_erp_id(session: AsyncSession, erp_id: str) -> User | None:
    return await dao.get_by_erp_id(session, erp_id)


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    return await dao.get_by_username(session, username)


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await dao.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


# --- DPDP grievances (AUTH-FR-10) ---------------------------------------------


async def file_grievance(
    session: AsyncSession, ctx: AuthContext, kind: str, details: str
) -> Grievance:
    grievance = Grievance(user_id=uuid.UUID(ctx.user_id), kind=kind, details=details)
    session.add(grievance)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="user.grievance.filed",
        object_type="grievance",
        object_id=str(grievance.id),
        after={"kind": kind},
    )
    await session.commit()
    return grievance


async def list_own_grievances(session: AsyncSession, ctx: AuthContext) -> list[Grievance]:
    return list(await dao.list_grievances(session, uuid.UUID(ctx.user_id), None))


async def list_open_grievances(session: AsyncSession) -> list[Grievance]:
    return list(await dao.list_grievances(session, None, "open"))


async def resolve_grievance(
    session: AsyncSession, ctx: AuthContext, grievance_id: uuid.UUID, response: str
) -> Grievance:
    grievance = await dao.get_grievance(session, grievance_id)
    if grievance is None or grievance.status != "open":
        raise HTTPException(status_code=404, detail="No open grievance found.")
    # Erasure of academic records: the statutory exemption must be STATED,
    # never a silent refusal (AUTH doc §5).
    if grievance.kind == "erasure" and "statutory" not in response.lower():
        response = f"{response}\n\n{STATUTORY_EXEMPTION_NOTE}"
    grievance.status = "resolved"
    grievance.response = response
    grievance.resolved_at = datetime.now(UTC)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="user.grievance.resolved",
        object_type="grievance",
        object_id=str(grievance.id),
        after={"kind": grievance.kind, "status": "resolved"},
    )
    await session.commit()
    return grievance
