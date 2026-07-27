"""Reporting-chain resolution with holder/vacancy status (AUTH-FR-18)."""

import httpx


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


async def test_reporting_resolves_holder_and_vacancy(make_client) -> None:
    async with make_client("super-admin") as admin:
        uni = await _unit(admin, type="university", name="U", code="UNI")
        fd = await _unit(
            admin, type="faculty_division", name="F", code="F1", parent_id=uni["id"]
        )
        school = await _unit(admin, type="school", name="S", code="S1", parent_id=fd["id"])
        dept = await _unit(admin, type="department", name="D", code="D1", parent_id=school["id"])

        hod_user = await _user(admin, "hod.rep")
        school_incharge = await _user(admin, "si.rep")
        issued = await admin.post(
            "/rbac/grants",
            json={"user_id": hod_user["id"], "role_code": "hod", "org_unit_id": dept["id"]},
        )
        assert issued.status_code == 201

        # No School Incharge yet: the chain reports the vacancy explicitly.
        vacant = (await admin.get(f"/rbac/users/{hod_user['id']}/reporting")).json()
        assert vacant == [
            {"role": "hod", "reports_to": "school-incharge", "status": "vacant", "holders": []}
        ]

        issued = await admin.post(
            "/rbac/grants",
            json={
                "user_id": school_incharge["id"],
                "role_code": "school-incharge",
                "org_unit_id": school["id"],
            },
        )
        assert issued.status_code == 201
        active = (await admin.get(f"/rbac/users/{hod_user['id']}/reporting")).json()
    assert active[0]["status"] == "active"
    assert active[0]["holders"] == [school_incharge["id"]]
