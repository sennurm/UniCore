"""CSV import pipeline. Maps TC-ONB-001/002/003/004/005/006/010/011."""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row


async def _upload(client: httpx.AsyncClient, rows: list[dict[str, str]]) -> httpx.Response:
    return await client.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1"},
        files={"file": ("intake.csv", csv_bytes(rows), "text/csv")},
    )


async def test_clean_import_creates_accounts(make_client, campus, audit_rows) -> None:
    """TC-ONB-001: valid rows become accounts with Section membership."""
    async with make_client("system-admin") as staff:
        response = await _upload(staff, [student_row(n) for n in range(1, 6)])
        assert response.status_code == 201, response.text
        batch = response.json()
        assert (batch["rows_created"], batch["rows_rejected"]) == (5, 0)
        assert batch["status"] == "committed"

        roster = await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")
        assert len(roster.json()) == 5
        assert roster.json()[0]["credential_delivery"] == "pending"
    assert await audit_rows("onb.import.completed")


async def test_partial_commit_with_error_report(make_client, campus) -> None:
    """TC-ONB-002/006: good rows commit; bad rows land in a downloadable report."""
    rows = [
        student_row(1),
        student_row(2, sif_id=""),  # missing mandatory field
        student_row(3, mobile="", email=""),  # no contact channel
        student_row(4, program_code="NOPE"),  # unknown program
        student_row(5, section_label="9Z"),  # section not created
        student_row(6, admission_year="not-a-year"),  # bad year
        student_row(7),
    ]
    async with make_client("system-admin") as staff:
        batch = (await _upload(staff, rows)).json()
        assert batch["rows_created"] == 2
        assert batch["rows_rejected"] == 5

        errors = (await staff.get(f"/onboarding/imports/{batch['id']}/errors")).json()
        fields = {e["field"] for e in errors}
        assert fields == {
            # A row with neither identifier is rejected on the pair, not on SIF
            # alone — either one is now sufficient (business rule 1).
            "sif_id/enrollment_id",
            "mobile/email",
            "program_code",
            "section_label",
            "admission_year",
        }

        report = await staff.get(f"/onboarding/imports/{batch['id']}/errors.csv")
        assert report.status_code == 200
        assert "row_number,field,reason,raw_row" in report.text
        assert "attachment" in report.headers["content-disposition"]


async def test_reimport_is_idempotent(make_client, campus) -> None:
    """TC-ONB-003: re-running the same file creates nothing new."""
    rows = [student_row(n) for n in range(1, 4)]
    async with make_client("system-admin") as staff:
        first = (await _upload(staff, rows)).json()
        second = (await _upload(staff, rows)).json()
    assert first["rows_created"] == 3
    assert (second["rows_created"], second["rows_unchanged"]) == (0, 3)


async def test_in_file_duplicate_rejected(make_client, campus) -> None:
    """TC-ONB-004: first occurrence wins, later ones are reported."""
    rows = [student_row(1), student_row(1, roll_number="R-9999")]
    async with make_client("system-admin") as staff:
        batch = (await _upload(staff, rows)).json()
        errors = (await staff.get(f"/onboarding/imports/{batch['id']}/errors")).json()
    assert batch["rows_created"] == 1
    assert batch["rows_rejected"] == 1
    assert "duplicate" in errors[0]["reason"]


async def test_roll_number_collision_rejected(make_client, campus) -> None:
    """TC-ONB-010: same roll number, same Program + year, different student."""
    async with make_client("system-admin") as staff:
        await _upload(staff, [student_row(1, roll_number="R-0001")])
        batch = (await _upload(staff, [student_row(2, roll_number="R-0001")])).json()
        errors = (await staff.get(f"/onboarding/imports/{batch['id']}/errors")).json()
    assert batch["rows_rejected"] == 1
    assert errors[0]["field"] == "roll_number"


async def test_import_requires_permission(make_client, campus) -> None:
    """TC-ONB-011: an unprivileged role cannot import."""
    async with make_client(user_id="random.user") as nobody:
        assert (await _upload(nobody, [student_row(1)])).status_code == 403


async def test_oversized_and_malformed_files_rejected(make_client, campus) -> None:
    """§8 pre-parse gate: whole-file rejection with a clear reason."""
    async with make_client("system-admin") as staff:
        bad_header = await staff.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("x.csv", b"name,age\nfoo,3\n", "text/csv")},
        )
        assert bad_header.status_code == 422
        assert "schema v1" in bad_header.json()["detail"]

        not_utf8 = await staff.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("x.csv", b"\xff\xfe\x00bad", "text/csv")},
        )
        assert not_utf8.status_code == 422


async def test_credential_delivery_activates_students(make_client, campus) -> None:
    """ONB-FR-06: generation → delivery → ACTIVE, with per-student status."""
    from unicore.modules.auth.providers import sms_provider

    async with make_client("system-admin") as staff:
        batch = (await _upload(staff, [student_row(n) for n in range(1, 4)])).json()
        result = await staff.post(f"/onboarding/imports/{batch['id']}/deliver-credentials")
        assert result.json() == {"delivered": 3, "failed": 0}

        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert {r["credential_delivery"] for r in roster} == {"delivered"}
    assert {r["status"] for r in roster} == {"active"}
    assert sum("temporary password" in m.body for m in sms_provider.outbox) == 3
