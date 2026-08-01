"""User provisioning (AUTH-FR-01, business rule 1: reactivate — never duplicate)."""

STUDENT = {
    "username": "r.kumar",
    "full_name": "R. Kumar",
    "kind": "student",
    "sif_id": "SIF-000123",
}


async def test_admin_provisions_user(make_client) -> None:
    async with make_client("system-admin") as client:
        response = await client.post("/user", json=STUDENT)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active"
    assert "password_hash" not in body  # credential fields never leave the API


async def test_unprivileged_role_cannot_provision(make_client) -> None:
    async with make_client("class-incharge") as client:
        assert (await client.post("/user", json=STUDENT)).status_code == 403


async def test_duplicate_active_erp_id_conflicts(make_client) -> None:
    async with make_client("system-admin") as client:
        assert (await client.post("/user", json=STUDENT)).status_code == 201
        duplicate = await client.post("/user", json={**STUDENT, "username": "someone.else"})
    assert duplicate.status_code == 409


async def test_rejoining_user_is_reactivated_not_duplicated(make_client, audit_rows) -> None:
    async with make_client("system-admin") as client:
        created = (await client.post("/user", json=STUDENT)).json()
        deactivated = await client.post(f"/user/{created['id']}/deactivate")
        assert deactivated.json()["status"] == "deactivated"

        rejoined = await client.post("/user", json={**STUDENT, "full_name": "R. Kumar Jr"})
        assert rejoined.status_code == 201
        body = rejoined.json()
    assert body["id"] == created["id"]  # same account, never a duplicate
    assert body["status"] == "active"
    assert body["full_name"] == "R. Kumar Jr"
    assert await audit_rows("user.reactivated")
    assert await audit_rows("user.deactivated")
