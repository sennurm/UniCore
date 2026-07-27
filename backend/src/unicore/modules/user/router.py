"""HTTP endpoints for the user module. No business logic here (see ARCHITECTURE.md)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.rbac.service import require_permission
from unicore.modules.user import service
from unicore.modules.user.schemas import UserCreate, UserOut

router = APIRouter(prefix="/user", tags=["user"])


@router.post("", response_model=UserOut, status_code=201)
async def provision_user(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("user:create")),
) -> UserOut:
    return UserOut.model_validate(await service.provision_user(session, ctx, payload))


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("user:read")),
) -> UserOut:
    return UserOut.model_validate(await service.get_user(session, user_id))


@router.post("/{user_id}/deactivate", response_model=UserOut)
async def deactivate_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("user:deactivate")),
) -> UserOut:
    return UserOut.model_validate(await service.deactivate_user(session, ctx, user_id))
