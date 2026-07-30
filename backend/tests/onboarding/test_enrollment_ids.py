"""Two one-to-one student ids: SIF (issued at admission) + enrollment (issued later)."""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row


async def _import_students(client: httpx.AsyncClient, count: int) -> None:
    response = await client.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1"},
        files={"file": ("x.csv", csv_bytes([student_row(n) for n in range(1, count + 1)]),
                        "text/csv")},
    )
    assert response.status_code == 201, response.text


def _enrollment_csv(pairs: list[tuple[str, str]]) -> bytes:
    lines = ["sif_id,enrollment_id"] + [f"{s},{e}" for s, e in pairs]
    return ("\n".join(lines) + "\n").encode()


async def _assign(client: httpx.AsyncClient, pairs: list[tuple[str, str]]) -> dict:
    response = await client.post(
        "/onboarding/enrollment-ids",
        files={"file": ("enrol.csv", _enrollment_csv(pairs), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_enrollment_numbers_assigned_after_admission(
    make_client, campus, audit_rows
) -> None:
    """Students onboard with SIF only; enrollment numbers arrive in a later upload."""
    async with make_client("system-admin") as staff:
        await _import_students(staff, 3)
        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        assert {r["enrollment_id"] for r in roster} == {None}  # not issued yet

        result = await _assign(staff, [
            ("SIF-00001", "TU2026CSE0001"),
            ("SIF-00002", "TU2026CSE0002"),
        ])
        assert (result["rows_assigned"], result["rows_rejected"]) == (2, 0)

        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        by_sif = {r["sif_id"]: r for r in roster}
    assert by_sif["SIF-00001"]["enrollment_id"] == "TU2026CSE0001"
    assert by_sif["SIF-00003"]["enrollment_id"] is None  # untouched
    assert await audit_rows("user.enrollment-id.assigned")


async def test_reupload_is_idempotent_and_corrections_are_audited(
    make_client, campus, audit_rows
) -> None:
    async with make_client("system-admin") as staff:
        await _import_students(staff, 1)
        first = await _assign(staff, [("SIF-00001", "TU2026CSE0001")])
        again = await _assign(staff, [("SIF-00001", "TU2026CSE0001")])
        corrected = await _assign(staff, [("SIF-00001", "TU2026CSE9999")])

        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert first["rows_assigned"] == 1
    assert (again["rows_assigned"], again["rows_unchanged"]) == (0, 1)
    assert corrected["rows_assigned"] == 1  # correction allowed…
    assert roster[0]["enrollment_id"] == "TU2026CSE9999"
    events = await audit_rows("user.enrollment-id.assigned")
    assert len(events) == 2  # …and audited before/after
    assert events[0]["before"]["enrollment_id"] == "TU2026CSE0001"


async def test_enrollment_numbers_are_university_wide_unique(make_client, campus) -> None:
    async with make_client("system-admin") as staff:
        await _import_students(staff, 2)
        await _assign(staff, [("SIF-00001", "TU2026CSE0001")])
        clash = await _assign(staff, [("SIF-00002", "TU2026CSE0001")])
    assert clash["rows_rejected"] == 1
    assert "already belongs to" in clash["errors"][0]["reason"]


async def test_row_errors_reported_without_failing_the_batch(make_client, campus) -> None:
    async with make_client("system-admin") as staff:
        await _import_students(staff, 1)
        result = await _assign(staff, [
            ("SIF-00001", "TU2026CSE0001"),          # valid
            ("SIF-NOBODY", "TU2026CSE0002"),         # unknown student
            ("SIF-00001", ""),                       # missing number
            ("SIF-00001", "TU2026CSE0003"),          # ok, corrects row 1
        ])
    assert result["rows_rejected"] == 2
    fields = {e["field"] for e in result["errors"]}
    assert fields == {"sif_id", "enrollment_id"}


async def test_students_are_searchable_by_either_id(make_client, campus) -> None:
    """Both ids are one-to-one with the student, so either finds them."""
    async with make_client("system-admin") as staff:
        await _import_students(staff, 1)
        await _assign(staff, [("SIF-00001", "TU2026CSE0001")])

        by_sif = (await staff.get("/rbac/directory", params={"search": "SIF-00001"})).json()
        by_enrollment = (
            await staff.get("/rbac/directory", params={"search": "TU2026CSE0001"})
        ).json()
    assert len(by_sif) == 1
    assert by_enrollment[0]["user_id"] == by_sif[0]["user_id"]
    assert by_enrollment[0]["sif_id"] == "SIF-00001"


async def test_enrollment_template_is_downloadable(make_client) -> None:
    async with make_client("system-admin") as staff:
        listed = {t["key"] for t in (await staff.get("/templates")).json()}
        assert "enrollment-ids" in listed
        body = (await staff.get("/templates/enrollment-ids.csv")).text
    header = [ln for ln in body.splitlines() if not ln.startswith("#")][0]
    assert header == "sif_id,enrollment_id"


async def test_enrollment_supplied_at_import_and_matches_later(make_client, campus) -> None:
    """Enrollment No is the canonical id: it may be supplied at import, and once
    issued a file carrying it resolves the same student even via that column."""
    from tests.onboarding.conftest import csv_bytes, student_row

    async def upload(rows: list[dict]) -> dict:
        response = await staff.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("x.csv", csv_bytes(rows), "text/csv")},
        )
        assert response.status_code == 201, response.text
        return response.json()

    async with make_client("system-admin") as staff:
        # Enrollment number known up front — stored on creation.
        first = await upload([student_row(1, enrollment_id="TU2026CSE0001")])
        assert first["rows_created"] == 1

        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        assert roster[0]["enrollment_id"] == "TU2026CSE0001"

        # Re-import of the same student is still idempotent.
        again = await upload([student_row(1, enrollment_id="TU2026CSE0001")])
        assert (again["rows_created"], again["rows_unchanged"]) == (0, 1)

        directory = (
            await staff.get("/rbac/directory", params={"search": "TU2026CSE0001"})
        ).json()
    assert len(directory) == 1
    assert directory[0]["sif_id"] == "SIF-00001"
