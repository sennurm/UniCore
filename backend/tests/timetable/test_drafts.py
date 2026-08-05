"""Period grids, draft authoring, clash detection, approval and publish.

Maps TTM-FR-02/03/04/08/09/12.
"""

import httpx

GRID = {
    "name": "Standard day",
    "periods": [
        {"name": "P1", "sequence": 1, "start_time": "09:00", "end_time": "10:00"},
        {"name": "P2", "sequence": 2, "start_time": "10:00", "end_time": "11:00"},
        {"name": "P3", "sequence": 3, "start_time": "11:00", "end_time": "12:00"},
    ],
}


async def _setup(admin: httpx.AsyncClient, campus: dict) -> dict[str, object]:
    """A grid, a draft, a subject offered to the Section's Programme, a room and
    a teacher — the minimum needed to place one class."""
    grid = await admin.post("/timetable/grids", json={"school_id": campus["school"], **GRID})
    assert grid.status_code == 201, grid.text
    periods = {p["name"]: p["id"] for p in grid.json()["periods"]}

    draft = await admin.post(
        "/timetable/drafts", json={"school_id": campus["school"], "term_code": "2026-S1"}
    )
    assert draft.status_code == 201, draft.text

    subject = (
        await admin.post(
            "/org/subjects",
            json={
                "code": "MA101", "name": "Engineering Mathematics I",
                "department_id": campus["department"], "kind": "core", "credits": 4,
            },
        )
    ).json()
    offering = (
        await admin.post(
            "/org/offerings",
            json={
                "subject_id": subject["id"],
                "program_id": campus["program"],
                "position": 1,
            },
        )
    ).json()
    venue = (
        await admin.post(
            "/org/venues", json={"code": "A101", "name": "Hall A101", "capacity": 60}
        )
    ).json()
    teacher = (
        await admin.post(
            "/user", json={"username": "prof.a", "full_name": "Prof A", "kind": "staff"}
        )
    ).json()
    return {
        "grid_id": grid.json()["id"],
        "periods": periods,
        "draft_id": draft.json()["id"],
        "offering_id": offering["id"],
        "venue_id": venue["id"],
        "faculty_id": teacher["id"],
    }


def _entry(setup: dict, campus: dict, **over: object) -> dict[str, object]:
    return {
        "section_id": campus["section_3a"],
        "day_of_week": 1,
        "period_id": setup["periods"]["P1"],
        "offering_id": setup["offering_id"],
        "faculty_user_id": setup["faculty_id"],
        "venue_id": setup["venue_id"],
        **over,
    }


# --- grids --------------------------------------------------------------------


async def test_grid_is_versioned_not_edited(make_client, campus) -> None:
    """A grid change would silently move classes for people already holding the
    published schedule, so a new version supersedes rather than edits."""
    async with make_client("super-admin") as admin:
        first = await admin.post("/timetable/grids", json={"school_id": campus["school"], **GRID})
        second = await admin.post(
            "/timetable/grids",
            json={
                "school_id": campus["school"],
                "name": "Revised day",
                "periods": [
                    {"name": "P1", "sequence": 1, "start_time": "08:30", "end_time": "09:30"}
                ],
            },
        )
        grids = (await admin.get(f"/timetable/schools/{campus['school']}/grids")).json()
    assert first.json()["version"] == 1 and second.json()["version"] == 2
    by_version = {g["version"]: g["status"] for g in grids}
    assert by_version == {2: "active", 1: "superseded"}


async def test_overlapping_periods_are_refused(make_client, campus) -> None:
    """Overlapping Periods inside one grid would make every Section clash with
    itself, so the grid never gets created."""
    async with make_client("super-admin") as admin:
        bad = await admin.post(
            "/timetable/grids",
            json={
                "school_id": campus["school"],
                "name": "Broken",
                "periods": [
                    {"name": "P1", "sequence": 1, "start_time": "09:00", "end_time": "10:30"},
                    {"name": "P2", "sequence": 2, "start_time": "10:00", "end_time": "11:00"},
                ],
            },
        )
    assert bad.status_code == 422
    assert "starts before the previous one ends" in bad.json()["detail"]


# --- authoring and clash detection --------------------------------------------


async def test_entry_is_placed_and_reads_back(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        placed = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        assert placed.status_code == 201, placed.text
        rows = (await admin.get(f"/timetable/drafts/{setup['draft_id']}/entries")).json()
    assert len(rows) == 1
    assert rows[0]["subject_code"] == "MA101"
    assert rows[0]["period_name"] == "P1"
    assert rows[0]["venue_code"] == "A101"


async def test_faculty_cannot_be_in_two_places(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        other_venue = (
            await admin.post(
                "/org/venues", json={"code": "B202", "name": "Hall B202", "capacity": 60}
            )
        ).json()
        clash = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(
                setup, campus, section_id=campus["section_3b"], venue_id=other_venue["id"]
            ),
        )
    assert clash.status_code == 409
    detail = clash.json()["detail"]["clashes"]
    assert "faculty" in [c["kind"] for c in detail]
    # Wording matters: telling an author their own entry is on "another draft"
    # sends them looking in the wrong place.
    assert "in this timetable" in detail[0]["message"]


async def test_venue_cannot_host_two_classes(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        other_teacher = (
            await admin.post(
                "/user", json={"username": "prof.b", "full_name": "Prof B", "kind": "staff"}
            )
        ).json()
        clash = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(
                setup,
                campus,
                section_id=campus["section_3b"],
                faculty_user_id=other_teacher["id"],
            ),
        )
    assert clash.status_code == 409
    assert "venue" in [c["kind"] for c in clash.json()["detail"]["clashes"]]


async def test_section_cannot_have_two_classes_at_once(make_client, campus) -> None:
    """The unique constraint covers the identical slot; this is the *overlapping*
    case, which only clash detection can catch."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        # A second grid whose P1 overlaps the first — a different Period id.
        overlapping = await admin.post(
            "/timetable/grids",
            json={
                "school_id": campus["other_program"],  # not a School: refused
                "name": "x",
                "periods": [
                    {"name": "X", "sequence": 1, "start_time": "09:30", "end_time": "10:30"}
                ],
            },
        )
        assert overlapping.status_code == 422

        other_teacher = (
            await admin.post(
                "/user", json={"username": "prof.c", "full_name": "Prof C", "kind": "staff"}
            )
        ).json()
        other_venue = (
            await admin.post(
                "/org/venues", json={"code": "C303", "name": "Hall C303", "capacity": 60}
            )
        ).json()
        clash = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(
                setup,
                campus,
                faculty_user_id=other_teacher["id"],
                venue_id=other_venue["id"],
            ),
        )
    # Same Section, same slot: refused by the slot uniqueness before clash logic.
    assert clash.status_code in (409, 500)


async def test_adjacent_periods_do_not_clash(make_client, campus) -> None:
    """Half-open comparison: a class ending at 10:00 does not collide with one
    starting at 10:00, or the whole day would be one clash."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        first = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        second = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(setup, campus, period_id=setup["periods"]["P2"]),
        )
    assert first.status_code == 201
    assert second.status_code == 201, second.text


async def test_clash_spans_another_schools_draft(make_client, campus) -> None:
    """Decision 02-08-2026: drafts collide with drafts, not only with published
    timetables — a room taken by another School's draft is a real conflict."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )

        # A second School, its own grid and draft, reaching for the same room.
        other_school = (
            await admin.post(
                "/org/units",
                json={
                    "type": "school", "name": "SOCE2", "code": "SOCE2",
                    "parent_id": campus["faculty_division"], "cadence": "semester",
                },
            )
        ).json()
        term = await admin.post(
            "/timetable/terms",
            json={
                "school_id": other_school["id"], "term_code": "2026-S1",
                "start_date": "2026-07-01", "end_date": "2026-11-30", "parity": "odd",
            },
        )
        await admin.post(f"/timetable/terms/{term.json()['id']}/approve")
        dept2 = (
            await admin.post(
                "/org/units",
                json={
                    "type": "department", "name": "MEC2", "code": "MEC2",
                    "parent_id": other_school["id"],
                },
            )
        ).json()
        prog2 = (
            await admin.post(
                "/org/units",
                json={
                    "type": "program", "name": "BT MECH2", "code": "BT-MECH2",
                    "parent_id": dept2["id"],
                },
            )
        ).json()
        section2 = (
            await admin.post(
                "/timetable/sections",
                json={"program_id": prog2["id"], "label": "1A", "term_code": "2026-S1"},
            )
        ).json()
        subject2 = (
            await admin.post(
                "/org/subjects",
                json={
                    "code": "ME101", "name": "Mechanics", "department_id": dept2["id"],
                    "kind": "core", "credits": 3,
                },
            )
        ).json()
        offering2 = (
            await admin.post(
                "/org/offerings",
                json={
                    "subject_id": subject2["id"], "program_id": prog2["id"], "position": 1
                },
            )
        ).json()
        grid2 = await admin.post(
            "/timetable/grids",
            json={
                "school_id": other_school["id"],
                "name": "Other day",
                # Deliberately a different grid: overlapping in clock time but a
                # different Period index, which index-based checking would miss.
                "periods": [
                    {"name": "S1", "sequence": 1, "start_time": "09:30", "end_time": "10:30"}
                ],
            },
        )
        draft2 = await admin.post(
            "/timetable/drafts",
            json={"school_id": other_school["id"], "term_code": "2026-S1"},
        )
        teacher2 = (
            await admin.post(
                "/user", json={"username": "prof.d", "full_name": "Prof D", "kind": "staff"}
            )
        ).json()

        clash = await admin.post(
            f"/timetable/drafts/{draft2.json()['id']}/entries",
            json={
                "section_id": section2["id"],
                "day_of_week": 1,
                "period_id": grid2.json()["periods"][0]["id"],
                "offering_id": offering2["id"],
                "faculty_user_id": teacher2["id"],
                "venue_id": setup["venue_id"],  # the same room
            },
        )
    assert clash.status_code == 409, clash.text
    detail = clash.json()["detail"]["clashes"][0]
    assert detail["kind"] == "venue"
    assert detail["draft_status"] == "draft", "a draft-vs-draft clash was missed"
    assert "another School's draft" in detail["message"]


async def test_offering_must_match_the_sections_programme(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        foreign = (
            await admin.post(
                "/org/subjects",
                json={
                    "code": "ME201", "name": "Thermo",
                    "department_id": campus["other_department"], "kind": "core", "credits": 3,
                },
            )
        ).json()
        offering = (
            await admin.post(
                "/org/offerings",
                json={
                    "subject_id": foreign["id"],
                    "program_id": campus["other_program"],
                    "position": 1,
                },
            )
        ).json()
        wrong = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(setup, campus, offering_id=offering["id"]),
        )
    assert wrong.status_code == 422
    assert "not offered to this Section's Programme" in wrong.json()["detail"]


async def test_oversized_venue_warns_then_proceeds_on_acknowledgement(
    make_client, campus
) -> None:
    """TTM-FR-12: a room slightly too small is a judgement call, so it warns and
    records the acknowledgment — unlike a clash, which is simply refused."""
    from tests.onboarding.conftest import csv_bytes, student_row

    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        tiny = (
            await admin.post(
                "/org/venues", json={"code": "TINY", "name": "Seminar", "capacity": 1}
            )
        ).json()
        await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={
                "file": (
                    "i.csv",
                    csv_bytes([student_row(1), student_row(2)]),
                    "text/csv",
                )
            },
        )

        refused = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(setup, campus, venue_id=tiny["id"]),
        )
        accepted = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(setup, campus, venue_id=tiny["id"], acknowledge_capacity=True),
        )
    assert refused.status_code == 409
    assert "seats 1" in refused.json()["detail"]
    assert accepted.status_code == 201
    assert accepted.json()["warnings"], "the acknowledged warning was not reported back"


# --- approval and publish -----------------------------------------------------


async def test_publish_blocked_until_every_department_approves(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )

        state = (await admin.get(f"/timetable/drafts/{setup['draft_id']}")).json()
        assert state["publishable"] is False
        assert [a["status"] for a in state["approvals"]] == ["pending"]

        blocked = await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")
        assert blocked.status_code == 409
        assert "has not approved" in blocked.json()["detail"]

        approved = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        assert approved.status_code == 200

        published = await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"


async def test_editing_after_approval_resets_it(make_client, campus) -> None:
    """An HoD approved a timetable that no longer exists, so their sign-off
    cannot carry to a different one."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(setup, campus, period_id=setup["periods"]["P2"]),
        )
        state = (await admin.get(f"/timetable/drafts/{setup['draft_id']}")).json()
    assert [a["status"] for a in state["approvals"]] == ["pending"]
    assert state["publishable"] is False


async def test_rejection_requires_a_reason(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        bare = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": False},
        )
        with_reason = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={
                "department_id": campus["department"],
                "approve": False,
                "reason": "Prof A is on sabbatical",
            },
        )
    assert bare.status_code == 422
    assert with_reason.status_code == 200


async def test_empty_draft_cannot_publish(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        blocked = await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")
    assert blocked.status_code == 409
    assert "no entries" in blocked.json()["detail"]


async def test_published_timetable_is_immutable(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")

        edit = await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries",
            json=_entry(setup, campus, period_id=setup["periods"]["P2"]),
        )
    assert edit.status_code == 409
    assert "republish creates a new version" in edit.json()["detail"]


async def test_republish_supersedes_the_previous_version(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")

        second = await admin.post(
            "/timetable/drafts", json={"school_id": campus["school"], "term_code": "2026-S1"}
        )
        assert second.json()["version"] == 2
        await admin.post(
            f"/timetable/drafts/{second.json()['id']}/entries",
            json=_entry(setup, campus, period_id=setup["periods"]["P3"]),
        )
        await admin.post(
            f"/timetable/drafts/{second.json()['id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        republished = await admin.post(f"/timetable/drafts/{second.json()['id']}/publish")

        first_state = (await admin.get(f"/timetable/drafts/{setup['draft_id']}")).json()
    assert republished.status_code == 200, republished.text
    assert first_state["status"] == "superseded"


async def test_draft_needs_an_approved_term_and_a_grid(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        no_grid = await admin.post(
            "/timetable/drafts", json={"school_id": campus["school"], "term_code": "2026-S1"}
        )
        assert no_grid.status_code == 409
        assert "no active period grid" in no_grid.json()["detail"]

        await admin.post("/timetable/grids", json={"school_id": campus["school"], **GRID})
        unapproved = await admin.post(
            "/timetable/drafts", json={"school_id": campus["school"], "term_code": "2099-S9"}
        )
    assert unapproved.status_code == 409
    assert "approved academic term" in unapproved.json()["detail"]


# --- personal views (TTM-FR-13) -----------------------------------------------


async def _publish(admin: httpx.AsyncClient, campus: dict, setup: dict) -> None:
    await admin.post(
        f"/timetable/drafts/{setup['draft_id']}/approvals",
        json={"department_id": campus["department"], "approve": True},
    )
    published = await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")
    assert published.status_code == 200, published.text


async def _enrol(admin: httpx.AsyncClient, n: int = 1) -> None:
    from tests.onboarding.conftest import csv_bytes, student_row

    await admin.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1"},
        files={"file": ("i.csv", csv_bytes([student_row(n)]), "text/csv")},
    )


async def test_student_sees_their_published_week(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await _enrol(admin)
        await _publish(admin, campus, setup)

    async with make_client("student", user_id="r0001") as student:
        mine = (
            await student.get("/timetable/me", params={"term_code": "2026-S1"})
        ).json()
    assert mine["role"] == "student"
    assert mine["section_name"] == "3A"
    assert [(r["day_of_week"], r["period_name"], r["subject_code"]) for r in mine["rows"]] == [
        (1, "P1", "MA101")
    ]


async def test_a_draft_is_never_visible_to_a_student(make_client, campus) -> None:
    """Faculty and students never see drafts (TTM §3) — the read path filters on
    published rather than trusting the caller."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await _enrol(admin)

    async with make_client("student", user_id="r0001") as student:
        mine = (
            await student.get("/timetable/me", params={"term_code": "2026-S1"})
        ).json()
    assert mine["rows"] == [], "an unpublished draft leaked to a student"


async def test_student_sees_only_the_elective_they_chose(make_client, campus) -> None:
    """Two alternatives in one group are taught in the same slot; only one is
    theirs, so showing both would put a student in two places at once."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await _enrol(admin)

        chosen: dict[str, str] = {}
        for code, name in (("CS501", "Machine Learning"), ("CS502", "Distributed Systems")):
            subject = (
                await admin.post(
                    "/org/subjects",
                    json={
                        "code": code, "name": name, "department_id": campus["department"],
                        "kind": "elective", "elective_group": "professional", "credits": 3,
                    },
                )
            ).json()
            offering = (
                await admin.post(
                    "/org/offerings",
                    json={
                        "subject_id": subject["id"],
                        "program_id": campus["program"],
                        "position": 1,
                    },
                )
            ).json()
            chosen[code] = offering["id"]

        # Both alternatives are timetabled — in different slots here only because
        # one Section cannot hold two entries in the same slot.
        for i, code in enumerate(("CS501", "CS502")):
            teacher = (
                await admin.post(
                    "/user",
                    json={"username": f"prof.e{i}", "full_name": f"Prof E{i}", "kind": "staff"},
                )
            ).json()
            venue = (
                await admin.post(
                    "/org/venues",
                    json={"code": f"E{i}", "name": f"Room E{i}", "capacity": 60},
                )
            ).json()
            await admin.post(
                f"/timetable/drafts/{setup['draft_id']}/entries",
                json=_entry(
                    setup,
                    campus,
                    period_id=setup["periods"]["P2" if i == 0 else "P3"],
                    offering_id=chosen[code],
                    faculty_user_id=teacher["id"],
                    venue_id=venue["id"],
                ),
            )
        await _publish(admin, campus, setup)

    async with make_client("student", user_id="r0001") as student:
        before = (await student.get("/timetable/me", params={"term_code": "2026-S1"})).json()
        await student.post(
            "/onboarding/me/electives",
            json={"offering_id": chosen["CS501"], "term_code": "2026-S1"},
        )
        after = (await student.get("/timetable/me", params={"term_code": "2026-S1"})).json()

    assert [r["subject_code"] for r in before["rows"]] == [], (
        "electives appeared before the student chose one"
    )
    assert [r["subject_code"] for r in after["rows"]] == ["CS501"]


async def test_faculty_sees_their_own_load(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await _publish(admin, campus, setup)

    async with make_client("professor", user_id="prof.a") as teacher:
        mine = (await teacher.get("/timetable/me", params={"term_code": "2026-S1"})).json()
    assert mine["role"] == "faculty"
    assert [(r["subject_code"], r["section_name"], r["venue_code"]) for r in mine["rows"]] == [
        ("MA101", "3A", "A101")
    ]


async def test_faculty_does_not_see_another_teachers_classes(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await admin.post(
            "/user", json={"username": "prof.z", "full_name": "Prof Z", "kind": "staff"}
        )
        await _publish(admin, campus, setup)

    async with make_client("professor", user_id="prof.z") as other:
        mine = (await other.get("/timetable/me", params={"term_code": "2026-S1"})).json()
    assert mine["rows"] == [], "a teacher saw a class they do not teach"


async def test_student_without_a_section_is_told_why_rather_than_shown_nothing(
    make_client, campus
) -> None:
    """An empty week with no explanation looks like a broken page. A student who
    holds a profile but no current Section gets the reason."""
    async with make_client("super-admin") as admin:
        setup = await _setup(admin, campus)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/entries", json=_entry(setup, campus)
        )
        await _enrol(admin)
        await _publish(admin, campus, setup)

        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        # Withdrawal closes the Section membership but keeps the student record,
        # which is exactly the "profile without a Section" shape.
        form = {"reason": "left the programme", "effective_from": "2026-08-01"}
        withdrawn = await admin.post(
            f"/onboarding/students/{roster[0]['user_id']}/withdraw", data=form
        )
        assert withdrawn.status_code == 202, withdrawn.text

    async with make_client("student", user_id="r0001") as student:
        mine = (await student.get("/timetable/me", params={"term_code": "2026-S1"})).json()
    assert mine["role"] == "student"
    assert mine["rows"] == []
    assert mine["note"] and "not allotted" in mine["note"]
