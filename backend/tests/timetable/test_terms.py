"""Academic terms + Section instances (TTM-FR-18/19)."""

from datetime import date


async def _school(admin, suffix: str = "") -> dict[str, str]:
    """A University → … → Programme spine. `suffix` builds a second, disjoint School
    under the same University for cross-School isolation checks."""

    async def unit(**body: object) -> dict:
        response = await admin.post("/org/units", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    existing = await admin.get("/org/units", params={"unit_type": "university"})
    roots = existing.json()
    uni = roots[0] if roots else await unit(type="university", name="U", code="UNI")
    fd = await unit(
        type="faculty_division", name=f"FET{suffix}", code=f"FET{suffix}", parent_id=uni["id"]
    )
    school = await unit(
        cadence="semester",
        type="school",
        name=f"SOCE{suffix}",
        code=f"SOCE{suffix}",
        parent_id=fd["id"],
    )
    dept = await unit(
        type="department", name=f"CSE{suffix}", code=f"CSE{suffix}", parent_id=school["id"]
    )
    program = await unit(
        type="program", name=f"BTech{suffix}", code=f"BT{suffix}", parent_id=dept["id"]
    )
    return {"school": school["id"], "dept": dept["id"], "program": program["id"]}


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


async def test_section_plan_pairs_programmes_with_their_sections(make_client) -> None:
    """The term-setup screen needs Programmes *without* Sections too — that gap is
    the whole point of the screen, and a list of Sections cannot show it."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        empty = await admin.post(
            "/org/units",
            json={"type": "program", "name": "MTech", "code": "MT", "parent_id": ids["dept"]},
        )
        term = await admin.post("/timetable/terms", json={**TERM, "school_id": ids["school"]})
        await admin.post(f"/timetable/terms/{term.json()['id']}/approve")
        for label in ("3B", "3A"):
            await admin.post(
                "/timetable/sections",
                json={"program_id": ids["program"], "label": label, "term_code": "2026-S1"},
            )

        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/section-plan", params={"term_code": "2026-S1"}
        )
        assert plan.status_code == 200
        rows = {r["programme"]["code"]: [s["name"] for s in r["sections"]] for r in plan.json()}
        assert rows == {"BT": ["3A", "3B"], "MT": []}  # sorted by label; empty Programme kept

        # A different term shares the Programmes but none of the Sections.
        other = await admin.get(
            f"/timetable/schools/{ids['school']}/section-plan", params={"term_code": "2027-S1"}
        )
        assert {r["programme"]["code"]: r["sections"] for r in other.json()} == {"BT": [], "MT": []}

        assert empty.status_code == 201


async def test_section_plan_covers_only_the_requested_school(make_client) -> None:
    async with make_client("super-admin") as admin:
        mine = await _school(admin)
        theirs = await _school(admin, suffix="2")
        for ids in (mine, theirs):
            term = await admin.post("/timetable/terms", json={**TERM, "school_id": ids["school"]})
            await admin.post(f"/timetable/terms/{term.json()['id']}/approve")
            await admin.post(
                "/timetable/sections",
                json={"program_id": ids["program"], "label": "1A", "term_code": "2026-S1"},
            )

        plan = await admin.get(
            f"/timetable/schools/{mine['school']}/section-plan", params={"term_code": "2026-S1"}
        )
        programmes = {r["programme"]["id"] for r in plan.json()}
    assert programmes == {mine["program"]}, "another School's Programmes leaked into the plan"


async def test_section_plan_rejects_a_non_school(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        response = await admin.get(
            f"/timetable/schools/{ids['program']}/section-plan", params={"term_code": "2026-S1"}
        )
    assert response.status_code == 422


async def test_all_terms_listing_spans_schools(make_client) -> None:
    async with make_client("super-admin") as admin:
        first = await _school(admin)
        second = await _school(admin, suffix="2")
        for ids in (first, second):
            await admin.post("/timetable/terms", json={**TERM, "school_id": ids["school"]})

        terms = await admin.get("/timetable/terms")
        assert terms.status_code == 200
        schools = {t["school_id"] for t in terms.json()}
    assert schools == {first["school"], second["school"]}


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
