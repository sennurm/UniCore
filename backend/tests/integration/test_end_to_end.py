"""The whole academic setup chain, asserted from every actor's point of view.

The `world` fixture in conftest.py has already walked the flow — org tree, term,
components, staff, subjects, offerings, venues, sections, students, elective
choices, grid, draft, approval, publish — through the HTTP API. These tests
check that what came out the far end is what each person should see.

MANDATORY (project rule): a new feature extends the world and adds its
assertion here. If a change cannot be exercised end to end, say so explicitly
rather than leaving this file untouched.
"""

from collections.abc import Callable

import httpx

from tests.integration.conftest import PERIODS, STAFF, TERM, username_of


async def _resolve(admin: httpx.AsyncClient, school_id: str, on: str) -> dict:
    """One date's answer from the calendar resolver."""
    response = await admin.get(
        f"/timetable/schools/{school_id}/calendar",
        params={"from_date": on, "to_date": on},
    )
    assert response.status_code == 200, response.text
    return response.json()[0]

# --- the setup chain itself ---------------------------------------------------


async def test_the_university_is_fully_set_up(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """One pass over the finished world: every stage left something behind."""
    async with make_client("super-admin") as admin:
        units = (await admin.get("/org/units", params={"limit": 2000})).json()
        subjects = (await admin.get("/org/subjects", params={"limit": 2000})).json()
        venues = (await admin.get("/org/venues", params={"limit": 2000})).json()
        directory = (await admin.get("/rbac/directory", params={"limit": 2000})).json()

    by_type: dict[str, int] = {}
    for unit in units:
        by_type[unit["type"]] = by_type.get(unit["type"], 0) + 1
    assert by_type == {
        "university": 1,
        "faculty_division": 2,
        "school": 2,
        "department": 2,
        "program": 2,
        "section": 2,
    }
    assert len(subjects) == 7
    assert {v["kind"] for v in venues} == {"classroom", "lab", "field"}
    assert len([p for p in directory if p["kind"] == "student"]) == 5
    # The test harness's own actors are staff too, so count the imported ones.
    imported = {username_of(emp) for emp, *_ in STAFF}
    assert {p["username"] for p in directory} >= imported


async def test_a_school_teaches_in_the_components_it_enabled(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """Nursing records field work and clinical postings; Engineering does not
    offer them at all, so its subjects cannot carry those hours."""
    async with make_client("super-admin") as admin:
        engineering = (
            await admin.get("/org/components", params={"school_id": world["school_SOCE"]})
        ).json()
        nursing = (
            await admin.get("/org/components", params={"school_id": world["school_SONUR"]})
        ).json()

        # An Engineering subject with clinical hours is refused rather than
        # silently recorded against a component the School does not teach.
        refused = await admin.post(
            "/org/subjects",
            json={
                "code": "CS999",
                "name": "Bedside Computing",
                "department_id": world["dept_CSE"],
                "kind": "core",
                "credits": 3,
                "hours": {"clinical": 4},
            },
        )
    assert {c["code"] for c in engineering if c["enabled"]} == {"theory", "lab"}
    assert {c["code"] for c in nursing if c["enabled"]} == {"theory", "clinical", "field_work"}
    assert refused.status_code == 422, refused.text
    assert "clinical" in refused.json()["detail"]


async def test_a_nursing_subject_carries_field_work_and_clinical_hours(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """The reason components exist: one subject taught two ways at once."""
    async with make_client("super-admin") as admin:
        subjects = (await admin.get("/org/subjects", params={"limit": 2000})).json()
    by_code = {s["code"]: s for s in subjects}
    assert by_code["NUR101"]["hours"] == {"theory": 2, "field_work": 4}
    assert by_code["NUR201"]["hours"] == {"theory": 2, "clinical": 8}
    assert by_code["CS201"]["hours"] == {"theory": 3, "lab": 2}


# --- what each actor sees -----------------------------------------------------


async def test_a_student_sees_their_own_published_week(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """Core subjects, plus the one elective they chose — never the alternative."""
    async with make_client("student", user_id="r0001") as student:
        mine = (await student.get("/timetable/me", params={"term_code": TERM})).json()

    assert mine["role"] == "student"
    assert mine["section_name"] == "1A"
    week = {(r["day_of_week"], r["period_name"]): r["subject_code"] for r in mine["rows"]}
    assert week == {
        (1, "P1"): "MA101",
        (1, "P2"): "CS201",
        (2, "P1"): "CS501",  # chosen
        (3, "P1"): "OE201",  # chosen, and owned by the Nursing School
    }
    assert "CS502" not in week.values(), "the elective they did not choose leaked in"


async def test_a_student_who_chose_nothing_sees_only_the_core_week(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """An unchosen elective is nobody's class — it must not appear by default."""
    async with make_client("student", user_id="r0002") as student:
        mine = (await student.get("/timetable/me", params={"term_code": TERM})).json()
    assert sorted(r["subject_code"] for r in mine["rows"]) == ["CS201", "MA101"]


async def test_a_faculty_member_sees_their_own_teaching_load(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """Sr Chitra teaches in both Schools — her load spans them, not one draft."""
    async with make_client("faculty", user_id=username_of("EMP-9003")) as teacher:
        mine = (await teacher.get("/timetable/me", params={"term_code": TERM})).json()
    assert mine["role"] == "faculty"
    assert sorted((r["day_of_week"], r["subject_code"]) for r in mine["rows"]) == [
        (1, "NUR101"),  # field work at the PHC
        (3, "OE201"),  # the Open elective, taught into the Engineering School
    ]


async def test_a_nursing_student_never_sees_the_engineering_week(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    async with make_client("student", user_id="r0004") as student:
        mine = (await student.get("/timetable/me", params={"term_code": TERM})).json()
    week = {(r["day_of_week"], r["period_name"]): r["subject_code"] for r in mine["rows"]}
    assert week == {
        (1, "P3"): "NUR101",  # field work at the PHC
        (2, "P3"): "NUR201",
        (7, "P1"): "NUR201",  # Sunday ward duty
    }


# --- the calendar (TTM-FR-26/27/28) -------------------------------------------


async def test_each_school_teaches_the_week_it_declared(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """The reason working days are a School attribute: on one campus, in one
    term, Nursing teaches Sunday and Engineering does not."""
    async with make_client("super-admin") as admin:
        # 06-09-2026 is a Sunday; 12-09-2026 is the 2nd Saturday of that month.
        nursing_sunday = await _resolve(admin, world["school_SONUR"], "2026-09-06")
        engineering_sunday = await _resolve(admin, world["school_SOCE"], "2026-09-06")
        first_saturday = await _resolve(admin, world["school_SOCE"], "2026-09-05")
        second_saturday = await _resolve(admin, world["school_SOCE"], "2026-09-12")

    assert nursing_sunday["teaching"] is True
    assert engineering_sunday["teaching"] is False
    # Alternate Saturdays, without a dated row per Saturday of the term.
    assert (first_saturday["teaching"], second_saturday["teaching"]) == (True, False)


async def test_a_school_works_through_a_university_holiday(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """Founder's Day closes the university; the Nursing ward does not close."""
    async with make_client("super-admin") as admin:
        engineering = await _resolve(admin, world["school_SOCE"], "2026-09-14")
        nursing = await _resolve(admin, world["school_SONUR"], "2026-09-14")

    assert (engineering["teaching"], engineering["decided_by"]) == (False, "university-holiday")
    assert (nursing["teaching"], nursing["decided_by"]) == (True, "school-override")
    assert "Founder's Day" in nursing["detail"]


async def test_a_compensatory_saturday_runs_mondays_timetable(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """19-09-2026 is a 3rd Saturday the School already teaches — but it runs
    Monday's schedule, which is what makes the lost day recoverable."""
    async with make_client("super-admin") as admin:
        made_up = await _resolve(admin, world["school_SOCE"], "2026-09-19")
    assert made_up["teaching"] is True
    assert made_up["effective_day_of_week"] == 1
    assert made_up["decided_by"] == "school-exception"


async def test_engineering_cannot_schedule_the_sunday_nursing_teaches(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """The authoring guard, in the world where the two Schools disagree."""
    async with make_client("super-admin") as admin:
        # The world's draft is published, so changes go through a new one.
        draft = await admin.post(
            "/timetable/drafts",
            json={"school_id": world["school_SOCE"], "term_code": TERM},
        )
        assert draft.status_code == 201, draft.text
        refused = await admin.post(
            f"/timetable/drafts/{draft.json()['id']}/entries",
            json={
                "section_id": world["section_BT-CSE"],
                "day_of_week": 7,
                "period_id": world["period_SOCE_P4"],
                "offering_id": world["offering_MA101"],
                "faculty_user_id": world["staff_EMP-9001"],
                "venue_id": world["venue_CR-101"],
            },
        )
    assert refused.status_code == 422, refused.text
    assert "does not teach Sunday" in refused.json()["detail"]


# --- the rules that hold the flow together ------------------------------------


async def test_a_room_cannot_be_double_booked_across_schools(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """Rooms are university-wide, so Nursing collides with a room Engineering
    already published into — the two Schools never have to speak."""
    async with make_client("super-admin") as admin:
        draft = await admin.post(
            "/timetable/drafts",
            json={"school_id": world["school_SONUR"], "term_code": TERM},
        )
        assert draft.status_code == 201, draft.text
        clash = await admin.post(
            f"/timetable/drafts/{draft.json()['id']}/entries",
            json={
                "section_id": world["section_BSC-NURS"],
                "day_of_week": 1,
                # CR-101 is taken Monday 09:00–10:00 by Engineering's MA101.
                "period_id": world["period_SONUR_P1"],
                "offering_id": world["offering_NUR101"],
                "faculty_user_id": world["staff_EMP-9003"],
                "venue_id": world["venue_CR-101"],
            },
        )
    assert clash.status_code == 409, clash.text
    kinds = [c["kind"] for c in clash.json()["detail"]["clashes"]]
    assert "venue" in kinds


async def test_an_open_elective_reaches_students_of_every_school(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """OE201 is owned by Nursing and offered once, with no Programme — an
    Engineering student and a Nursing student both see the same seat pool."""
    seen = {}
    for username in ("r0003", "r0005"):
        async with make_client("student", user_id=username) as student:
            groups = (
                await student.get("/onboarding/me/electives", params={"term_code": TERM})
            ).json()
        seen[username] = {g["elective_group"] for g in groups}
    assert "open" in seen["r0003"] and "open" in seen["r0005"]
    # Only Engineering runs professional electives, so only they see that group.
    assert "professional" in seen["r0003"]
    assert "professional" not in seen["r0005"]


async def test_the_last_seat_in_an_elective_goes_to_one_student(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """CS501 was offered with a single seat and r0001 took it during setup."""
    async with make_client("student", user_id="r0002") as student:
        refused = await student.post(
            "/onboarding/me/electives",
            json={"offering_id": world["offering_CS501"], "term_code": TERM},
        )
        options = (await student.get("/onboarding/me/electives", params={"term_code": TERM})).json()
    assert refused.status_code == 409, refused.text
    professional = next(g for g in options if g["elective_group"] == "professional")
    by_code = {o["subject_code"]: o for o in professional["options"]}
    assert by_code["CS501"]["seats_left"] == 0
    assert by_code["CS502"]["seats_left"] == 30


async def test_a_published_timetable_is_not_editable(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """People are already holding this week — changing it needs a new draft."""
    async with make_client("super-admin") as admin:
        blocked = await admin.post(
            f"/timetable/drafts/{world['draft_SOCE']}/entries",
            json={
                "section_id": world["section_BT-CSE"],
                "day_of_week": 4,
                "period_id": world["period_SOCE_P4"],
                "offering_id": world["offering_MA101"],
                "faculty_user_id": world["staff_EMP-9001"],
                "venue_id": world["venue_CR-101"],
            },
        )
    assert blocked.status_code == 409, blocked.text


async def test_the_grid_a_school_published_is_the_one_students_are_told(
    make_client: Callable[..., httpx.AsyncClient], world: dict
) -> None:
    """A period's clock time is what makes cross-School clashes detectable, so
    it must survive all the way to the student's screen."""
    async with make_client("student", user_id="r0001") as student:
        mine = (await student.get("/timetable/me", params={"term_code": TERM})).json()
    first = next(r for r in mine["rows"] if r["period_name"] == "P1")
    assert first["start_time"].startswith(PERIODS[0]["start_time"])
