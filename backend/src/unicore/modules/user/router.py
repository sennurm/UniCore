"""HTTP endpoints for the user module. No business logic here (see ARCHITECTURE.md)."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.rbac.service import require_permission
from unicore.modules.user import service
from unicore.modules.user.schemas import (
    GrievanceCreate,
    GrievanceOut,
    GrievanceResolve,
    UserCreate,
    UserOut,
)

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


def _ctx(request: Request) -> AuthContext:
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None:  # pragma: no cover — the gate rejects earlier
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Unauthenticated.")
    return ctx


@router.post("/me/grievances", response_model=GrievanceOut, status_code=201)
async def file_grievance(
    payload: GrievanceCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GrievanceOut:
    grievance = await service.file_grievance(
        session, _ctx(request), payload.kind, payload.details
    )
    return GrievanceOut.model_validate(grievance)


@router.get("/me/grievances", response_model=list[GrievanceOut])
async def my_grievances(
    request: Request, session: AsyncSession = Depends(get_session)
) -> list[GrievanceOut]:
    rows = await service.list_own_grievances(session, _ctx(request))
    return [GrievanceOut.model_validate(g) for g in rows]


@router.get("/grievances/open", response_model=list[GrievanceOut])
async def open_grievances(
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("grievance:resolve")),
) -> list[GrievanceOut]:
    rows = await service.list_open_grievances(session)
    return [GrievanceOut.model_validate(g) for g in rows]


@router.post("/grievances/{grievance_id}/resolve", response_model=GrievanceOut)
async def resolve_grievance(
    grievance_id: uuid.UUID,
    payload: GrievanceResolve,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("grievance:resolve")),
) -> GrievanceOut:
    grievance = await service.resolve_grievance(session, ctx, grievance_id, payload.response)
    return GrievanceOut.model_validate(grievance)
