"""Bulk org-structure CSV import + upload templates."""

import httpx


def _csv(rows: list[dict[str, str]], header: bool = True) -> bytes:
    from unicore.modules.org.schemas import ORG_CSV_COLUMNS

    lines = [",".join(ORG_CSV_COLUMNS)] if header else []
    lines += [",".join(r.get(c, "") for c in ORG_CSV_COLUMNS) for r in rows]
    return ("\n".join(lines) + "\n").encode()


async def _upload(client: httpx.AsyncClient, payload: bytes) -> httpx.Response:
    return await client.post(
        "/org/imports", files={"file": ("org.csv", payload, "text/csv")}
    )


async def test_builds_tree_regardless_of_row_order(make_client) -> None:
    """Children may appear before their parents — the importer sorts by depth."""
    async with make_client("super-admin") as admin:
        await admin.post("/org/units", json={"type": "university", "name": "U", "code": "UNI"})
        # Deliberately deepest-first.
        rows = [
            {"type": "program", "code": "BT-CSE", "name": "BTech CSE",
             "parent_path": "uni.fet.soce.cse"},
            {"type": "department", "code": "CSE", "name": "Computer Science",
             "parent_path": "uni.fet.soce"},
            {"type": "school", "code": "SOCE", "name": "School of Computing",
             "parent_path": "uni.fet"},
            {"type": "faculty_division", "code": "FET", "name": "Engineering & Tech",
             "parent_path": "uni"},
        ]
        result = (await _upload(admin, _csv(rows))).json()
        assert (result["rows_created"], result["rows_rejected"]) == (4, 0)

        root = (await admin.get("/org/root")).json()
        fet = (await admin.get(f"/org/units/{root['id']}/children")).json()[0]
        soce = (await admin.get(f"/org/units/{fet['id']}/children")).json()[0]
    assert fet["code"] == "FET"
    assert soce["path"] == "uni.fet.soce"


async def test_partial_commit_with_errors(make_client) -> None:
    async with make_client("super-admin") as admin:
        await admin.post("/org/units", json={"type": "university", "name": "U", "code": "UNI"})
        rows = [
            {"type": "faculty_division", "code": "FET", "name": "Eng", "parent_path": "uni"},
            {"type": "school", "code": "X", "name": "Nowhere", "parent_path": "uni.ghost"},
            {"type": "department", "code": "D", "name": "Wrong parent", "parent_path": "uni"},
            {"type": "section", "code": "3B", "name": "3B", "parent_path": "uni.fet"},
            {"type": "school", "code": "", "name": "No code", "parent_path": "uni.fet"},
        ]
        result = (await _upload(admin, _csv(rows))).json()
    assert result["rows_created"] == 1
    assert result["rows_rejected"] == 4
    reasons = {e["field"]: e["reason"] for e in result["errors"]}
    parent_reason = reasons["parent_path"]
    assert "no org unit at path" in parent_reason or "must sit under" in parent_reason
    assert "Timetable Cell" in reasons["type"]
    assert reasons["code"] == "mandatory field is missing"


async def test_reimport_is_idempotent(make_client) -> None:
    rows = [{"type": "faculty_division", "code": "FET", "name": "Eng", "parent_path": "uni"}]
    async with make_client("super-admin") as admin:
        await admin.post("/org/units", json={"type": "university", "name": "U", "code": "UNI"})
        first = (await _upload(admin, _csv(rows))).json()
        second = (await _upload(admin, _csv(rows))).json()
        renamed = (await _upload(
            admin, _csv([{**rows[0], "name": "Engineering and Technology"}])
        )).json()
    assert first["rows_created"] == 1
    assert second["rows_unchanged"] == 1 and second["rows_created"] == 0
    assert renamed["rows_updated"] == 1


async def test_import_is_super_admin_only(make_client) -> None:
    async with make_client("system-admin") as staff:
        denied = await _upload(staff, _csv([]))
    assert denied.status_code == 403


async def test_template_endpoints(make_client) -> None:
    """Templates are listed and downloadable, and match the live column tuples."""
    from unicore.modules.onboarding.schemas import CSV_COLUMNS_V1
    from unicore.modules.org.schemas import ORG_CSV_COLUMNS

    async with make_client("system-admin") as staff:
        listed = (await staff.get("/templates")).json()
        keys = {t["key"] for t in listed}
        assert {"org-structure", "students", "sections"} <= keys

        org = await staff.get("/templates/org-structure.csv")
        assert org.status_code == 200
        assert "attachment" in org.headers["content-disposition"]
        header_line = [ln for ln in org.text.splitlines() if not ln.startswith("#")][0]
        assert header_line == ",".join(ORG_CSV_COLUMNS)

        students = await staff.get("/templates/students.csv")
        student_header = [ln for ln in students.text.splitlines() if not ln.startswith("#")][0]
        assert student_header == ",".join(CSV_COLUMNS_V1)

        assert (await staff.get("/templates/nope.csv")).status_code == 404


async def test_downloaded_template_imports_cleanly(make_client) -> None:
    """The shipped template is a valid upload once its example row is real."""
    async with make_client("super-admin") as admin:
        await admin.post("/org/units", json={"type": "university", "name": "U", "code": "UNI"})
        root_id = (await admin.get("/org/root")).json()["id"]
        fet = (await admin.post(
            "/org/units",
            json={"type": "faculty_division", "name": "FET", "code": "FET", "parent_id": root_id},
        )).json()
        await admin.post(
            "/org/units",
            json={"type": "school", "name": "SOCE", "code": "SOCE", "parent_id": fet["id"]},
        )

        template = (await admin.get("/templates/org-structure.csv")).text
        result = (await _upload(admin, template.encode())).json()
    # The template's example row (a Department under UNI.FET.SOCE) applies cleanly.
    assert result["rows_created"] == 1
    assert result["rows_rejected"] == 0
