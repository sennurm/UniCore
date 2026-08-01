"""Combined Users & roles directory + CSV role round-trip."""

import httpx


async def _tree(admin: httpx.AsyncClient) -> dict[str, str]:
    async def unit(**body: object) -> dict:
        response = await admin.post("/org/units", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    uni = await unit(type="university", name="U", code="UNI")
    fd = await unit(type="faculty_division", name="FET", code="FET", parent_id=uni["id"])
    school = await unit(
        cadence="semester", type="school", name="SOCE", code="SOCE", parent_id=fd["id"]
    )
    dept = await unit(type="department", name="CSE", code="CSE", parent_id=school["id"])
    return {"school": school["id"], "dept": dept["id"], "dept_path": dept["path"]}


async def _user(admin: httpx.AsyncClient, username: str) -> dict:
    response = await admin.post(
        "/user", json={"username": username, "full_name": username.title(), "kind": "staff"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_directory_lists_users_with_roles_and_filters(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _tree(admin)
        hod = await _user(admin, "meera.hod")
        plain = await _user(admin, "no.roles")
        await admin.post(
            "/rbac/grants",
            json={"user_id": hod["id"], "role_code": "hod", "org_unit_id": ids["dept"]},
        )

        everyone = (await admin.get("/rbac/directory")).json()
        by_username = {r["username"]: r for r in everyone}
        assert by_username["meera.hod"]["roles"][0]["spec"] == f"hod@{ids['dept_path']}"
        assert by_username["no.roles"]["roles"] == []

        only_hods = (await admin.get("/rbac/directory", params={"role_code": "hod"})).json()
        assert [r["username"] for r in only_hods] == ["meera.hod"]

        searched = (await admin.get("/rbac/directory", params={"search": "meera"})).json()
        assert [r["username"] for r in searched] == ["meera.hod"]
    assert plain["id"]


async def test_csv_round_trip_grants_and_revokes(make_client, audit_rows) -> None:
    """Download, edit the roles column, upload back: additions grant, removals revoke."""
    async with make_client("super-admin") as admin:
        ids = await _tree(admin)
        await _user(admin, "alice.staff")
        bob = await _user(admin, "bob.staff")
        await admin.post(
            "/rbac/grants",
            json={"user_id": bob["id"], "role_code": "hod", "org_unit_id": ids["dept"]},
        )

        export = await admin.get("/rbac/directory.csv")
        assert export.status_code == 200
        assert "attachment" in export.headers["content-disposition"]

        school_path = (await admin.get(f"/org/units/{ids['school']}")).json()["path"]

        # Edit the roles column the way a spreadsheet would: give alice a scoped
        # school-incharge role, clear bob's.
        import csv as _csv
        import io as _io

        body = "\n".join(ln for ln in export.text.splitlines() if not ln.lstrip().startswith("#"))
        rows = list(_csv.DictReader(_io.StringIO(body)))
        for row in rows:
            if row["username"] == "alice.staff":
                row["roles"] = f"school-incharge@{school_path}"
            elif row["username"] == "bob.staff":
                row["roles"] = ""
        out = _io.StringIO()
        writer = _csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

        result = (
            await admin.post(
                "/rbac/directory/imports",
                files={"file": ("roles.csv", out.getvalue().encode(), "text/csv")},
            )
        ).json()
        assert result["rows_rejected"] == 0, result["errors"]
        assert result["grants_issued"] == 1
        assert result["grants_revoked"] == 1

        directory = {r["username"]: r for r in (await admin.get("/rbac/directory")).json()}
    assert [e["role_code"] for e in directory["alice.staff"]["roles"]] == ["school-incharge"]
    assert directory["bob.staff"]["roles"] == []
    assert await audit_rows("rbac.grant.issued")
    assert await audit_rows("rbac.grant.revoked")


async def test_csv_errors_are_reported_per_row(make_client) -> None:
    """Unknown users, unknown roles and bad paths fail their row, not the batch."""
    async with make_client("super-admin") as admin:
        await _tree(admin)
        await _user(admin, "carol.staff")
        payload = (
            "username,full_name,kind,status,roles\n"
            "carol.staff,Carol,staff,active,system-admin\n"
            "ghost.user,Ghost,staff,active,system-admin\n"
            "carol.staff,Carol,staff,active,not-a-role\n"
            "carol.staff,Carol,staff,active,hod@uni.nowhere\n"
        )
        result = (
            await admin.post(
                "/rbac/directory/imports",
                files={"file": ("roles.csv", payload.encode(), "text/csv")},
            )
        ).json()
    assert result["grants_issued"] == 1  # carol gets system-admin
    assert result["rows_rejected"] == 3
    reasons = " ".join(str(e["reason"]) for e in result["errors"])
    assert "ghost.user" in str(result["errors"])
    assert "not-a-role" in reasons or "Unknown role" in reasons
    assert "uni.nowhere" in reasons


async def test_bulk_roles_requires_permission(make_client) -> None:
    async with make_client(user_id="random.person") as nobody:
        denied = await nobody.post(
            "/rbac/directory/imports",
            files={"file": ("roles.csv", b"username,roles\nx,y\n", "text/csv")},
        )
    assert denied.status_code == 403
