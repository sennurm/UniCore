"""Org-structure administration (AUTH-FR-19). Maps to TC-AUTH-010."""

import httpx


async def _create(client: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await client.post("/org/units", json=body)


async def test_hierarchy_creation_builds_ltree_paths(make_client) -> None:
    async with make_client("super-admin") as client:
        uni = (await _create(client, type="university", name="Test University", code="UNI")).json()
        fd = (
            await _create(
                client,
                type="faculty_division",
                name="Faculty of Engineering & Technology",
                code="FET",
                parent_id=uni["id"],
            )
        ).json()
        school = (
            await _create(
                client,
                type="school",
                name="School of Computational Engineering",
                code="SOCE",
                parent_id=fd["id"],
            )
        ).json()
    assert uni["path"] == "uni"
    assert fd["path"] == "uni.fet"
    assert school["path"] == "uni.fet.soce"
    assert school["status"] == "active"


async def test_org_crud_restricted_to_super_admin(make_client, audit_rows) -> None:
    """TC-AUTH-010: System Admin 403 (audited via log), Super Admin succeeds, no delete API."""
    async with make_client("system-admin") as client:
        denied = await _create(client, type="university", name="X", code="X1")
        assert denied.status_code == 403

    async with make_client("super-admin") as client:
        created = await _create(client, type="university", name="Test University", code="UNI")
        assert created.status_code == 201
        unit_id = created.json()["id"]

        # Deactivated units reject new children; reads still work.
        deactivated = await client.post(f"/org/units/{unit_id}/deactivate")
        assert deactivated.status_code == 200
        child = await _create(
            client, type="faculty_division", name="FD", code="FD1", parent_id=unit_id
        )
        assert child.status_code == 409
        assert (await client.get(f"/org/units/{unit_id}")).status_code == 200

        # Delete does not exist anywhere on the API surface.
        assert (await client.delete(f"/org/units/{unit_id}")).status_code == 405

    created_events = await audit_rows("org.unit.created")
    assert created_events and created_events[0]["actor"] == "test-actor"
    assert await audit_rows("org.unit.deactivated")


async def test_section_creation_rejected_here(make_client) -> None:
    async with make_client("super-admin") as client:
        uni = (await _create(client, type="university", name="U", code="UNI")).json()
        response = await _create(
            client, type="section", name="3B", code="S3B", parent_id=uni["id"]
        )
    assert response.status_code == 422
    assert "TTM" in response.json()["detail"]


async def test_wrong_parent_type_rejected(make_client) -> None:
    async with make_client("super-admin") as client:
        uni = (await _create(client, type="university", name="U", code="UNI")).json()
        response = await _create(
            client, type="department", name="CSE", code="CSE", parent_id=uni["id"]
        )
    assert response.status_code == 422


async def test_duplicate_sibling_code_conflicts(make_client) -> None:
    async with make_client("super-admin") as client:
        uni = (await _create(client, type="university", name="U", code="UNI")).json()
        first = await _create(
            client, type="faculty_division", name="A", code="FET", parent_id=uni["id"]
        )
        second = await _create(
            client, type="faculty_division", name="B", code="FET", parent_id=uni["id"]
        )
    assert first.status_code == 201
    assert second.status_code == 409


async def test_reparent_rewrites_subtree_paths(make_client) -> None:
    async with make_client("super-admin") as client:
        uni = (await _create(client, type="university", name="U", code="UNI")).json()
        fd = {"type": "faculty_division", "parent_id": uni["id"]}
        fd1 = (await _create(client, name="F1", code="F1", **fd)).json()
        fd2 = (await _create(client, name="F2", code="F2", **fd)).json()
        school = (
            await _create(client, type="school", name="S", code="S1", parent_id=fd1["id"])
        ).json()
        dept = (
            await _create(client, type="department", name="D", code="D1", parent_id=school["id"])
        ).json()
        assert dept["path"] == "uni.f1.s1.d1"

        moved = await client.post(
            f"/org/units/{school['id']}/reparent", json={"new_parent_id": fd2["id"]}
        )
        assert moved.status_code == 200
        assert moved.json()["path"] == "uni.f2.s1"
        dept_after = (await client.get(f"/org/units/{dept['id']}")).json()
        assert dept_after["path"] == "uni.f2.s1.d1"

        # Moving a unit into its own subtree is impossible.
        cycle = await client.post(
            f"/org/units/{fd2['id']}/reparent", json={"new_parent_id": school["id"]}
        )
        assert cycle.status_code == 422
