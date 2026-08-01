"""Password+OTP authentication. TC-AUTH-001/002/003/004/007/012."""

import httpx
from sqlalchemy import text

from unicore.core.db import get_sessionmaker
from unicore.modules.auth.providers import sms_provider

USER = {
    "username": "s.priya",
    "full_name": "S. Priya",
    "kind": "staff",
    "mobile": "9990001111",
}


def _last_code() -> str:
    body = sms_provider.outbox[-1].body
    return body.split("UniCore OTP:")[1].strip().split()[0]


def _temp_password() -> str:
    for message in reversed(sms_provider.outbox):
        if "temporary password" in message.body:
            return message.body.split(":", 1)[1].strip()
    raise AssertionError("no temp password delivered")


async def _provision(admin: httpx.AsyncClient) -> str:
    user = (await admin.post("/user", json=USER)).json()
    issued = await admin.post(f"/auth/users/{user['id']}/temp-password")
    assert issued.status_code == 202
    assert issued.json()["delivered_via"] == "sms"
    return user["id"]


async def test_login_otp_session_and_forced_change(make_client) -> None:
    """TC-AUTH-001: password -> OTP -> session scoped to grants; forced first change."""
    async with make_client("system-admin") as admin:
        await _provision(admin)
        password = _temp_password()

        login = await admin.post(
            "/auth/login", json={"username": USER["username"], "password": password}
        )
        assert login.status_code == 200, login.text
        verified = await admin.post(
            "/auth/otp/verify",
            json={"challenge_id": login.json()["challenge_id"], "code": _last_code()},
        )
        assert verified.status_code == 200
        token = verified.json()["token"]
        assert verified.json()["force_password_change"] is True

        me = await admin.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200

        changed = await admin.post(
            "/auth/password",
            json={"current_password": password, "new_password": "correct-horse-staple-9"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert changed.status_code == 204


async def test_five_wrong_passwords_lock_account(make_client) -> None:
    """TC-AUTH-002: 15-minute lock after 5 consecutive failures."""
    async with make_client("system-admin") as admin:
        await _provision(admin)
        password = _temp_password()
        for _ in range(5):
            failed = await admin.post(
                "/auth/login", json={"username": USER["username"], "password": "wrong-pass-1"}
            )
            assert failed.status_code == 401
        locked = await admin.post(
            "/auth/login", json={"username": USER["username"], "password": password}
        )
    assert locked.status_code == 423  # even the CORRECT password is refused


async def test_otp_expires(make_client) -> None:
    """TC-AUTH-003: an OTP older than its validity window is rejected."""
    async with make_client("system-admin") as admin:
        await _provision(admin)
        password = _temp_password()
        login = await admin.post(
            "/auth/login", json={"username": USER["username"], "password": password}
        )
        challenge_id = login.json()["challenge_id"]
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    "UPDATE otp_challenges SET expires_at = now() - interval '1 minute' "
                    "WHERE id = :id"
                ),
                {"id": challenge_id},
            )
            await session.commit()
        rejected = await admin.post(
            "/auth/otp/verify", json={"challenge_id": challenge_id, "code": _last_code()}
        )
    assert rejected.status_code == 401
    assert "expired" in rejected.json()["detail"].lower()


async def test_sixth_otp_attempt_invalidates(make_client) -> None:
    """TC-AUTH-004: five wrong attempts consume the OTP — the right code no longer works."""
    async with make_client("system-admin") as admin:
        await _provision(admin)
        password = _temp_password()
        login = await admin.post(
            "/auth/login", json={"username": USER["username"], "password": password}
        )
        challenge_id = login.json()["challenge_id"]
        good = _last_code()
        wrong = "000000" if good != "000000" else "111111"
        for _ in range(5):
            await admin.post("/auth/otp/verify", json={"challenge_id": challenge_id, "code": wrong})
        final = await admin.post(
            "/auth/otp/verify", json={"challenge_id": challenge_id, "code": good}
        )
    assert final.status_code == 401


async def test_deactivation_revokes_sessions(make_client) -> None:
    """TC-AUTH-007: deactivation kills live sessions immediately."""
    async with make_client("system-admin") as admin:
        user_id = await _provision(admin)
        password = _temp_password()
        login = await admin.post(
            "/auth/login", json={"username": USER["username"], "password": password}
        )
        verified = await admin.post(
            "/auth/otp/verify",
            json={"challenge_id": login.json()["challenge_id"], "code": _last_code()},
        )
        token = verified.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert (await admin.get("/auth/me", headers=headers)).status_code == 200

        assert (await admin.post(f"/user/{user_id}/deactivate")).status_code == 200
        assert (await admin.get("/auth/me", headers=headers)).status_code == 401


async def test_otp_flood_throttled(make_client) -> None:
    """TC-AUTH-012: max 5 OTP issues per hour per account."""
    async with make_client("system-admin") as admin:
        await _provision(admin)
        password = _temp_password()
        statuses = []
        for _ in range(6):
            response = await admin.post(
                "/auth/login", json={"username": USER["username"], "password": password}
            )
            statuses.append(response.status_code)
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


async def test_otp_disabled_issues_session_directly(make_client, monkeypatch) -> None:
    """UNICORE_OTP_LOGIN_ENABLED=false (dev only): password stage returns a session."""
    from unicore.core.config import get_settings

    async with make_client("system-admin") as admin:
        await _provision(admin)
        password = _temp_password()
        monkeypatch.setattr(get_settings(), "otp_login_enabled", False)
        login = await admin.post(
            "/auth/login", json={"username": USER["username"], "password": password}
        )
        assert login.status_code == 200
        body = login.json()
        assert body["challenge_id"] is None
        assert body["token"]
        me = await admin.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
        assert me.status_code == 200
    assert not sms_provider.outbox or "OTP" not in sms_provider.outbox[-1].body


def test_otp_cannot_be_disabled_in_production() -> None:
    import pytest as _pytest

    from unicore.core.config import Settings

    with _pytest.raises(ValueError, match="cannot be disabled in production"):
        Settings(environment="production", otp_login_enabled=False)
