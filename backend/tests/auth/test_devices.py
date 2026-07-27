"""Single-device registration + approval-based change. TC-AUTH-008/009."""

import asyncio
import uuid

from unicore.core.db import get_sessionmaker
from unicore.modules.auth import service as auth_service


async def test_device_change_lifecycle(make_client, audit_rows) -> None:
    """TC-AUTH-008: approval invalidates the old device; exactly one active."""
    async with make_client(user_id="stud.dev") as student:
        first = await student.post("/auth/devices/register", json={"fingerprint": "device-AAA-1"})
        assert first.status_code == 201
        again = await student.post("/auth/devices/register", json={"fingerprint": "device-BBB-2"})
        assert again.status_code == 409  # exactly one active device

        request = await student.post(
            "/auth/devices/change-request", json={"new_fingerprint": "device-BBB-2"}
        )
        assert request.status_code == 201
        request_id = request.json()["id"]
        me = (await student.get("/auth/me")).json()

    async with make_client("system-admin") as admin:
        approved = await admin.post(f"/auth/devices/requests/{request_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["fingerprint"] == "device-BBB-2"

    async with get_sessionmaker()() as session:
        user_id = uuid.UUID(me["user_id"])
        assert not await auth_service.device_is_active(session, user_id, "device-AAA-1")
        assert await auth_service.device_is_active(session, user_id, "device-BBB-2")
    assert await audit_rows("auth.device.change-approved")


async def test_concurrent_change_requests_single_pending(make_client) -> None:
    """TC-AUTH-009: two racing requests — exactly one pending, never two devices."""
    async with make_client(user_id="stud.race") as student:
        assert (
            await student.post("/auth/devices/register", json={"fingerprint": "device-XYZ-1"})
        ).status_code == 201
        results = await asyncio.gather(
            student.post("/auth/devices/change-request", json={"new_fingerprint": "dev-NEW-1"}),
            student.post("/auth/devices/change-request", json={"new_fingerprint": "dev-NEW-2"}),
        )
    assert sorted(r.status_code for r in results) == [201, 409]


async def test_consent_capture_and_status(make_client, audit_rows) -> None:
    """AUTH-FR-09: versioned notice + separate geolocation consent, audited."""
    async with make_client(user_id="stud.consent") as student:
        assert (await student.get("/auth/consent")).json() is None
        recorded = await student.post(
            "/auth/consent", json={"notice_version": "v1", "geolocation_consent": False}
        )
        assert recorded.status_code == 201
        status = (await student.get("/auth/consent")).json()
    assert status["notice_version"] == "v1"
    assert status["geolocation_consent"] is False
    assert await audit_rows("auth.consent.recorded")
