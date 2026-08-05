"""The `world` fixture: one university, built end to end through the HTTP API.

MANDATORY (project rule): every feature that adds to the academic setup chain —
org structure, terms, sections, staff, subjects, offerings, venues, students,
electives, grids, timetables — must extend this world and assert its effect in
`test_end_to_end.py`. A feature that only unit tests its own module has not been
integration tested.

Two rules keep this honest:

1.  **Every call goes through HTTP with a real token.** Nothing is seeded
    straight into the database, so the auth gate, the permission checks and the
    scope filters are all exercised on the way past — a route that forgets
    `require_permission` fails here, not only in `test_security.py`.
2.  **Each stage uses the actor who would really do it.** Super Admin builds the
    org tree, the School Incharge approves the term, the HoD signs off the
    timetable. A stage that only passes as super-admin is hiding a missing grant.

The world is deliberately two Schools wide: an Engineering School that teaches
in theory and lab hours, and a Nursing School that teaches in clinical postings
and field work. Single-School fixtures hide the cross-School bugs — a shared
room double-booked, an Open elective that never leaves its own School.
"""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

# --- the shape of the world ---------------------------------------------------

TERM = "2026-S1"

#: Both Schools run the same day, so a room booked by one collides with the other.
PERIODS = [
    {"name": "P1", "sequence": 1, "start_time": "09:00", "end_time": "10:00"},
    {"name": "P2", "sequence": 2, "start_time": "10:00", "end_time": "11:00"},
    {"name": "P3", "sequence": 3, "start_time": "11:00", "end_time": "12:00"},
    {"name": "P4", "sequence": 4, "start_time": "14:00", "end_time": "15:00"},
]

STAFF = [
    ("EMP-9001", "Prof Aruna Rajan", "Professor", "CSE"),
    ("EMP-9002", "Dr Bhaskar Iyer", "HoD", "CSE"),
    ("EMP-9003", "Sr Chitra Menon", "Professor", "NURS"),
    ("EMP-9004", "Dr Devi Krishnan", "HoD", "NURS"),
]

# (code, name, department, kind, elective_group, credits, hours)
SUBJECTS: list[tuple[str, str, str, str, str | None, int, dict[str, int]]] = [
    ("MA101", "Engineering Mathematics I", "CSE", "core", None, 4, {"theory": 4}),
    ("CS201", "Data Structures", "CSE", "core", None, 4, {"theory": 3, "lab": 2}),
    ("CS501", "Machine Learning", "CSE", "elective", "professional", 3, {"theory": 3}),
    ("CS502", "Distributed Systems", "CSE", "elective", "professional", 3, {"theory": 3}),
    # Nursing teaches the same subject two ways at once — that is why hours is a
    # map and not a theory/lab pair.
    ("NUR101", "Community Health Nursing", "NURS", "core", None, 4, {"theory": 2, "field_work": 4}),
    ("NUR201", "Medical-Surgical Nursing", "NURS", "core", None, 4, {"theory": 2, "clinical": 8}),
    # Owned by Nursing, offered university-wide: an Engineering student can take
    # it, which is the whole point of an Open elective.
    ("OE201", "Indian Constitution", "NURS", "elective", "open", 2, {"theory": 2}),
]

VENUES = [
    ("CR-101", "Lecture Hall 101", "classroom", 60),
    ("LAB-1", "Programming Lab 1", "lab", 30),
    # Off-campus, but still a venue: two cohorts cannot be at the same PHC in the
    # same slot, so field work is clash-checked like any other class.
    ("FIELD-PHC", "Primary Health Centre, Tiruvallur", "field", 40),
]


def _staff_csv() -> bytes:
    from unicore.modules.onboarding.schemas import STAFF_CSV_COLUMNS

    rows = [
        {
            "employee_id": emp,
            "full_name": name,
            "designation": designation,
            "department_code": dept,
            "email": f"{emp.lower()}@takshashila.edu.in",
            "date_of_joining": "01-06-2020",
        }
        for emp, name, designation, dept in STAFF
    ]
    lines = [",".join(STAFF_CSV_COLUMNS)]
    lines += [",".join(r.get(c, "") for c in STAFF_CSV_COLUMNS) for r in rows]
    return ("\n".join(lines) + "\n").encode()


def username_of(employee_id: str) -> str:
    """Staff usernames are the employee id, alphanumeric and lowercased."""
    return "".join(ch for ch in employee_id.lower() if ch.isalnum())


# --- stages -------------------------------------------------------------------


async def _post(client: httpx.AsyncClient, url: str, body: object, expect: int = 201) -> dict:
    response = await client.post(url, json=body)
    assert response.status_code == expect, f"POST {url} -> {response.status_code} {response.text}"
    return response.json()


async def build_org_tree(admin: httpx.AsyncClient) -> dict[str, str]:
    """University → two Faculty Divisions → two Schools → Departments → Programmes."""
    ids: dict[str, str] = {}
    uni = await _post(
        admin, "/org/units", {"type": "university", "name": "Takshashila", "code": "TU"}
    )
    ids["university"] = uni["id"]

    for fd_code, school_code, school_name, dept_code, prog_code, prog_name in (
        ("FET", "SOCE", "School of Computing", "CSE", "BT-CSE", "B.Tech Computer Science"),
        ("FHS", "SONUR", "School of Nursing", "NURS", "BSC-NURS", "B.Sc Nursing"),
    ):
        fd = await _post(
            admin,
            "/org/units",
            {"type": "faculty_division", "name": fd_code, "code": fd_code, "parent_id": uni["id"]},
        )
        school = await _post(
            admin,
            "/org/units",
            {
                "type": "school",
                "name": school_name,
                "code": school_code,
                "parent_id": fd["id"],
                "cadence": "semester",
            },
        )
        dept = await _post(
            admin,
            "/org/units",
            {"type": "department", "name": dept_code, "code": dept_code, "parent_id": school["id"]},
        )
        prog = await _post(
            admin,
            "/org/units",
            {"type": "program", "name": prog_name, "code": prog_code, "parent_id": dept["id"]},
        )
        # The ladder a Programme runs — 4 years of a semester School is positions 1..8.
        updated = await admin.put(f"/org/units/{prog['id']}", json={"duration_years": 4})
        assert updated.status_code == 200, updated.text
        ids |= {
            f"fd_{fd_code}": fd["id"],
            f"school_{school_code}": school["id"],
            f"dept_{dept_code}": dept["id"],
            f"prog_{prog_code}": prog["id"],
        }
    return ids


async def approve_terms(
    admin: httpx.AsyncClient, make_client: Callable[..., httpx.AsyncClient], ids: dict[str, str]
) -> dict[str, str]:
    """A term is drafted by the School office and approved by the School Incharge."""
    terms: dict[str, str] = {}
    for school in ("SOCE", "SONUR"):
        term = await _post(
            admin,
            "/timetable/terms",
            {
                "school_id": ids[f"school_{school}"],
                "term_code": TERM,
                "start_date": "2026-07-01",
                "end_date": "2026-11-30",
            },
        )
        terms[school] = term["id"]

    async with make_client("super-admin", user_id="school.incharge") as incharge:
        for term_id in terms.values():
            approved = await incharge.post(f"/timetable/terms/{term_id}/approve")
            assert approved.status_code == 200, approved.text
    return {f"term_{k}": v for k, v in terms.items()}


async def enable_components(admin: httpx.AsyncClient, ids: dict[str, str]) -> None:
    """Each School declares what it teaches in before its subjects can carry hours."""
    wanted = {"SOCE": {"theory", "lab"}, "SONUR": {"theory", "clinical", "field_work"}}
    for school, codes in wanted.items():
        school_id = ids[f"school_{school}"]
        catalogue = (await admin.get(f"/org/components?school_id={school_id}")).json()
        chosen = [c["code"] for c in catalogue if c["code"] in codes]
        assert len(chosen) == len(codes), f"{school}: components missing from the catalogue"
        saved = await admin.put(f"/org/schools/{school_id}/components", json={"codes": chosen})
        assert saved.status_code == 200, saved.text


async def import_staff(admin: httpx.AsyncClient) -> dict[str, str]:
    """Bulk staff provisioning — the designation column grants the role."""
    run = await admin.post(
        "/onboarding/staff/imports",
        files={"file": ("staff.csv", _staff_csv(), "text/csv")},
    )
    assert run.status_code == 201, run.text
    assert run.json()["rows_rejected"] == 0, run.json()
    assert run.json()["rows_created"] == len(STAFF)

    # The Timetable Cell picks faculty out of the same directory the UI reads.
    directory = (await admin.get("/rbac/directory", params={"limit": 2000})).json()
    by_username = {row["username"]: row["user_id"] for row in directory}
    return {emp: by_username[username_of(emp)] for emp, *_ in STAFF}


async def build_catalogue(admin: httpx.AsyncClient, ids: dict[str, str]) -> dict[str, str]:
    """Subjects owned by the Department that teaches them, then offered to the
    Programmes that study them."""
    subjects: dict[str, str] = {}
    for code, name, dept, kind, group, credits, hours in SUBJECTS:
        created = await _post(
            admin,
            "/org/subjects",
            {
                "code": code,
                "name": name,
                "department_id": ids[f"dept_{dept}"],
                "kind": kind,
                "elective_group": group,
                "credits": credits,
                "hours": hours,
            },
        )
        subjects[code] = created["id"]

    offerings: dict[str, str] = {}
    plan: list[tuple[str, str | None, int | None]] = [
        ("MA101", "BT-CSE", 30),
        ("CS201", "BT-CSE", 30),
        # One seat, on purpose: the capacity race has somewhere to happen.
        ("CS501", "BT-CSE", 1),
        ("CS502", "BT-CSE", 30),
        ("NUR101", "BSC-NURS", 40),
        ("NUR201", "BSC-NURS", 40),
        ("OE201", None, 50),  # university-wide: no Programme, no position
    ]
    for code, programme, capacity in plan:
        body: dict[str, object] = {"subject_id": subjects[code], "capacity": capacity}
        if programme is not None:
            body |= {"program_id": ids[f"prog_{programme}"], "position": 1}
        offerings[code] = (await _post(admin, "/org/offerings", body))["id"]
    return {
        **{f"subject_{k}": v for k, v in subjects.items()},
        **{f"offering_{k}": v for k, v in offerings.items()},
    }


async def create_venues(admin: httpx.AsyncClient) -> dict[str, str]:
    """Rooms are university-wide, not School-owned — that is what makes a
    cross-School double-booking detectable."""
    venues = {}
    for code, name, kind, capacity in VENUES:
        created = await _post(
            admin, "/org/venues", {"code": code, "name": name, "kind": kind, "capacity": capacity}
        )
        venues[f"venue_{code}"] = created["id"]
    return venues


async def create_sections(admin: httpx.AsyncClient, ids: dict[str, str]) -> dict[str, str]:
    sections = {}
    for programme in ("BT-CSE", "BSC-NURS"):
        created = await _post(
            admin,
            "/timetable/sections",
            {"program_id": ids[f"prog_{programme}"], "label": "1A", "term_code": TERM},
        )
        sections[f"section_{programme}"] = created["id"]
    return sections


def _student_csv(rows: list[dict[str, str]]) -> bytes:
    from unicore.modules.onboarding.schemas import CSV_COLUMNS_V1

    lines = [",".join(CSV_COLUMNS_V1)]
    lines += [",".join(r.get(c, "") for c in CSV_COLUMNS_V1) for r in rows]
    return ("\n".join(lines) + "\n").encode()


async def import_students(admin: httpx.AsyncClient) -> list[str]:
    """Three Engineering and two Nursing first-years, from the ERP extract."""
    roster = [
        ("R-0001", "Ananya Raman", "BT-CSE"),
        ("R-0002", "Karthik Subramanian", "BT-CSE"),
        ("R-0003", "Fatima Sheikh", "BT-CSE"),
        ("R-0004", "Joseph Mathew", "BSC-NURS"),
        ("R-0005", "Meera Nair", "BSC-NURS"),
    ]
    rows = [
        {
            "sif_id": f"SIF-2026-{i:05d}",
            "full_name": name,
            "date_of_birth": "15-08-2006",
            "gender": "F",
            "mobile": f"98000{i:05d}",
            "email": f"{roll.lower()}@student.takshashila.edu.in",
            "program_code": programme,
            "section_label": "1A",
            "admission_year": "2026",
            "position": "1",
            "roll_number": roll,
        }
        for i, (roll, name, programme) in enumerate(roster, start=1)
    ]
    run = await admin.post(
        "/onboarding/imports",
        data={"term_code": TERM},
        files={"file": ("students.csv", _student_csv(rows), "text/csv")},
    )
    assert run.status_code == 201, run.text
    assert run.json()["rows_rejected"] == 0, run.json()
    assert run.json()["rows_created"] == len(roster)
    return [roll.lower().replace("-", "") for roll, _, _ in roster]


async def choose_electives(
    make_client: Callable[..., httpx.AsyncClient], ids: dict[str, str]
) -> None:
    """One student picks one subject per elective group — including an Open
    elective owned by a School they do not belong to."""
    async with make_client("student", user_id="r0001") as student:
        for code in ("CS501", "OE201"):
            picked = await student.post(
                "/onboarding/me/electives",
                json={"offering_id": ids[f"offering_{code}"], "term_code": TERM},
            )
            assert picked.status_code == 201, picked.text


async def build_timetables(
    admin: httpx.AsyncClient, make_client: Callable[..., httpx.AsyncClient], ids: dict[str, str]
) -> dict[str, str]:
    """A grid and a published week per School, signed off by the owning HoD."""
    result: dict[str, str] = {}
    for school in ("SOCE", "SONUR"):
        grid = await _post(
            admin,
            "/timetable/grids",
            {"school_id": ids[f"school_{school}"], "name": "Standard day", "periods": PERIODS},
        )
        result[f"grid_{school}"] = grid["id"]
        for period in grid["periods"]:
            result[f"period_{school}_{period['name']}"] = period["id"]

        draft = await _post(
            admin,
            "/timetable/drafts",
            {"school_id": ids[f"school_{school}"], "term_code": TERM},
        )
        result[f"draft_{school}"] = draft["id"]

    # (School, section, day, period, offering, faculty, venue)
    week: list[tuple[str, str, int, str, str, str, str]] = [
        ("SOCE", "BT-CSE", 1, "P1", "MA101", "EMP-9001", "CR-101"),
        ("SOCE", "BT-CSE", 1, "P2", "CS201", "EMP-9001", "LAB-1"),
        ("SOCE", "BT-CSE", 2, "P1", "CS501", "EMP-9001", "CR-101"),
        # The two professional electives are alternatives and belong in one
        # shared slot, but a Section cannot hold two entries at the same time
        # yet (TTM-FR-06, elective slots, is still outstanding) — so for now
        # they sit in consecutive Periods.
        ("SOCE", "BT-CSE", 2, "P2", "CS502", "EMP-9002", "LAB-1"),
        # A Nursing-owned Open elective, taught to Engineering students by
        # Nursing staff, inside the Engineering School's timetable.
        ("SOCE", "BT-CSE", 3, "P1", "OE201", "EMP-9003", "CR-101"),
        ("SONUR", "BSC-NURS", 1, "P3", "NUR101", "EMP-9003", "FIELD-PHC"),
        ("SONUR", "BSC-NURS", 2, "P3", "NUR201", "EMP-9004", "CR-101"),
    ]
    for school, programme, day, period, subject, staff, venue in week:
        await _post(
            admin,
            f"/timetable/drafts/{result[f'draft_{school}']}/entries",
            {
                "section_id": ids[f"section_{programme}"],
                "day_of_week": day,
                "period_id": result[f"period_{school}_{period}"],
                "offering_id": ids[f"offering_{subject}"],
                "faculty_user_id": ids[f"staff_{staff}"],
                "venue_id": ids[f"venue_{venue}"],
            },
        )

    # The HoD whose Department owns the Sections signs off, then it is published.
    for school, dept, hod in (("SOCE", "CSE", "EMP-9002"), ("SONUR", "NURS", "EMP-9004")):
        async with make_client("hod", user_id=username_of(hod)) as hod_client:
            await _post(
                hod_client,
                f"/timetable/drafts/{result[f'draft_{school}']}/approvals",
                {"department_id": ids[f"dept_{dept}"], "approve": True},
                expect=200,
            )
        published = await admin.post(f"/timetable/drafts/{result[f'draft_{school}']}/publish")
        assert published.status_code == 200, published.text
    return result


# --- the fixture --------------------------------------------------------------


@pytest.fixture
async def world(make_client: Callable[..., httpx.AsyncClient]) -> AsyncIterator[dict[str, str]]:
    """A fully set-up university: two Schools, staff, subjects, students and a
    published timetable for each School.

    Returns a flat id map keyed `school_SOCE`, `dept_CSE`, `prog_BT-CSE`,
    `section_BT-CSE`, `subject_MA101`, `offering_MA101`, `venue_CR-101`,
    `staff_EMP-9001`, `draft_SOCE`, `period_SOCE_P1`, `term_SOCE`, plus
    `students` (a list of usernames).
    """
    async with make_client("super-admin") as admin:
        ids = await build_org_tree(admin)
        ids |= await approve_terms(admin, make_client, ids)
        await enable_components(admin, ids)
        ids |= {f"staff_{k}": v for k, v in (await import_staff(admin)).items()}
        ids |= await build_catalogue(admin, ids)
        ids |= await create_venues(admin)
        ids |= await create_sections(admin, ids)
        students = await import_students(admin)
        await choose_electives(make_client, ids)
        ids |= await build_timetables(admin, make_client, ids)
    yield {**ids, "students": students}  # type: ignore[dict-item]
