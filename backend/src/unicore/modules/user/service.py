"""Business rules for the user module. The only layer other modules may call.

Covers AUTH-FR-01: accounts are provisioned only (admin action here; ONB bulk
import arrives in milestone 2). AUTH business rule 1: re-joining users are
REACTIVATED, never duplicated — matched on ERP ID.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.user import dao
from unicore.modules.user.models import User
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
    # Phase 3: session revocation within 60 s hooks in here (AUTH-FR-07).
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


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await dao.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return user
