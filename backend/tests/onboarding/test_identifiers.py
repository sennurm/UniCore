"""Either identifier is enough (business rule 1, ONB-FR-02/04/05).

A student may arrive named by the SIF id issued at admission, by the Enrollment
No issued later, or by both — a mid-programme ERP extract often has only the
latter. What is *not* allowed is a row that names nobody, or one that names two
different people.
"""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row


async def _upload(client: httpx.AsyncClient, rows: list[dict[str, str]]) -> httpx.Response:
    return await client.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1"},
        files={"file": ("intake.csv", csv_bytes(rows), "text/csv")},
    )


async def _errors(client: httpx.AsyncClient, run: dict) -> list[dict]:
    return (await client.get(f"/onboarding/imports/{run['id']}/errors")).json()


async def test_sif_only_row_creates_a_student(make_client, campus) -> None:
    """The admission-time case: SIF exists, enrollment number not yet issued."""
    async with make_client("super-admin") as admin:
        run = (await _upload(admin, [student_row(1, enrollment_id="")])).json()
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert run["rows_created"] == 1
    assert roster[0]["sif_id"] == "SIF-00001"
    assert roster[0]["enrollment_id"] is None


async def test_both_ids_on_one_row_are_accepted(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        run = (await _upload(admin, [student_row(1, enrollment_id="TU2026CSE0001")])).json()
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert run["rows_created"] == 1
    assert (roster[0]["sif_id"], roster[0]["enrollment_id"]) == ("SIF-00001", "TU2026CSE0001")


async def test_enrollment_only_row_updates_an_existing_student(make_client, campus) -> None:
    """The mid-programme case: the extract carries only the canonical number."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1, enrollment_id="TU2026CSE0001")])

        # Same student, named only by enrollment number, with a corrected name.
        run = (
            await _upload(
                admin,
                [
                    student_row(
                        1, sif_id="", enrollment_id="TU2026CSE0001", full_name="Ananya R Iyer"
                    )
                ],
            )
        ).json()
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert (run["rows_created"], run["rows_updated"]) == (0, 1)
    assert len(roster) == 1, "matched the existing student rather than creating a second"
    assert roster[0]["full_name"] == "Ananya R Iyer"
    assert roster[0]["sif_id"] == "SIF-00001", "the SIF was not wiped by a row that omitted it"


async def test_row_with_neither_identifier_is_rejected(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        run = (await _upload(admin, [student_row(1, sif_id="", enrollment_id="")])).json()
        errors = await _errors(admin, run)
    assert run["rows_rejected"] == 1
    assert errors[0]["field"] == "sif_id/enrollment_id"
    assert "at least one identifier" in errors[0]["reason"]


async def test_unknown_enrollment_only_row_is_rejected(make_client, campus) -> None:
    """Enrollment numbers are issued to students who already exist, so one that
    matches nobody is a typo — creating here would mint a phantom student."""
    async with make_client("super-admin") as admin:
        run = (
            await _upload(admin, [student_row(1, sif_id="", enrollment_id="TU-NOBODY")])
        ).json()
        errors = await _errors(admin, run)
    assert run["rows_rejected"] == 1
    assert errors[0]["field"] == "enrollment_id"
    assert "no student holds Enrollment No" in errors[0]["reason"]


async def test_ids_naming_two_different_students_are_rejected(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        await _upload(
            admin,
            [
                student_row(1, enrollment_id="TU2026CSE0001"),
                student_row(2, enrollment_id="TU2026CSE0002"),
            ],
        )
        # Student 1's SIF paired with student 2's enrollment number.
        run = (
            await _upload(admin, [student_row(1, enrollment_id="TU2026CSE0002")])
        ).json()
        errors = await _errors(admin, run)
    assert run["rows_rejected"] == 1
    assert errors[0]["field"] == "enrollment_id"
    assert "two different students" in errors[0]["reason"]


async def test_in_file_duplicate_detected_on_enrollment_number(make_client, campus) -> None:
    """ONB-FR-05 now covers either id: two rows sharing only an enrollment
    number are still the same person."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1, enrollment_id="TU2026CSE0001")])
        run = (
            await _upload(
                admin,
                [
                    student_row(1, sif_id="", enrollment_id="TU2026CSE0001"),
                    student_row(2, sif_id="", enrollment_id="TU2026CSE0001"),
                ],
            )
        ).json()
        errors = await _errors(admin, run)
    assert run["rows_rejected"] == 1
    assert errors[0]["field"] == "enrollment_id"
    assert "in-file duplicate" in errors[0]["reason"]


async def test_downloaded_template_uploads_back_unchanged(make_client, campus) -> None:
    """ONB-FR-16: the template is downloadable and usable. It ships with `#` notes,
    and the UI promises they are ignored — so the round trip must actually work."""
    async with make_client("super-admin") as admin:
        template = await admin.get("/templates/students.csv")
        assert template.status_code == 200
        assert template.text.lstrip().startswith("#"), "template no longer carries notes"

        run = await admin.post(
            "/onboarding/imports",
            data={"term_code": "2026-S1"},
            files={"file": ("unicore_students_template.csv", template.text, "text/csv")},
        )
    # The sample rows name Programmes that do not exist here, so they are rejected
    # row by row — but the *header* must parse, which is what the notes used to break.
    assert run.status_code == 201, run.text
    assert run.json()["rows_total"] > 0, "the header was not found behind the # notes"


async def test_imported_students_hold_the_student_role(make_client, campus) -> None:
    """A student with no grant can sign in and do nothing (AUTH §1), so the import
    that provisions them grants the role in the same transaction."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1), student_row(2)])
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        grants = (
            await admin.get(f"/rbac/users/{roster[0]['user_id']}/grants")
        ).json()
    assert [g["role_code"] for g in grants] == ["student"]
    assert grants[0]["org_unit_id"] == campus["program"], "scoped to the Programme, not a Section"


async def test_reimport_backfills_the_role_without_duplicating_it(make_client, campus) -> None:
    """Idempotent: re-running a file fixes students provisioned before the role
    existed, and never piles up a second grant for those that have it."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1)])
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        user_id = roster[0]["user_id"]

        await _upload(admin, [student_row(1)])
        grants = (await admin.get(f"/rbac/users/{user_id}/grants")).json()
    assert len([g for g in grants if g["role_code"] == "student"]) == 1


async def test_programme_move_relocates_the_role(make_client, campus) -> None:
    """A student must never hold a grant on a Programme they have left."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1)])
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        user_id = roster[0]["user_id"]

        await _upload(admin, [student_row(1, program_code="BT-MECH", section_label="1A")])
        grants = (await admin.get(f"/rbac/users/{user_id}/grants")).json()

    active = [g for g in grants if g["role_code"] == "student" and g["status"] == "active"]
    assert len(active) == 1, "the student held a grant on both Programmes"
    assert active[0]["org_unit_id"] == campus["other_program"]


async def test_transfer_relocates_the_role(make_client, campus) -> None:
    """ONB-FR-11 transfers go through their own endpoint, not the importer."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1)])
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        user_id = roster[0]["user_id"]

        moved = await admin.post(
            "/onboarding/transfers",
            json={
                "user_id": user_id,
                "new_program_id": campus["other_program"],
                "new_section_id": campus["other_section"],
                "effective_from": "2026-08-01",
            },
        )
        assert moved.status_code == 202, moved.text
        grants = (await admin.get(f"/rbac/users/{user_id}/grants")).json()

    active = [g for g in grants if g["role_code"] == "student" and g["status"] == "active"]
    assert len(active) == 1
    assert active[0]["org_unit_id"] == campus["other_program"]


async def test_role_registry_is_served(make_client, campus) -> None:
    """The screen reads the registry rather than carrying a literal list."""
    async with make_client("super-admin") as admin:
        roles = (await admin.get("/rbac/roles")).json()
    by_code = {r["code"]: r for r in roles}
    assert by_code["student"]["unit_type"] == "program"
    assert by_code["class-incharge"]["term_bound"] is True
    assert "super-admin" in by_code, "the hardcoded UI list had omitted this one"
