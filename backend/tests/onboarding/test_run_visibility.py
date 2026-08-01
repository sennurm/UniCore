"""Import-run visibility gaps found reviewing the page against ONB (§8, FR-14/17/20/21).

Each of these covers something the screen could not previously see or do.
"""

import httpx

from tests.onboarding.conftest import csv_bytes, student_row


async def _upload(
    client: httpx.AsyncClient, rows: list[dict[str, str]], **extra: str
) -> httpx.Response:
    return await client.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1", **extra},
        files={"file": ("intake.csv", csv_bytes(rows), "text/csv")},
    )


async def test_run_summary_names_the_batches_it_created(make_client, campus) -> None:
    """ONB §8: a typo'd admission_year creates a real cohort, so the run must say so."""
    async with make_client("super-admin") as admin:
        first = await _upload(admin, [student_row(1, admission_year="2026")])
        assert first.json()["created_batches"] == ["BT-CSE-2026"]

        # A second run into the same cohort creates nothing new.
        second = await _upload(admin, [student_row(2, admission_year="2026")])
        assert second.json()["created_batches"] == []

        # A mistyped year is visible immediately rather than silently accepted.
        typo = await _upload(admin, [student_row(3, admission_year="2062")])
        assert typo.json()["created_batches"] == ["BT-CSE-2062"]

        listed = (await admin.get("/onboarding/imports")).json()
    # The dashboard shows past runs too, so the field has to survive the request.
    assert [r["created_batches"] for r in listed][-1] == ["BT-CSE-2026"]


async def test_parked_run_can_be_released_and_then_delivers(make_client, campus) -> None:
    """A run held by the risky-change guardrail must have a way forward."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(n) for n in range(1, 6)])

        # Move every student to another Programme: >20% risky changes parks the run.
        moved = [student_row(n, program_code="BT-MECH", section_label="1A") for n in range(1, 6)]
        parked = await _upload(admin, moved)
        run = parked.json()
        assert run["status"] == "needs-review", run

        blocked = await admin.post(f"/onboarding/imports/{run['id']}/deliver-credentials")
        assert blocked.status_code == 409
        assert "release it before delivery" in blocked.json()["detail"]

        released = await admin.post(f"/onboarding/imports/{run['id']}/confirm")
        assert released.status_code == 200
        assert released.json()["status"] == "committed"

        delivered = await admin.post(f"/onboarding/imports/{run['id']}/deliver-credentials")
    assert delivered.status_code == 200


async def test_confirming_a_committed_run_is_refused(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        run = (await _upload(admin, [student_row(1)])).json()
        assert run["status"] == "committed"
        again = await admin.post(f"/onboarding/imports/{run['id']}/confirm")
    assert again.status_code == 404


async def test_roster_carries_batch_and_position(make_client, campus) -> None:
    """ONB-FR-20: the screen shows the position, and derives the year from it."""
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        await _upload(admin, [student_row(1, position="3")])
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert len(roster) == 1
    row = roster[0]
    assert row["position"] == 3
    assert row["year"] == 2, "year is derived from the semester, not stored"
    # No lateral entry declared on the Programme, so no offset: joining year wins.
    assert row["batch_code"] == "BT-CSE-2026"


async def test_roster_as_of_reads_history(make_client, campus) -> None:
    """ONB-FR-17: 'as of any date' — the screen can now ask for an earlier date."""
    async with make_client("super-admin") as admin:
        await _upload(admin, [student_row(1)])
        before = await admin.get(
            f"/onboarding/sections/{campus['section_3a']}/roster",
            params={"as_of": "2020-01-01"},
        )
        today = await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")
    assert before.json() == [], "membership did not exist in 2020"
    assert len(today.json()) == 1


async def test_screen_defaults_reach_the_pipeline(make_client, campus) -> None:
    """ONB-FR-21 end to end: the form fields the page now sends actually apply."""
    async with make_client("super-admin") as admin:
        await admin.put(f"/org/units/{campus['program']}", json={"duration_years": 4})
        rows = [student_row(1, program_code="", position=""), student_row(2, position="5")]
        run = await _upload(
            admin, rows, default_program_code="BT-CSE", default_position="3"
        )
        assert run.json()["rows_created"] == 2, run.text
        roster = (await admin.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    positions = sorted(r["position"] for r in roster)
    assert positions == [3, 5], "the default filled the blank row and left the stated one alone"
