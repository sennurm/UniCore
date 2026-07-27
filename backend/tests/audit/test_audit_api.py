"""Audit immutability + read API (AUTH-FR-08, TC-AUTH-014) and the outbox."""

import uuid

from unicore.core.db import get_sessionmaker
from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.org import service as org_service
from unicore.modules.rbac import service as rbac_service

SETUP_CTX = AuthContext(user_id="test-setup", session_id="s", role_names=("super-admin",))


async def test_audit_is_read_only_and_scoped(make_client) -> None:
    """TC-AUTH-014: no update/delete route exists; reads need the audit role."""
    async with make_client("system-admin") as admin:
        await admin.post(
            "/user", json={"username": "a.b", "full_name": "A B", "kind": "staff"}
        )
        events = await admin.get("/audit/events", params={"action": "user.provisioned"})
        assert events.status_code == 200
        assert len(events.json()) == 1
        event_id = events.json()[0]["id"]

        for method in ("put", "patch", "delete"):
            response = await getattr(admin, method)(f"/audit/events/{event_id}")
            assert response.status_code in (404, 405)  # the route does not exist

    async with make_client(user_id="plain.user") as plain:
        assert (await plain.get("/audit/events")).status_code == 403


async def test_outbox_dispatch_drives_term_closure(make_client) -> None:
    """Phase 4 outbox: publish -> dispatch -> rbac revocation handler fires."""
    async with make_client("super-admin") as admin:
        uni = (await admin.post(
            "/org/units", json={"type": "university", "name": "U", "code": "UNI"}
        )).json()
        fd = (await admin.post(
            "/org/units",
            json={"type": "faculty_division", "name": "F", "code": "F1", "parent_id": uni["id"]},
        )).json()
        school = (await admin.post(
            "/org/units",
            json={"type": "school", "name": "S", "code": "S1", "parent_id": fd["id"]},
        )).json()
        dept = (await admin.post(
            "/org/units",
            json={"type": "department", "name": "D", "code": "D1", "parent_id": school["id"]},
        )).json()
        prog = (await admin.post(
            "/org/units",
            json={"type": "program", "name": "P", "code": "P1", "parent_id": dept["id"]},
        )).json()
        user = (await admin.post(
            "/user", json={"username": "ic.x", "full_name": "IC X", "kind": "staff"}
        )).json()

        async with get_sessionmaker()() as session:
            section = await org_service.create_section_instance(
                session, SETUP_CTX, uuid.UUID(prog["id"]), "3B", "2026-S1"
            )
            section_id = section.id
        issued = await admin.post(
            "/rbac/grants",
            json={
                "user_id": user["id"],
                "role_code": "class-incharge",
                "org_unit_id": str(section_id),
                "term_code": "2026-S1",
            },
        )
        assert issued.status_code == 201

        rbac_service.register_event_handlers()
        async with get_sessionmaker()() as session:
            await audit_service.publish(
                session,
                "prm.term-closure",
                {"term_code": "2026-S1", "section_unit_ids": [str(section_id)]},
            )
            await session.commit()
        dispatched = await audit_service.dispatch_pending()
        assert dispatched == 1

        grants = (await admin.get(f"/rbac/users/{user['id']}/grants")).json()
    assert grants[0]["status"] == "revoked"
    assert grants[0]["revoke_cause"] == "term-closure"
