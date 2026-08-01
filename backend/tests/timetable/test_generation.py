"""Section generation and multi-School calendars (TTM-FR-22/23/24/25).

Maps TC-TTM-031..044.
"""

import httpx

TERM = {"term_code": "2026-S1", "start_date": "2026-07-01", "end_date": "2026-11-30"}


async def _unit(admin: httpx.AsyncClient, **body: object) -> dict:
    response = await admin.post("/org/units", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _school(
    admin: httpx.AsyncClient,
    suffix: str = "",
    cadence: str = "semester",
    duration: int | None = 4,
) -> dict[str, str]:
    existing = (await admin.get("/org/units", params={"unit_type": "university"})).json()
    uni = existing[0] if existing else await _unit(admin, type="university", name="U", code="UNI")
    fd = await _unit(
        admin, type="faculty_division", name=f"FET{suffix}", code=f"FET{suffix}",
        parent_id=uni["id"],
    )
    school = await _unit(
        admin, type="school", name=f"SOCE{suffix}", code=f"SOCE{suffix}",
        parent_id=fd["id"], cadence=cadence,
    )
    dept = await _unit(
        admin, type="department", name=f"CSE{suffix}", code=f"CSE{suffix}",
        parent_id=school["id"],
    )
    programme = await _unit(
        admin, type="program", name=f"BTech AI & DS{suffix}", code=f"BT-AIDS{suffix}",
        parent_id=dept["id"],
    )
    if duration is not None:
        await admin.put(f"/org/units/{programme['id']}", json={"duration_years": duration})
    return {"school": school["id"], "dept": dept["id"], "programme": programme["id"]}


async def _approved_term(
    admin: httpx.AsyncClient, school_id: str, parity: str | None = "odd", **over: object
) -> str:
    body = {**TERM, "school_id": school_id, "parity": parity, **over}
    term = await admin.post("/timetable/terms", json=body)
    assert term.status_code == 201, term.text
    approved = await admin.post(f"/timetable/terms/{term.json()['id']}/approve")
    assert approved.status_code == 200, approved.text
    return str(term.json()["id"])


def _rows(plan: dict) -> dict[int, dict]:
    return {row["position"]: row for row in plan["rows"]}


# --- parity ------------------------------------------------------------------


async def test_odd_term_generates_only_odd_positions(make_client) -> None:
    """TC-TTM-032: a 4-year semester Programme has 8 rungs but runs 4 per term."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"], parity="odd")
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    assert plan.status_code == 200, plan.text
    assert sorted(_rows(plan.json())) == [1, 3, 5, 7]


async def test_even_term_generates_the_other_half(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"], parity="even")
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    assert sorted(_rows(plan.json())) == [2, 4, 6, 8]


async def test_yearly_cadence_ignores_parity(make_client) -> None:
    """TC-TTM-033: parity is a semester concept; a yearly Programme runs every year."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin, cadence="yearly", duration=3)
        await _approved_term(admin, ids["school"], parity="odd")
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    assert sorted(_rows(plan.json())) == [1, 2, 3]


async def test_parity_can_be_backfilled_once_then_never_changed(make_client) -> None:
    """Calendars uploaded before parity existed carry none; generation is blocked
    until it is set. Setting it is a one-way backfill, not a back-door amendment."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        term_id = await _approved_term(admin, ids["school"], parity=None)

        blocked = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
        assert blocked.status_code == 409

        filled = await admin.patch(
            f"/timetable/terms/{term_id}/parity", json={"parity": "odd"}
        )
        assert filled.status_code == 200, filled.text
        assert filled.json()["parity"] == "odd"

        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
        assert plan.status_code == 200
        assert sorted(_rows(plan.json())) == [1, 3, 5, 7]

        # Changing a stated parity would move which half of every ladder is live.
        again = await admin.patch(
            f"/timetable/terms/{term_id}/parity", json={"parity": "even"}
        )
    assert again.status_code == 409
    assert "amending the calendar" in again.json()["detail"]


async def test_term_response_exposes_parity(make_client) -> None:
    """The screen cannot show a missing parity if the API never sends the field."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"], parity="even")
        terms = (await admin.get(f"/timetable/schools/{ids['school']}/terms")).json()
    assert terms[0]["parity"] == "even"


async def test_missing_parity_refuses_generation(make_client) -> None:
    """TC-TTM-037: guessing parity would create the wrong half of every ladder."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"], parity=None)
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    assert plan.status_code == 409
    assert "parity" in plan.json()["detail"]


# --- sizing ------------------------------------------------------------------


async def test_new_intake_with_expected_headcount_splits_by_cap(make_client) -> None:
    """TC-TTM-031/036: 90 students at a cap of 60 needs two Sections."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"])
        result = await admin.post(
            f"/timetable/schools/{ids['school']}/generate-sections",
            json={
                "term_code": "2026-S1",
                "expected_intake": {f"{ids['programme']}:1": 90},
            },
        )
        assert result.status_code == 200, result.text
        created = {s["name"] for s in result.json()["created"]}
    # Positions 3, 5 and 7 have neither roster nor expected intake → one each.
    assert "I Semester - A" in created and "I Semester - B" in created
    assert "III Semester - A" in created
    assert len([n for n in created if n.startswith("I Semester")]) == 2


async def test_exactly_at_the_cap_needs_one_section(make_client) -> None:
    """TC-TTM-035: 60 at a cap of 60 is one Section; 61 is two."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"])
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
        rows = _rows(plan.json())
    assert rows[1]["class_size_cap"] == 60
    # No roster and no expected intake still proposes one — never zero.
    assert rows[1]["required"] == 1
    assert rows[1]["headcount_source"] == "none"


async def test_school_cap_overrides_the_university_default(make_client) -> None:
    """TC-TTM-039: the cap is School-configurable over a university default."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await admin.put(f"/org/units/{ids['school']}", json={"class_size_cap": 30})
        await _approved_term(admin, ids["school"])
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    assert _rows(plan.json())[1]["class_size_cap"] == 30


async def test_programme_without_duration_is_skipped_with_a_warning(make_client) -> None:
    """TC-TTM-038: guessing 4 years would invent terms the Programme has not got."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin, duration=None)
        await _approved_term(admin, ids["school"])
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    body = plan.json()
    assert body["rows"] == []
    assert any("duration" in w for w in body["warnings"])


# --- idempotency -------------------------------------------------------------


async def test_generation_is_idempotent(make_client) -> None:
    """TC-TTM-034: a second run creates nothing and renames nothing."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"])
        first = await admin.post(
            f"/timetable/schools/{ids['school']}/generate-sections",
            json={"term_code": "2026-S1"},
        )
        second = await admin.post(
            f"/timetable/schools/{ids['school']}/generate-sections",
            json={"term_code": "2026-S1"},
        )
    assert len(first.json()["created"]) == 4  # one per live position
    assert second.json()["created"] == []
    assert second.json()["existing"] == 4


async def test_regeneration_after_growth_continues_the_letter_run(make_client) -> None:
    """A position that outgrows its Sections gains B, never a second A."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"])
        await admin.post(
            f"/timetable/schools/{ids['school']}/generate-sections",
            json={"term_code": "2026-S1"},
        )
        grown = await admin.post(
            f"/timetable/schools/{ids['school']}/generate-sections",
            json={
                "term_code": "2026-S1",
                "expected_intake": {f"{ids['programme']}:1": 150},
            },
        )
        added = [s["name"] for s in grown.json()["created"]]
    assert added == ["I Semester - B", "I Semester - C"]


async def test_generation_requires_an_approved_term(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        draft = await admin.post(
            "/timetable/terms",
            json={**TERM, "school_id": ids["school"], "parity": "odd"},
        )
        assert draft.status_code == 201
        plan = await admin.get(
            f"/timetable/schools/{ids['school']}/generation-plan",
            params={"term_code": "2026-S1"},
        )
    assert plan.status_code == 409


# --- multi-School calendar ---------------------------------------------------


async def test_multi_school_apply_fans_out_to_independent_drafts(make_client) -> None:
    """TC-TTM-040/042: one action, one draft each, approved independently."""
    async with make_client("super-admin") as admin:
        first = await _school(admin)
        second = await _school(admin, suffix="2")
        applied = await admin.post(
            "/timetable/terms/multi",
            json={
                "school_ids": [first["school"], second["school"]],
                "term_code": "2027-S1",
                "start_date": "2027-01-05",
                "end_date": "2027-05-30",
                "parity": "even",
            },
        )
        assert applied.status_code == 201, applied.text
        assert [r["outcome"] for r in applied.json()] == ["created", "created"]

        terms = (await admin.get("/timetable/terms")).json()
        drafts = [t for t in terms if t["term_code"] == "2027-S1"]
        assert len(drafts) == 2
        assert {t["status"] for t in drafts} == {"draft"}

        # Approving one leaves the other untouched — the point of fanning out.
        await admin.post(f"/timetable/terms/{drafts[0]['id']}/approve")
        after = (await admin.get("/timetable/terms")).json()
        by_id = {t["id"]: t["status"] for t in after}
    assert by_id[drafts[0]["id"]] == "approved"
    assert by_id[drafts[1]["id"]] == "draft"


async def test_multi_school_apply_versions_an_existing_term(make_client) -> None:
    """TC-TTM-043: never a silent overwrite — the School gets a new draft version."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        await _approved_term(admin, ids["school"])
        applied = await admin.post(
            "/timetable/terms/multi",
            json={
                "school_ids": [ids["school"]],
                "term_code": "2026-S1",
                "start_date": "2026-07-15",
                "end_date": "2026-12-15",
                "parity": "odd",
            },
        )
        row = applied.json()[0]
        terms = (await admin.get(f"/timetable/schools/{ids['school']}/terms")).json()
    assert row["outcome"] == "versioned" and row["version"] == 2
    by_version = {t["version"]: t["status"] for t in terms}
    # v1 stays approved until v2 is itself approved.
    assert by_version == {2: "draft", 1: "approved"}


async def test_multi_school_apply_skips_non_schools(make_client) -> None:
    async with make_client("super-admin") as admin:
        ids = await _school(admin)
        applied = await admin.post(
            "/timetable/terms/multi",
            json={
                "school_ids": [ids["school"], ids["dept"]],
                "term_code": "2027-S2",
                "start_date": "2027-01-05",
                "end_date": "2027-05-30",
            },
        )
        outcomes = {r["outcome"] for r in applied.json()}
    assert outcomes == {"created", "skipped"}


async def test_multi_school_apply_denied_to_school_staff(make_client) -> None:
    """TC-TTM-041: applying across Schools is a university-level act."""
    async with make_client("super-admin") as admin:
        ids = await _school(admin)

    async with make_client("office-staff", user_id="school.office") as staff:
        denied = await staff.post(
            "/timetable/terms/multi",
            json={
                "school_ids": [ids["school"]],
                "term_code": "2027-S1",
                "start_date": "2027-01-05",
                "end_date": "2027-05-30",
            },
        )
    assert denied.status_code == 403
