"""Subjects, offerings, venues, staff import and student elective choice.

The master data TTM cannot exist without: a Period needs a subject to point at,
a room to sit in, and a faculty member to teach it.
"""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row

# student_row(1) carries roll_number R-0001, and the importer derives the
# username from it — so this is how a test signs in *as* that student rather
# than as a new user who merely shares their display name.
STUDENT_USERNAME = "r0001"


async def _subject(admin: httpx.AsyncClient, campus: dict, **over: object) -> dict:
    body = {
        "code": "MA101",
        "name": "Engineering Mathematics I",
        "department_id": campus["department"],
        "kind": "core",
        "credits": 4,
        "theory_hours": 4,
        "lab_hours": 0,
        **over,
    }
    return (await admin.post("/org/subjects", json=body)).json()


# --- subjects ----------------------------------------------------------------


async def test_subject_is_owned_by_a_department_not_a_programme(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        created = await admin.post(
            "/org/subjects",
            json={
                "code": "MA101",
                "name": "Engineering Mathematics I",
                "department_id": campus["program"],  # a Programme, not a Department
                "kind": "core",
            },
        )
    assert created.status_code == 422
    assert "owned by a Department" in created.json()["detail"]


async def test_one_subject_serves_many_programmes(make_client, campus) -> None:
    """The point of Department ownership: MA101 is defined once and offered to
    every Programme that studies it, so coverage and question banks stay whole."""
    async with make_client("super-admin") as admin:
        subject = await _subject(admin, campus)
        for programme in (campus["program"], campus["other_program"]):
            offered = await admin.post(
                "/org/offerings",
                json={"subject_id": subject["id"], "program_id": programme, "position": 1},
            )
            assert offered.status_code == 201, offered.text

        mine = (await admin.get(f"/org/programmes/{campus['program']}/offerings")).json()
        theirs = (await admin.get(f"/org/programmes/{campus['other_program']}/offerings")).json()
    assert [o["subject"]["code"] for o in mine] == ["MA101"]
    assert [o["subject"]["code"] for o in theirs] == ["MA101"]
    assert mine[0]["subject"]["id"] == theirs[0]["subject"]["id"], "the subject was duplicated"


async def test_elective_requires_a_group_and_core_forbids_one(make_client, campus) -> None:
    """The group is what a student chooses *within*, so it is meaningless on a
    core subject and mandatory on an elective."""
    async with make_client("super-admin") as admin:
        no_group = await admin.post(
            "/org/subjects",
            json={
                "code": "CS501",
                "name": "Machine Learning",
                "department_id": campus["department"],
                "kind": "elective",
            },
        )
        core_with_group = await admin.post(
            "/org/subjects",
            json={
                "code": "CS502",
                "name": "Compilers",
                "department_id": campus["department"],
                "kind": "core",
                "elective_group": "professional",
            },
        )
    assert no_group.status_code == 422
    assert core_with_group.status_code == 422


async def test_offering_position_must_be_on_the_ladder(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        subject = await _subject(admin, campus)
        bad = await admin.post(
            "/org/offerings",
            json={"subject_id": subject["id"], "program_id": campus["program"], "position": 9},
        )
    assert bad.status_code == 422
    assert "1..8" in bad.json()["detail"]


async def test_offering_is_idempotent(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        subject = await _subject(admin, campus)
        body = {"subject_id": subject["id"], "program_id": campus["program"], "position": 1}
        first = await admin.post("/org/offerings", json=body)
        second = await admin.post("/org/offerings", json=body)
        listed = (await admin.get(f"/org/programmes/{campus['program']}/offerings")).json()
    assert first.json()["id"] == second.json()["id"]
    assert len(listed) == 1


async def test_subject_import_defines_once_and_offers_many(make_client, campus) -> None:
    from unicore.modules.org.schemas import SUBJECT_CSV_COLUMNS

    rows = [
        {
            "subject_code": "MA101", "subject_name": "Engineering Mathematics I",
            "department_code": "CSE", "kind": "core", "elective_group": "",
            "credits": "4", "theory_hours": "4", "lab_hours": "0",
            "programme_code": "BT-CSE", "position": "1",
        },
        {
            "subject_code": "MA101", "subject_name": "Engineering Mathematics I",
            "department_code": "CSE", "kind": "core", "elective_group": "",
            "credits": "4", "theory_hours": "4", "lab_hours": "0",
            "programme_code": "BT-MECH", "position": "1",
        },
        {
            "subject_code": "CS501", "subject_name": "Machine Learning",
            "department_code": "CSE", "kind": "elective", "elective_group": "professional",
            "credits": "3", "theory_hours": "3", "lab_hours": "2",
            "programme_code": "BT-CSE", "position": "3",
        },
        {  # unknown Programme — rejected without failing the run
            "subject_code": "XX999", "subject_name": "Nowhere",
            "department_code": "CSE", "kind": "core", "elective_group": "",
            "credits": "1", "theory_hours": "1", "lab_hours": "0",
            "programme_code": "NOPE", "position": "1",
        },
    ]
    header = ",".join(SUBJECT_CSV_COLUMNS)
    body = "\n".join(
        [header] + [",".join(r.get(c, "") for c in SUBJECT_CSV_COLUMNS) for r in rows]
    )

    async with make_client("super-admin") as admin:
        result = await admin.post(
            "/org/subjects/imports",
            files={"file": ("subjects.csv", body.encode(), "text/csv")},
        )
        payload = result.json()
        subjects = (await admin.get("/org/subjects")).json()
    assert payload["subjects_created"] == 2, payload  # MA101 and CS501; XX999 rejected
    assert payload["offerings_created"] == 3
    assert payload["rows_rejected"] == 1
    assert payload["errors"][0]["field"] == "programme_code"
    assert sorted(s["code"] for s in subjects) == ["CS501", "MA101"]


# --- venues ------------------------------------------------------------------


async def test_venue_crud_and_import(make_client, campus) -> None:
    from unicore.modules.org.schemas import VENUE_CSV_COLUMNS

    rows = [
        {"code": "A101", "name": "Lecture Hall A101", "capacity": "60", "kind": "classroom",
         "campus_code": "MAIN", "building": "Block A", "room": "101"},
        {"code": "CSLAB1", "name": "Computer Lab 1", "capacity": "30", "kind": "lab",
         "campus_code": "MAIN", "building": "Block B", "room": "G02"},
        {"code": "BAD1", "name": "Mystery Room", "capacity": "10", "kind": "dungeon",
         "campus_code": "", "building": "", "room": ""},
    ]
    header = ",".join(VENUE_CSV_COLUMNS)
    body = "\n".join(
        [header] + [",".join(r.get(c, "") for c in VENUE_CSV_COLUMNS) for r in rows]
    )

    async with make_client("super-admin") as admin:
        result = (
            await admin.post(
                "/org/venues/imports", files={"file": ("venues.csv", body.encode(), "text/csv")}
            )
        ).json()
        venues = (await admin.get("/org/venues")).json()
        labs = (await admin.get("/org/venues", params={"kind": "lab"})).json()

        # Re-import updates rather than duplicating.
        again = (
            await admin.post(
                "/org/venues/imports", files={"file": ("venues.csv", body.encode(), "text/csv")}
            )
        ).json()
    assert (result["rows_created"], result["rows_rejected"]) == (2, 1)
    assert result["errors"][0]["field"] == "kind"
    assert sorted(v["code"] for v in venues) == ["A101", "CSLAB1"]
    assert [v["code"] for v in labs] == ["CSLAB1"]
    assert again["rows_created"] == 0 and again["rows_updated"] == 2


async def test_venue_write_is_admin_only(make_client, campus) -> None:
    """Rooms are central estate: a School Incharge may read but not create."""
    async with make_client("super-admin") as admin:
        await _hod_grant(admin, campus)

    async with make_client("hod", user_id="hod.cse") as hod:
        denied = await hod.post(
            "/org/venues", json={"code": "X1", "name": "Room", "capacity": 10}
        )
    assert denied.status_code == 403


async def _hod_grant(admin: httpx.AsyncClient, campus: dict) -> None:
    user = await admin.post(
        "/user", json={"username": "hod.cse", "full_name": "HoD CSE", "kind": "staff"}
    )
    await admin.post(
        "/rbac/grants",
        json={
            "user_id": user.json()["id"],
            "role_code": "hod",
            "org_unit_id": campus["department"],
        },
    )


# --- staff import -------------------------------------------------------------


async def _staff_csv(rows: list[dict[str, str]]) -> bytes:
    from unicore.modules.onboarding.schemas import STAFF_CSV_COLUMNS

    header = ",".join(STAFF_CSV_COLUMNS)
    lines = [header] + [",".join(r.get(c, "") for c in STAFF_CSV_COLUMNS) for r in rows]
    return "\n".join(lines).encode()


async def test_staff_import_provisions_and_grants_the_designation(make_client, campus) -> None:
    content = await _staff_csv(
        [
            {"employee_id": "EMP-1", "full_name": "Prof A", "designation": "Professor",
             "department_code": "CSE", "email": "a@uni.example", "mobile": "",
             "date_of_joining": ""},
            {"employee_id": "EMP-2", "full_name": "Dr B", "designation": "HoD",
             "department_code": "CSE", "email": "b@uni.example", "mobile": "",
             "date_of_joining": ""},
        ]
    )
    async with make_client("super-admin") as admin:
        run = (
            await admin.post(
                "/onboarding/staff/imports", files={"file": ("staff.csv", content, "text/csv")}
            )
        ).json()
        directory = (await admin.get("/rbac/directory")).json()

    assert run["rows_created"] == 2, run
    by_name = {r["full_name"]: r for r in directory}
    assert [e["role_code"] for e in by_name["Prof A"]["roles"]] == ["professor"]
    assert [e["role_code"] for e in by_name["Dr B"]["roles"]] == ["hod"]


async def test_staff_import_will_not_double_head_a_department(make_client, campus) -> None:
    """HoD is a singleton — a file naming a second one is rejected, not applied."""
    first = await _staff_csv(
        [{"employee_id": "EMP-1", "full_name": "Dr B", "designation": "HoD",
          "department_code": "CSE", "email": "b@uni.example", "mobile": "",
          "date_of_joining": ""}]
    )
    second = await _staff_csv(
        [{"employee_id": "EMP-9", "full_name": "Dr C", "designation": "HoD",
          "department_code": "CSE", "email": "c@uni.example", "mobile": "",
          "date_of_joining": ""}]
    )
    async with make_client("super-admin") as admin:
        await admin.post(
            "/onboarding/staff/imports", files={"file": ("a.csv", first, "text/csv")}
        )
        clash = (
            await admin.post(
                "/onboarding/staff/imports", files={"file": ("b.csv", second, "text/csv")}
            )
        ).json()
        errors = (await admin.get(f"/onboarding/imports/{clash['id']}/errors")).json()
    assert clash["rows_rejected"] == 1
    assert errors[0]["field"] == "designation"
    assert "active holder" in errors[0]["reason"]


async def test_staff_import_rejects_an_unknown_designation(make_client, campus) -> None:
    content = await _staff_csv(
        [{"employee_id": "EMP-3", "full_name": "Someone", "designation": "Chief Wizard",
          "department_code": "CSE", "email": "c@uni.example", "mobile": "",
          "date_of_joining": ""}]
    )
    async with make_client("super-admin") as admin:
        run = (
            await admin.post(
                "/onboarding/staff/imports", files={"file": ("staff.csv", content, "text/csv")}
            )
        ).json()
        errors = (await admin.get(f"/onboarding/imports/{run['id']}/errors")).json()
    assert run["rows_rejected"] == 1
    assert errors[0]["field"] == "designation"


async def test_staff_import_is_idempotent(make_client, campus) -> None:
    content = await _staff_csv(
        [{"employee_id": "EMP-1", "full_name": "Prof A", "designation": "Professor",
          "department_code": "CSE", "email": "a@uni.example", "mobile": "",
          "date_of_joining": ""}]
    )
    async with make_client("super-admin") as admin:
        await admin.post(
            "/onboarding/staff/imports", files={"file": ("s.csv", content, "text/csv")}
        )
        again = (
            await admin.post(
                "/onboarding/staff/imports", files={"file": ("s.csv", content, "text/csv")}
            )
        ).json()
        directory = (await admin.get("/rbac/directory")).json()
    assert (again["rows_created"], again["rows_unchanged"]) == (0, 1)
    assert len([r for r in directory if r["full_name"] == "Prof A"]) == 1


# --- student elective choice --------------------------------------------------


async def _elective_setup(admin: httpx.AsyncClient, campus: dict) -> dict[str, str]:
    """Two professional electives and one open elective at position 1."""
    await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
    offerings = {}
    for code, name, group in (
        ("CS501", "Machine Learning", "professional"),
        ("CS502", "Distributed Systems", "professional"),
        ("OE201", "Indian Constitution", "open"),
    ):
        subject = (
            await admin.post(
                "/org/subjects",
                json={
                    "code": code, "name": name, "department_id": campus["department"],
                    "kind": "elective", "elective_group": group, "credits": 3,
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
        offerings[code] = offering["id"]
    return offerings


async def test_student_sees_and_chooses_one_elective_per_group(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        offerings = await _elective_setup(admin, campus)
        await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("i.csv", csv_bytes([student_row(1)]), "text/csv")},
        )
    async with make_client("student", user_id=STUDENT_USERNAME) as student:
        groups = (
            await student.get("/onboarding/me/electives", params={"term_code": "2026-S1"})
        ).json()
        by_group = {g["elective_group"]: g for g in groups}
        assert sorted(by_group) == ["open", "professional"]
        assert len(by_group["professional"]["options"]) == 2
        assert by_group["professional"]["chosen_offering_id"] is None

        picked = await student.post(
            "/onboarding/me/electives",
            json={"offering_id": offerings["CS501"], "term_code": "2026-S1"},
        )
        assert picked.status_code == 201, picked.text

        after = (
            await student.get("/onboarding/me/electives", params={"term_code": "2026-S1"})
        ).json()
    professional = next(g for g in after if g["elective_group"] == "professional")
    assert professional["chosen_offering_id"] == offerings["CS501"]
    assert [o["chosen"] for o in professional["options"] if o["subject_code"] == "CS501"] == [True]


async def test_choosing_again_replaces_rather_than_adds(make_client, campus) -> None:
    """One subject per group per term — enforced by the database, so a
    double-submit cannot leave a student in two alternatives."""
    async with make_client("super-admin") as admin:
        offerings = await _elective_setup(admin, campus)
        await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("i.csv", csv_bytes([student_row(1)]), "text/csv")},
        )

    async with make_client("student", user_id=STUDENT_USERNAME) as student:
        for code in ("CS501", "CS502"):
            await student.post(
                "/onboarding/me/electives",
                json={"offering_id": offerings[code], "term_code": "2026-S1"},
            )
        groups = (
            await student.get("/onboarding/me/electives", params={"term_code": "2026-S1"})
        ).json()
    professional = next(g for g in groups if g["elective_group"] == "professional")
    assert professional["chosen_offering_id"] == offerings["CS502"]
    assert sum(1 for o in professional["options"] if o["chosen"]) == 1


async def test_student_cannot_choose_another_programmes_elective(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        subject = (
            await admin.post(
                "/org/subjects",
                json={
                    "code": "ME501", "name": "Thermodynamics II",
                    "department_id": campus["other_department"], "kind": "elective",
                    "elective_group": "professional", "credits": 3,
                },
            )
        ).json()
        foreign = (
            await admin.post(
                "/org/offerings",
                json={
                    "subject_id": subject["id"],
                    "program_id": campus["other_program"],
                    "position": 1,
                },
            )
        ).json()
        await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("i.csv", csv_bytes([student_row(1)]), "text/csv")},
        )

    async with make_client("student", user_id=STUDENT_USERNAME) as student:
        denied = await student.post(
            "/onboarding/me/electives",
            json={"offering_id": foreign["id"], "term_code": "2026-S1"},
        )
    assert denied.status_code == 403
    assert "not offered to your Programme" in denied.json()["detail"]


async def test_a_core_subject_is_not_choosable(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        subject = await _subject(admin, campus)
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
        await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("i.csv", csv_bytes([student_row(1)]), "text/csv")},
        )

    async with make_client("student", user_id=STUDENT_USERNAME) as student:
        denied = await student.post(
            "/onboarding/me/electives",
            json={"offering_id": offering["id"], "term_code": "2026-S1"},
        )
    assert denied.status_code == 422
    assert "core subject" in denied.json()["detail"]
