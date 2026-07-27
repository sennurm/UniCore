"""DPDP grievance flow (AUTH-FR-10). TC-AUTH-011: erasure cites the statutory exemption."""


async def test_erasure_grievance_cites_statutory_exemption(make_client, audit_rows) -> None:
    async with make_client(user_id="stud.grv") as student:
        filed = await student.post(
            "/user/me/grievances",
            json={"kind": "erasure", "details": "Please erase my promotion record."},
        )
        assert filed.status_code == 201
        grievance_id = filed.json()["id"]

    async with make_client("system-admin") as admin:
        listed = await admin.get("/user/grievances/open")
        assert any(g["id"] == grievance_id for g in listed.json())
        resolved = await admin.post(
            f"/user/grievances/{grievance_id}/resolve",
            json={"response": "We reviewed your request."},
        )
        assert resolved.status_code == 200

    async with make_client(user_id="stud.grv") as student:
        mine = (await student.get("/user/me/grievances")).json()
    assert mine[0]["status"] == "resolved"
    # Never a silent refusal: the statutory retention exemption is stated.
    assert "statutory" in mine[0]["response"].lower()
    assert await audit_rows("user.grievance.resolved")


async def test_peers_cannot_see_each_others_grievances(make_client) -> None:
    async with make_client(user_id="stud.one") as one:
        await one.post(
            "/user/me/grievances",
            json={"kind": "correction", "details": "Fix my phone number."},
        )
    async with make_client(user_id="stud.two") as two:
        assert (await two.get("/user/me/grievances")).json() == []
        assert (await two.get("/user/grievances/open")).status_code == 403
