"""HTTP endpoints for the auth module. No business logic here (see ARCHITECTURE.md).

/auth/login, /auth/otp/verify and the password-reset pair are on the public
allowlist (core.security); everything else requires a session. Self-service
endpoints resolve the subject from the AuthContext — never a client-supplied id.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.auth import service
from unicore.modules.auth.schemas import (
    ConsentIn,
    DeviceChangeRequestIn,
    DeviceChangeRequestOut,
    DeviceOut,
    DeviceRegisterRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    OtpVerifyRequest,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    SessionResponse,
)
from unicore.modules.rbac.service import require_permission

router = APIRouter(prefix="/auth", tags=["auth"])


def _ctx(request: Request) -> AuthContext:
    """Authenticated caller for self-service endpoints (gate guarantees presence)."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None:  # pragma: no cover — the gate rejects earlier
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Unauthenticated.")
    return ctx


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_session)
) -> LoginResponse:
    result = await service.login(session, payload.username, payload.password)
    if result.token is not None:
        return LoginResponse(
            token=result.token,
            force_password_change=result.force_password_change,
            message="Signed in (OTP disabled in this environment).",
        )
    return LoginResponse(challenge_id=result.challenge_id)


@router.post("/otp/verify", response_model=SessionResponse)
async def verify_otp(
    payload: OtpVerifyRequest, session: AsyncSession = Depends(get_session)
) -> SessionResponse:
    token, force_change = await service.verify_otp(session, payload.challenge_id, payload.code)
    return SessionResponse(token=token, force_password_change=force_change)


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    ctx = _ctx(request)
    return MeResponse(user_id=ctx.user_id, roles=ctx.role_names)


@router.post("/password", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    await service.change_password(
        session, _ctx(request), payload.current_password, payload.new_password
    )


@router.post("/password-reset/request", status_code=202)
async def password_reset_request(
    payload: PasswordResetRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, str | None]:
    challenge_id = await service.request_password_reset(session, payload.username)
    # Identical response for unknown users: no account enumeration.
    return {"challenge_id": str(challenge_id) if challenge_id else None}


@router.post("/password-reset/confirm", status_code=204)
async def password_reset_confirm(
    payload: PasswordResetConfirm, session: AsyncSession = Depends(get_session)
) -> None:
    await service.confirm_password_reset(
        session, payload.challenge_id, payload.code, payload.new_password
    )


@router.post("/users/{user_id}/temp-password", status_code=202)
async def issue_temp_password(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("user:create")),
) -> dict[str, str]:
    channel = await service.set_temp_password(session, ctx, user_id)
    return {"delivered_via": channel}


@router.post("/devices/register", response_model=DeviceOut, status_code=201)
async def register_device(
    payload: DeviceRegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DeviceOut:
    device = await service.register_device(session, _ctx(request), payload.fingerprint)
    return DeviceOut.model_validate(device)


@router.post("/devices/change-request", response_model=DeviceChangeRequestOut, status_code=201)
async def request_device_change(
    payload: DeviceChangeRequestIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DeviceChangeRequestOut:
    change = await service.request_device_change(
        session, _ctx(request), payload.new_fingerprint
    )
    return DeviceChangeRequestOut.model_validate(change)


@router.post("/devices/requests/{request_id}/approve", response_model=DeviceOut)
async def approve_device_change(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("device:approve")),
) -> DeviceOut:
    device = await service.approve_device_change(session, ctx, request_id)
    return DeviceOut.model_validate(device)


@router.post("/consent", status_code=201)
async def record_consent(
    payload: ConsentIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    record = await service.record_consent(
        session, _ctx(request), payload.notice_version, payload.geolocation_consent
    )
    return record.as_dict()


@router.get("/consent")
async def consent_status(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, object] | None:
    record = await service.latest_consent(session, uuid.UUID(_ctx(request).user_id))
    return record.as_dict() if record else None
