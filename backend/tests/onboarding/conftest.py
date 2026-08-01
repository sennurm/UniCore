"""Shared fixtures for ONB tests: an org tree with an approved term and Sections."""

from collections.abc import Callable

import httpx
import pytest


@pytest.fixture
async def campus(make_client: Callable[..., httpx.AsyncClient]) -> dict[str, str]:
    """University → FET → SOCE (School) → CSE (Dept) → BT-CSE (Program) → Sections 3A/3B,
    with an approved 2026-S1 term. Returns the ids by name."""
    async with make_client("super-admin") as admin:

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
        program = await unit(type="program", name="BTech CSE", code="BT-CSE", parent_id=dept["id"])
        other_dept = await unit(type="department", name="MEC", code="MEC", parent_id=school["id"])
        other_program = await unit(
            type="program", name="BTech MECH", code="BT-MECH", parent_id=other_dept["id"]
        )

        term = await admin.post(
            "/timetable/terms",
            json={
                "school_id": school["id"],
                "term_code": "2026-S1",
                "start_date": "2026-07-01",
                "end_date": "2026-11-30",
            },
        )
        assert term.status_code == 201, term.text
        term_id = term.json()["id"]

    async with make_client("super-admin", user_id="school.incharge") as si:
        approved = await si.post(f"/timetable/terms/{term_id}/approve")
        assert approved.status_code == 200, approved.text

    async with make_client("super-admin") as admin:
        sections = {}
        for label in ("3A", "3B"):
            created = await admin.post(
                "/timetable/sections",
                json={"program_id": program["id"], "label": label, "term_code": "2026-S1"},
            )
            assert created.status_code == 201, created.text
            sections[label] = created.json()["id"]

        # A Section in a sibling Department — the target of scope-isolation checks.
        other = await admin.post(
            "/timetable/sections",
            json={"program_id": other_program["id"], "label": "1A", "term_code": "2026-S1"},
        )
        assert other.status_code == 201, other.text

    return {
        "university": uni["id"],
        "school": school["id"],
        "department": dept["id"],
        "program": program["id"],
        "other_department": other_dept["id"],
        "other_program": other_program["id"],
        "other_section": other.json()["id"],
        "term_id": term_id,
        "section_3a": sections["3A"],
        "section_3b": sections["3B"],
    }


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    from unicore.modules.onboarding.schemas import CSV_COLUMNS_V1

    header = ",".join(CSV_COLUMNS_V1)
    lines = [header]
    for row in rows:
        lines.append(",".join(row.get(col, "") for col in CSV_COLUMNS_V1))
    return ("\n".join(lines) + "\n").encode()


def student_row(n: int, **overrides: str) -> dict[str, str]:
    row = {
        "sif_id": f"SIF-{n:05d}",
        "full_name": f"Student {n}",
        "date_of_birth": "15-08-2006",
        "gender": "F",
        "mobile": f"90000{n:05d}",
        "email": f"student{n}@uni.example",
        "program_code": "BT-CSE",
        "section_label": "3A",
        "admission_year": "2026",
        "roll_number": f"R-{n:04d}",
    }
    row.update(overrides)
    return row
