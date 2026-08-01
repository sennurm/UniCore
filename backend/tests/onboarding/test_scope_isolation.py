"""Cross-scope reads of student data (project rule: no cross-user data leakage).

`onb:read` says the caller may read rosters — not that they may read *this*
roster. Most holders of it (HoD, Class In-charge, School Incharge) are org-scoped,
so every read keyed on a client-supplied id is checked against the caller's own
subtree. These tests exist because it previously was not.
"""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row


async def _hod_of(admin: httpx.AsyncClient, department_id: str, username: str) -> str:
    user = await admin.post(
        "/user", json={"username": username, "full_name": username, "kind": "staff"}
    )
    assert user.status_code == 201, user.text
    grant = await admin.post(
        "/rbac/grants",
        json={"user_id": user.json()["id"], "role_code": "hod", "org_unit_id": department_id},
    )
    assert grant.status_code == 201, grant.text
    return str(user.json()["id"])


async def test_hod_cannot_read_another_departments_roster(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        await _hod_of(admin, campus["department"], "hod.cse")
        upload = await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("intake.csv", csv_bytes([student_row(1)]), "text/csv")},
        )
        assert upload.status_code == 201, upload.text

    async with make_client("hod", user_id="hod.cse") as hod:
        own = await hod.get(f"/onboarding/sections/{campus['section_3a']}/roster")
        assert own.status_code == 200, own.text
        assert len(own.json()) == 1  # their own Department's students are readable

        other = await hod.get(f"/onboarding/sections/{campus['other_section']}/roster")
    assert other.status_code == 403, (
        "a HoD read a Section outside their Department — student PII leaked across scopes"
    )


async def test_university_scoped_admin_still_reads_any_roster(make_client, campus) -> None:
    """The check narrows scoped roles without breaking university-wide ones."""
    async with make_client("super-admin") as admin:
        for section in (campus["section_3a"], campus["other_section"]):
            response = await admin.get(f"/onboarding/sections/{section}/roster")
            assert response.status_code == 200, response.text


async def test_import_runs_are_not_visible_across_scopes(make_client, campus) -> None:
    """An import run's rows — and its error report — carry the uploaded PII."""
    async with make_client("super-admin") as admin:
        await _hod_of(admin, campus["department"], "hod.cse")
        upload = await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={
                "file": (
                    "intake.csv",
                    csv_bytes([student_row(1), student_row(2, sif_id="")]),
                    "text/csv",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        batch_id = upload.json()["id"]
        assert len((await admin.get("/onboarding/imports")).json()) == 1

    # office-staff granted on one School is scoped, so another actor's run is invisible.
    async with make_client("super-admin") as admin:
        await _hod_of(admin, campus["other_department"], "hod.mec")

    async with make_client("hod", user_id="hod.mec") as scoped:
        listing = await scoped.get("/onboarding/imports")
        assert listing.status_code == 200
        assert listing.json() == [], "another actor's import run was listed"

        errors = await scoped.get(f"/onboarding/imports/{batch_id}/errors")
    assert errors.status_code == 404, "error report exposed raw CSV rows across scopes"
