"""Business rules for the auth module. The only layer other modules may call.

Covers AUTH-FR-02/03/06/07/09: password+OTP login with lockout ladder and OTP
rate limits, opaque Redis sessions (revocation-exact), password lifecycle,
single-device registration with approval-based change, and DPDP consent capture.
Step-up re-auth ships with the first sensitive consumer (QPG paper release).
"""

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.logging import get_logger
from unicore.core.redis import get_redis
from unicore.core.security import AuthContext, InvalidTokenError
from unicore.modules.audit import service as audit_service
from unicore.modules.auth import dao, providers
from unicore.modules.auth.models import ConsentRecord, Device, DeviceChangeRequest, OtpChallenge

_hasher = PasswordHasher()

OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
OTP_RATE_LIMIT_PER_HOUR = 5
LOCK_AFTER_FAILURES = 5
LOCK_MINUTES = 15
LOCKOUTS_BEFORE_ADMIN = 3
SESSION_TTL_STAFF = timedelta(hours=12)
SESSION_TTL_PRIVILEGED = timedelta(hours=4)
PRIVILEGED_ROLES = {"super-admin", "system-admin", "exam-cell", "controller-of-examination"}
# Minimal breached-password denylist; replaced by a k-anonymity check pre-launch.
BREACHED = {"password123", "1234567890", "qwertyuiop", "iloveyou123", "admin@12345"}


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def validate_password_policy(raw: str) -> None:
    if len(raw) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters.")
    if raw.lower() in BREACHED:
        raise HTTPException(status_code=422, detail="This password appears in breach lists.")


async def set_temp_password(session: AsyncSession, ctx: AuthContext, user_id: uuid.UUID) -> str:
    """Admin action: generate + deliver a one-time credential (forced change on login)."""
    from unicore.modules.user import service as user_service

    user = await user_service.get_user(session, user_id)
    temp = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temp)
    user.force_password_change = True
    channel = await providers.deliver(
        user.mobile, user.email, f"UniCore temporary password: {temp}"
    )
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="auth.temp-password.issued",
        object_type="user",
        object_id=str(user.id),
        after={"channel": channel},
    )
    await session.commit()
    return channel


async def login(session: AsyncSession, username: str, password: str) -> uuid.UUID:
    """Password stage. On success issues an OTP challenge and returns its id."""
    from unicore.modules.user import service as user_service

    user = await user_service.get_by_username(session, username)
    if user is None or user.status != "active" or user.password_hash is None:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    r = get_redis()
    if await r.exists(f"lock:{user.id}"):
        raise HTTPException(status_code=423, detail="Account locked. Try again later.")
    if await r.exists(f"adminlock:{user.id}"):
        raise HTTPException(status_code=423, detail="Account locked. Contact the IT cell.")

    try:
        _hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        fails = await r.incr(f"fails:{user.id}")
        await r.expire(f"fails:{user.id}", 900)
        if fails >= LOCK_AFTER_FAILURES:
            await r.setex(f"lock:{user.id}", LOCK_MINUTES * 60, "1")
            await r.delete(f"fails:{user.id}")
            lockouts = await r.incr(f"lockouts:{user.id}")
            await r.expire(f"lockouts:{user.id}", 86400)
            if lockouts >= LOCKOUTS_BEFORE_ADMIN:
                await r.set(f"adminlock:{user.id}", "1")
            get_logger().warning("account locked after failed logins")  # AUTH-FR-12 signal
        raise HTTPException(status_code=401, detail="Invalid credentials.") from None
    await r.delete(f"fails:{user.id}")

    issued = await r.incr(f"otprate:{user.id}")
    await r.expire(f"otprate:{user.id}", 3600)
    if issued > OTP_RATE_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many OTP requests. Try later.")

    return await _issue_otp(session, user.id, "login", user.mobile, user.email)


async def _issue_otp(
    session: AsyncSession,
    user_id: uuid.UUID,
    purpose: str,
    mobile: str | None,
    email: str | None,
) -> uuid.UUID:
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = OtpChallenge(
        user_id=user_id,
        purpose=purpose,
        code_hash=hash_password(code),
        expires_at=datetime.now(UTC) + OTP_TTL,
    )
    session.add(challenge)
    await session.flush()
    await providers.deliver(mobile, email, f"UniCore OTP: {code} (valid 5 minutes)")
    await session.commit()
    return challenge.id


async def verify_otp(
    session: AsyncSession, challenge_id: uuid.UUID, code: str, purpose: str = "login"
) -> tuple[str, bool]:
    """OTP stage. Returns (session token, force_password_change)."""
    from unicore.modules.user import service as user_service

    challenge = await dao.get_challenge(session, challenge_id)
    if challenge is None or challenge.purpose != purpose or challenge.consumed:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
    if datetime.now(UTC) > challenge.expires_at:
        raise HTTPException(status_code=401, detail="OTP expired. Request a new one.")
    if challenge.attempts >= OTP_MAX_ATTEMPTS:
        challenge.consumed = True
        await session.commit()
        raise HTTPException(status_code=401, detail="OTP invalidated. Request a new one.")
    try:
        _hasher.verify(challenge.code_hash, code)
    except VerifyMismatchError:
        challenge.attempts += 1
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            challenge.consumed = True
        await session.commit()
        raise HTTPException(status_code=401, detail="Incorrect OTP.") from None

    challenge.consumed = True
    user = await user_service.get_user(session, challenge.user_id)
    token = await _create_session(session, user.id)
    await session.commit()
    return token, user.force_password_change


async def _create_session(session: AsyncSession, user_id: uuid.UUID) -> str:
    from unicore.modules.rbac import service as rbac_service

    grants = await rbac_service.list_user_grants(session, user_id)
    roles = sorted({g.role_code for g in grants if g.status == "active"})
    ttl = SESSION_TTL_PRIVILEGED if PRIVILEGED_ROLES & set(roles) else SESSION_TTL_STAFF
    token = secrets.token_urlsafe(32)
    r = get_redis()
    payload = json.dumps({"user_id": str(user_id), "roles": roles})
    await r.setex(f"session:{token}", int(ttl.total_seconds()), payload)
    await r.sadd(f"usersessions:{user_id}", token)
    await r.expire(f"usersessions:{user_id}", int(SESSION_TTL_STAFF.total_seconds()))
    return token


async def verify_session_token(token: str) -> AuthContext:
    """Registered with the core auth gate at app startup (fails closed before that)."""
    data = await get_redis().get(f"session:{token}")
    if data is None:
        raise InvalidTokenError
    payload = json.loads(data)
    return AuthContext(
        user_id=payload["user_id"],
        session_id=token[:8],
        role_names=tuple(payload.get("roles", ())),
    )


async def revoke_user_sessions(user_id: uuid.UUID | str) -> int:
    """AUTH-FR-07: deactivation revokes every session immediately (well under 60 s)."""
    r = get_redis()
    tokens = await r.smembers(f"usersessions:{user_id}")
    for token in tokens:
        text = token.decode() if isinstance(token, bytes) else token
        await r.delete(f"session:{text}")
    await r.delete(f"usersessions:{user_id}")
    return len(tokens)


async def change_password(
    session: AsyncSession, ctx: AuthContext, current: str, new: str
) -> None:
    from unicore.modules.user import service as user_service

    user = await user_service.get_user(session, uuid.UUID(ctx.user_id))
    if user.password_hash is None:
        raise HTTPException(status_code=409, detail="No password set.")
    try:
        _hasher.verify(user.password_hash, current)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Current password incorrect.") from None
    validate_password_policy(new)
    user.password_hash = hash_password(new)
    user.force_password_change = False
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="auth.password.changed",
        object_type="user",
        object_id=str(user.id),
    )
    await session.commit()


async def request_password_reset(session: AsyncSession, username: str) -> uuid.UUID | None:
    """AUTH-FR-03. Returns None for unknown users (no account enumeration)."""
    from unicore.modules.user import service as user_service

    user = await user_service.get_by_username(session, username)
    if user is None or user.status != "active":
        return None
    return await _issue_otp(session, user.id, "password-reset", user.mobile, user.email)


async def confirm_password_reset(
    session: AsyncSession, challenge_id: uuid.UUID, code: str, new_password: str
) -> None:
    from unicore.modules.user import service as user_service

    challenge = await dao.get_challenge(session, challenge_id)
    if challenge is None:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
    validate_password_policy(new_password)
    if challenge.purpose != "password-reset" or challenge.consumed:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
    if datetime.now(UTC) > challenge.expires_at:
        raise HTTPException(status_code=401, detail="OTP expired. Request a new one.")
    try:
        _hasher.verify(challenge.code_hash, code)
    except VerifyMismatchError:
        challenge.attempts += 1
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            challenge.consumed = True
        await session.commit()
        raise HTTPException(status_code=401, detail="Incorrect OTP.") from None
    challenge.consumed = True
    user = await user_service.get_user(session, challenge.user_id)
    user.password_hash = hash_password(new_password)
    user.force_password_change = False
    await audit_service.record(
        session,
        actor=str(user.id),
        action="auth.password.reset",
        object_type="user",
        object_id=str(user.id),
    )
    await session.commit()
    await revoke_user_sessions(user.id)


# --- device registration (AUTH-FR-06) ---------------------------------------


async def register_device(
    session: AsyncSession, ctx: AuthContext, fingerprint: str
) -> Device:
    user_id = uuid.UUID(ctx.user_id)
    if await dao.active_device(session, user_id) is not None:
        raise HTTPException(
            status_code=409, detail="A device is already registered — request a change."
        )
    device = Device(user_id=user_id, fingerprint=fingerprint)
    session.add(device)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A device is already registered.") from None
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="auth.device.registered",
        object_type="device",
        object_id=str(device.id),
    )
    await session.commit()
    return device


async def request_device_change(
    session: AsyncSession, ctx: AuthContext, new_fingerprint: str
) -> DeviceChangeRequest:
    user_id = uuid.UUID(ctx.user_id)
    if await dao.active_device(session, user_id) is None:
        raise HTTPException(status_code=409, detail="No registered device — register directly.")
    request = DeviceChangeRequest(user_id=user_id, new_fingerprint=new_fingerprint)
    session.add(request)
    try:
        await session.flush()
    except IntegrityError:  # TC-AUTH-009: second pending request loses
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A change request is already pending."
        ) from None
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="auth.device.change-requested",
        object_type="device_change_request",
        object_id=str(request.id),
    )
    await session.commit()
    return request


async def approve_device_change(
    session: AsyncSession, ctx: AuthContext, request_id: uuid.UUID
) -> Device:
    """Approver: System Admin (Class In-charge joins once ONB membership exists)."""
    request = await dao.get_change_request(session, request_id)
    if request is None or request.status != "pending":
        raise HTTPException(status_code=404, detail="No pending request found.")
    old = await dao.active_device(session, request.user_id)
    if old is not None:
        old.status = "invalidated"
        old.invalidated_at = datetime.now(UTC)
        await session.flush()  # the unique index needs the old row gone first
    new = Device(user_id=request.user_id, fingerprint=request.new_fingerprint)
    session.add(new)
    request.status = "approved"
    request.decided_at = datetime.now(UTC)
    request.decided_by = ctx.user_id
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="auth.device.change-approved",
        object_type="device",
        object_id=str(new.id),
        before={"old_device": str(old.id) if old else None},
        after={"new_device": str(new.id)},
    )
    await session.commit()
    return new


async def device_is_active(session: AsyncSession, user_id: uuid.UUID, fingerprint: str) -> bool:
    """Consumed by ATT scan validation in its milestone."""
    device = await dao.active_device(session, user_id)
    return device is not None and device.fingerprint == fingerprint


# --- DPDP consent (AUTH-FR-09) -----------------------------------------------


async def record_consent(
    session: AsyncSession, ctx: AuthContext, notice_version: str, geolocation: bool
) -> ConsentRecord:
    record = ConsentRecord(
        user_id=uuid.UUID(ctx.user_id),
        notice_version=notice_version,
        geolocation_consent=geolocation,
    )
    session.add(record)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="auth.consent.recorded",
        object_type="consent",
        object_id=str(record.id),
        after=record.as_dict(),
    )
    await session.commit()
    return record


async def latest_consent(session: AsyncSession, user_id: uuid.UUID) -> ConsentRecord | None:
    """Consent-state API for other modules (ATT geolocation gate)."""
    return await dao.latest_consent(session, user_id)
