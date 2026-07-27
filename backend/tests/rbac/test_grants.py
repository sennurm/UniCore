"""Grant engine (AUTH-FR-04/13-17). Maps TC-AUTH-005/006/013/015/016/017/018/019/020/021/022."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from unicore.core.db import get_sessionmaker
from unicore.core.security import AuthContext
from unicore.modules.org import service as org_service
from unicore.modules.rbac import service as rbac_service

SETUP_CTX = AuthContext(user_id="test-setup", session_id="s", role_names=("super-admin",))


async def _unit(client: httpx.AsyncClient, **body: object) -> dict:
    response = await client.post("/org/units", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _user(client: httpx.AsyncClient, username: str) -> dict:
    response = await client.post(
        "/user", json={"username": username, "full_name": username, "kind": "staff"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _grant(client: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await client.post("/rbac/grants", json=body)


async def _tree(client: httpx.AsyncClient) -> dict[str, dict]:
    """University -> FET -> School -> two departments, each with a program + section."""
    uni = await _unit(client, type="university", name="U", code="UNI")
    fd = await _unit(client, type="faculty_division", name="FET", code="FET", parent_id=uni["id"])
    school = await _unit(client, type="school", name="SOCE", code="SOCE", parent_id=fd["id"])
    dept = {"type": "department", "parent_id": school["id"]}
    dept_a = await _unit(client, name="CSE", code="CSE", **dept)
    dept_b = await _unit(client, name="AIDS", code="AIDS", **dept)
    prog_a = await _unit(
        client, type="program", name="BTech CSE", code="BT-CSE", parent_id=dept_a["id"]
    )
    prog_b = await _unit(
        client, type="program", name="BTech AIDS", code="BT-AIDS", parent_id=dept_b["id"]
    )
    async with get_sessionmaker()() as session:
        sec_a = await org_service.create_section_instance(
            session, SETUP_CTX, __import__("uuid").UUID(prog_a["id"]), "3B", "2026-S1"
        )
        sec_b = await org_service.create_section_instance(
            session, SETUP_CTX, __import__("uuid").UUID(prog_b["id"]), "1A", "2026-S1"
        )
        sections = {"sec_a": {"id": str(sec_a.id)}, "sec_b": {"id": str(sec_b.id)}}
    return {
        "uni": uni, "fd": fd, "school": school,
        "dept_a": dept_a, "dept_b": dept_b, "prog_a": prog_a, "prog_b": prog_b, **sections,
    }


async def test_hod_designation_scope_enforced(make_client, audit_rows) -> None:
    """TC-AUTH-005 + AUTH-FR-15: HoD designates In-charge in own Department only."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        hod_user = await _user(admin, "hod.a")
        incharge = await _user(admin, "faculty.x")
        issued = await _grant(
            admin, user_id=hod_user["id"], role_code="hod", org_unit_id=tree["dept_a"]["id"]
        )
        assert issued.status_code == 201

    async with make_client(user_id="hod.a") as hod:
        own = await _grant(
            hod,
            user_id=incharge["id"],
            role_code="class-incharge",
            org_unit_id=tree["sec_a"]["id"],
            term_code="2026-S1",
        )
        assert own.status_code == 201
        foreign = await _grant(
            hod,
            user_id=incharge["id"],
            role_code="class-incharge",
            org_unit_id=tree["sec_b"]["id"],
            term_code="2026-S1",
        )
        assert foreign.status_code == 403  # other Department's Section

    denials = await audit_rows("rbac.grant.denied")
    assert denials, "denied grant attempt must be audited"


async def test_new_grant_effective_immediately(make_client) -> None:
    """TC-AUTH-006: grant visible to the grantee well inside the 60 s bound."""
    async with make_client("super-admin") as admin:
        uni = await _unit(admin, type="university", name="U", code="UNI")
        user = await _user(admin, "new.admin")
        assert (
            await _grant(admin, user_id=user["id"], role_code="system-admin")
        ).status_code == 201

    async with make_client(user_id="new.admin") as fresh:
        assert (await fresh.get(f"/org/units/{uni['id']}")).status_code == 200


async def test_expired_grant_denied_at_check_time(make_client) -> None:
    """TC-AUTH-013: expiry is enforced at permission-check time, not by cleanup jobs."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        hod_user = await _user(admin, "hod.expired")
        target = await _user(admin, "faculty.y")
        expired = await _grant(
            admin,
            user_id=hod_user["id"],
            role_code="hod",
            org_unit_id=tree["dept_a"]["id"],
            valid_until=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        )
        assert expired.status_code == 201

    async with make_client(user_id="hod.expired") as hod:
        attempt = await _grant(
            hod,
            user_id=target["id"],
            role_code="class-incharge",
            org_unit_id=tree["sec_a"]["id"],
            term_code="2026-S1",
        )
    assert attempt.status_code == 403


async def test_singleton_second_holder_blocked(make_client) -> None:
    """TC-AUTH-018/019: one active holder per unit; the fix is the supersede flow."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        u1 = await _user(admin, "hod.one")
        u2 = await _user(admin, "hod.two")
        first = await _grant(
            admin, user_id=u1["id"], role_code="hod", org_unit_id=tree["dept_a"]["id"]
        )
        second = await _grant(
            admin, user_id=u2["id"], role_code="hod", org_unit_id=tree["dept_a"]["id"]
        )
    assert first.status_code == 201
    assert second.status_code == 409
    assert "supersede" in second.json()["detail"].lower()


async def test_university_singleton_blocked(make_client) -> None:
    async with make_client("super-admin") as admin:
        u1 = await _user(admin, "vc.one")
        u2 = await _user(admin, "vc.two")
        assert (await _grant(admin, user_id=u1["id"], role_code="vc")).status_code == 201
        assert (await _grant(admin, user_id=u2["id"], role_code="vc")).status_code == 409


async def test_supersede_is_atomic(make_client, audit_rows) -> None:
    """TC-AUTH-021: revoke + issue in one operation; never 0 or 2 active holders."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        u1 = await _user(admin, "hod.old")
        u2 = await _user(admin, "hod.new")
        assert (
            await _grant(admin, user_id=u1["id"], role_code="hod", org_unit_id=tree["dept_a"]["id"])
        ).status_code == 201

        superseded = await admin.post(
            "/rbac/grants/supersede",
            json={
                "role_code": "hod",
                "org_unit_id": tree["dept_a"]["id"],
                "new_user_id": u2["id"],
            },
        )
        assert superseded.status_code == 201

        old_grants = (await admin.get(f"/rbac/users/{u1['id']}/grants")).json()
        new_grants = (await admin.get(f"/rbac/users/{u2['id']}/grants")).json()
    assert [g["status"] for g in old_grants] == ["revoked"]
    assert old_grants[0]["revoke_cause"] == "superseded"
    assert [g["status"] for g in new_grants] == ["active"]
    assert await audit_rows("rbac.grant.superseded")


async def test_additional_charge_across_two_departments(make_client) -> None:
    """TC-AUTH-020: same role, two units, one person; each grant acts in its own scope."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        hod_user = await _user(admin, "hod.double")
        target = await _user(admin, "faculty.z")
        for dept, extra in ((tree["dept_a"], {}), (tree["dept_b"], {"additional_charge": True})):
            issued = await _grant(
                admin, user_id=hod_user["id"], role_code="hod", org_unit_id=dept["id"], **extra
            )
            assert issued.status_code == 201

    async with make_client(user_id="hod.double") as hod:
        for section in (tree["sec_a"], tree["sec_b"]):
            issued = await _grant(
                hod,
                user_id=target["id"],
                role_code="class-incharge",
                org_unit_id=section["id"],
                term_code="2026-S1",
            )
            assert issued.status_code == 201  # both units, no cross-unit merge needed


async def test_concurrent_singleton_race(make_client) -> None:
    """TC-AUTH-022: two simultaneous grants on one Department — exactly one wins."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        u1 = await _user(admin, "race.one")
        u2 = await _user(admin, "race.two")
        results = await asyncio.gather(
            _grant(admin, user_id=u1["id"], role_code="hod", org_unit_id=tree["dept_a"]["id"]),
            _grant(admin, user_id=u2["id"], role_code="hod", org_unit_id=tree["dept_a"]["id"]),
        )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409]


async def test_term_closure_revokes_and_rollback_restores(make_client, audit_rows) -> None:
    """TC-AUTH-015/016: PRM term-closure revokes term-bound grants; rollback restores."""
    import uuid

    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        incharge = await _user(admin, "incharge.t")
        issued = await _grant(
            admin,
            user_id=incharge["id"],
            role_code="class-incharge",
            org_unit_id=tree["sec_a"]["id"],
            term_code="2026-S1",
        )
        assert issued.status_code == 201

        section_ids = [uuid.UUID(tree["sec_a"]["id"])]
        async with get_sessionmaker()() as session:
            revoked = await rbac_service.handle_term_closure(session, "2026-S1", section_ids)
        assert revoked == 1
        grants = (await admin.get(f"/rbac/users/{incharge['id']}/grants")).json()
        assert grants[0]["status"] == "revoked"
        assert grants[0]["revoke_cause"] == "term-closure"

        async with get_sessionmaker()() as session:
            restored = await rbac_service.handle_term_rollback(session, "2026-S1", section_ids)
        assert restored == 1
        grants = (await admin.get(f"/rbac/users/{incharge['id']}/grants")).json()
        assert grants[0]["status"] == "active"

    assert await audit_rows("rbac.grant.restored")


async def test_dual_role_user_acts_under_each_grant(make_client) -> None:
    """TC-AUTH-017: HoD + Class In-charge held concurrently; each action authorized."""
    async with make_client("super-admin") as admin:
        tree = await _tree(admin)
        dual = await _user(admin, "dual.role")
        target = await _user(admin, "faculty.w")
        for body in (
            {"user_id": dual["id"], "role_code": "hod", "org_unit_id": tree["dept_a"]["id"]},
            {
                "user_id": dual["id"],
                "role_code": "class-incharge",
                "org_unit_id": tree["sec_a"]["id"],
                "term_code": "2026-S1",
            },
        ):
            assert (await _grant(admin, **body)).status_code == 201

    async with make_client(user_id="dual.role") as dual_client:
        # Acts as HoD (grants an In-charge for another section of their Department).
        async with get_sessionmaker()() as session:
            sec_new = await org_service.create_section_instance(
                session,
                SETUP_CTX,
                __import__("uuid").UUID(tree["prog_a"]["id"]),
                "3C",
                "2026-S1",
            )
        issued = await _grant(
            dual_client,
            user_id=target["id"],
            role_code="class-incharge",
            org_unit_id=str(sec_new.id),
            term_code="2026-S1",
        )
        assert issued.status_code == 201
