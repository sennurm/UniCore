"""Batch (admission cohort) and ladder position (ONB-FR-19/20/21).

Maps TC-ONB-020..029.
"""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row


async def _upload(
    client: httpx.AsyncClient, rows: list[dict[str, str]], **defaults: str
) -> httpx.Response:
    return await client.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1", **defaults},
        files={"file": ("intake.csv", csv_bytes(rows), "text/csv")},
    )


async def _profile(admin: httpx.AsyncClient, section_id: str) -> list[dict]:
    return (await admin.get(f"/onboarding/sections/{section_id}/roster")).json()


async def test_batch_is_created_once_and_reused(make_client, campus, audit_rows) -> None:
    """TC-ONB-020/021."""
    async with make_client("super-admin") as admin:
        first = await _upload(admin, [student_row(1)])
        assert first.status_code == 201, first.text
        assert first.json()["rows_created"] == 1

        second = await _upload(admin, [student_row(2)])
        assert second.json()["rows_created"] == 1

        codes = await _batch_codes(campus["program"])
    assert codes == {"BT-CSE-2026"}, "a second batch was created for the same cohort"


async def test_position_defaults_to_one(make_client, campus) -> None:
    """TC-ONB-022: blank in file and screen means first year, first semester."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1, position="")])
        positions = await _positions(campus["program"])
    assert positions == {1}


async def test_screen_default_fills_blanks_only(make_client, campus) -> None:
    """TC-ONB-023: a value in the file always wins over the picker."""
    async with make_client("super-admin") as admin:
        await _upload(
            admin,
            [student_row(1, position="3"), student_row(2, position="")],
            default_position="5",
        )
        positions = await _positions(campus["program"])
    assert positions == {3, 5}


async def test_position_beyond_the_ladder_is_rejected(make_client, campus) -> None:
    """TC-ONB-024: clamping would place a student in a term that does not exist."""
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        response = await _upload(
            admin, [student_row(1, position="9"), student_row(2, position="7")]
        )
        body = response.json()
        errors = (await admin.get(f"/onboarding/imports/{body['id']}/errors")).json()
    assert (body["rows_created"], body["rows_rejected"]) == (1, 1)
    assert errors[0]["field"] == "position"
    assert "1..8" in errors[0]["reason"]


async def test_lateral_entrant_joins_the_graduating_cohort(make_client, campus) -> None:
    """TC-ONB-027: a Programme that declares lateral entry at semester 3 puts a
    2026 lateral entrant in the 2025 cohort — they graduate with that intake."""
    async with make_client("super-admin") as admin:
        await admin.put(
            f"/org/units/{campus['program']}",
            json={"duration_years": 4, "lateral_entry_semester": 3},
        )
        await _upload(
            admin,
            [
                student_row(1, position="1", admission_year="2026"),
                student_row(2, position="3", admission_year="2026"),
            ],
        )
        codes = await _batch_codes(campus["program"])
    assert codes == {"BT-CSE-2025", "BT-CSE-2026"}


async def test_continuing_student_is_not_mistaken_for_a_lateral_entrant(
    make_client, campus
) -> None:
    """A mid-programme backfill carries the student's CURRENT position, not their
    entry position. Without a declared lateral entry point there is no offset, so
    a 2024 admission sitting in semester 3 stays in the 2024 cohort."""
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        await _upload(admin, [student_row(1, position="3", admission_year="2024")])
        codes = await _batch_codes(campus["program"])
    assert codes == {"BT-CSE-2024"}, "a continuing student was pushed into an earlier cohort"


async def test_batch_is_decided_once_and_not_recomputed(make_client, campus) -> None:
    """Re-importing with a changed admission_year must not silently move cohorts."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1, admission_year="2026")])
        moved = await _upload(admin, [student_row(1, admission_year="2027")])
        body = moved.json()
        errors = (await admin.get(f"/onboarding/imports/{body['id']}/errors")).json()
        codes = await _batch_codes(campus["program"])
    assert body["rows_rejected"] == 1
    assert errors[0]["field"] == "admission_year"
    assert codes == {"BT-CSE-2026"}, "the student's cohort was rewritten by a re-import"


async def test_batch_template_change_is_not_retroactive(make_client, campus) -> None:
    """TC-ONB-028: renaming cohorts already issued would rewrite history."""
    from unicore.core.db import get_sessionmaker
    from unicore.core.security import AuthContext
    from unicore.modules.org import service as org_service

    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1, admission_year="2026")])

        async with get_sessionmaker()() as session:
            await org_service.set_setting(
                session,
                AuthContext(user_id="tester", session_id="s", role_names=("super-admin",)),
                "batch_name_template",
                "{programme_code}/{joining_year}",
            )

        await _upload(admin, [student_row(2, admission_year="2027")])
        codes = await _batch_codes(campus["program"])
    assert codes == {"BT-CSE-2026", "BT-CSE/2027"}


async def test_cadence_change_refused_while_students_occupy_the_ladder(
    make_client, campus
) -> None:
    """A semester→yearly switch halves the ladder; students above it would strand."""
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        await _upload(admin, [student_row(1, position="7")])

        refused = await admin.put(
            f"/org/units/{campus['school']}", json={"cadence": "yearly"}
        )
    assert refused.status_code == 409
    assert "position 7" in refused.json()["detail"]


async def test_cadence_change_allowed_when_nobody_is_stranded(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        await _upload(admin, [student_row(1, position="2")])

        changed = await admin.put(
            f"/org/units/{campus['school']}", json={"cadence": "yearly"}
        )
    assert changed.status_code == 200, changed.text
    assert changed.json()["cadence"] == "yearly"
    # An explicit decision clears the migration's guess flag.
    assert changed.json()["cadence_unconfirmed"] is False


async def _positions(program_id: str) -> set[int]:
    import uuid as uuid_mod

    from unicore.core.db import get_sessionmaker
    from unicore.modules.onboarding import dao

    async with get_sessionmaker()() as session:
        counts = await dao.count_students_by_position(session, uuid_mod.UUID(program_id))
    return set(counts)


async def _batch_codes(program_id: str) -> set[str]:
    import uuid as uuid_mod

    from unicore.core.db import get_sessionmaker
    from unicore.modules.onboarding import dao

    async with get_sessionmaker()() as session:
        batches = await dao.list_batches_for_programs(session, [uuid_mod.UUID(program_id)])
    return {b.code for b in batches}
