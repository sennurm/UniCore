"""Academic terms + Section instances (TTM-FR-18/19)."""

from datetime import date


async def _school(admin) -> dict[str, str]:
    async def unit(**body: object) -> dict:
        response = await admin.post("/org/units", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    uni = await unit(type="university", name="U", code="UNI")
    fd = await unit(type="faculty_division", name="FET", code="FET", parent_id=uni["id"])
    school = await unit(type="school", name="SOCE", code="SOCE", parent_id=fd["id"])
    dept = await unit(type="department", name="CSE", code="CSE", parent_id=school["id"])
    program = await unit(type="program", name="BTech", code="BT", parent_id=dept["id"])
    return {"school": school["id"], "program": program["id"]}


TERM = {
    "term_code": "2026-S1",
    "start_date": "2026-07-01",
    "end_date": "2026-11-30",
    "exam_ranges": [{"from": "2026-11-10", "to": "2026-11-25", "label": "End sem"}],
}


async def test_term_requires_approval_before_sections_exist(make_client, audit_rows) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        term = await admin.post("/timetable/terms", json={**TERM, "school_id": ids["school"]})
        assert term.status_code == 201
        assert term.json()["status"] == "draft"

        # A draft calendar cannot yet anchor Sections.
        early = await admin.post(
            "/timetable/sections",
            json={"program_id": ids["program"], "label": "3A", "term_code": "2026-S1"},
        )
        assert early.status_code == 409
        assert "approved academic term" in early.json()["detail"]

        approved = await admin.post(f"/timetable/terms/{term.json()['id']}/approve")
        assert approved.status_code == 200
        assert approved.json()["approved_by"]

        section = await admin.post(
            "/timetable/sections",
            json={"program_id": ids["program"], "label": "3A", "term_code": "2026-S1"},
        )
        assert section.status_code == 201
        assert section.json()["term_code"] == "2026-S1"
    assert await audit_rows("ttm.term.approved")


async def test_amendment_supersedes_previous_version(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        first = await admin.post("/timetable/terms", json={**TERM, "school_id": ids["school"]})
        await admin.post(f"/timetable/terms/{first.json()['id']}/approve")

        amended = await admin.post(
            "/timetable/terms",
            json={**TERM, "school_id": ids["school"], "end_date": "2026-12-15"},
        )
        assert amended.json()["version"] == 2
        await admin.post(f"/timetable/terms/{amended.json()['id']}/approve")

        terms = (await admin.get(f"/timetable/schools/{ids['school']}/terms")).json()
    by_version = {t["version"]: t["status"] for t in terms}
    assert by_version == {2: "approved", 1: "superseded"}


async def test_section_creation_restricted(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        term = await admin.post("/timetable/terms", json={**TERM, "school_id": ids["school"]})
        await admin.post(f"/timetable/terms/{term.json()['id']}/approve")

    async with make_client(user_id="random.person") as nobody:
        denied = await nobody.post(
            "/timetable/sections",
            json={"program_id": ids["program"], "label": "3A", "term_code": "2026-S1"},
        )
    assert denied.status_code == 403


async def test_term_dates_validated(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        bad = await admin.post(
            "/timetable/terms",
            json={
                "school_id": ids["school"],
                "term_code": "2026-S2",
                "start_date": "2026-11-30",
                "end_date": str(date(2026, 7, 1)),
            },
        )
    assert bad.status_code == 422
