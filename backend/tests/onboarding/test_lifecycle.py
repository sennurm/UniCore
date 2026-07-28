"""Allotment, transfer, withdrawal, membership-as-of-date. TC-ONB-013/014."""

from datetime import date, timedelta

import httpx

from tests.onboarding.conftest import csv_bytes, student_row
from unicore.core.db import get_sessionmaker
from unicore.modules.onboarding import service as onb_service


async def _import_one(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/onboarding/imports",
        data={"term_code": "2026-S1"},
        files={"file": ("x.csv", csv_bytes([student_row(1)]), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_reallotment_preserves_history(make_client, campus, audit_rows) -> None:
    """TC-ONB-013: a dated move closes the old membership without rewriting history —
    reads before the effective date still resolve to the old Section."""
    import uuid

    tomorrow = date.today() + timedelta(days=1)
    async with make_client("system-admin") as staff:
        await _import_one(staff)
        roster_a = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        user_id = roster_a[0]["user_id"]

        moved = await staff.post(
            "/onboarding/allotments",
            json={
                "user_id": user_id,
                "section_id": campus["section_3b"],
                "effective_from": tomorrow.isoformat(),
            },
        )
        assert moved.status_code == 201

        today_a = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        future_b = await staff.get(
            f"/onboarding/sections/{campus['section_3b']}/roster",
            params={"as_of": tomorrow.isoformat()},
        )
        future_a = await staff.get(
            f"/onboarding/sections/{campus['section_3a']}/roster",
            params={"as_of": tomorrow.isoformat()},
        )
    assert len(today_a) == 1        # still in 3A today — the move is dated
    assert len(future_b.json()) == 1  # in 3B from tomorrow
    assert future_a.json() == []      # and out of 3A from tomorrow

    async with get_sessionmaker()() as session:
        today_membership = await onb_service.membership_as_of(
            session, uuid.UUID(user_id), date.today()
        )
        future_membership = await onb_service.membership_as_of(
            session, uuid.UUID(user_id), tomorrow
        )
    assert str(today_membership.section_id) == campus["section_3a"]
    assert str(future_membership.section_id) == campus["section_3b"]
    assert await audit_rows("onb.section.alloted")


async def test_withdrawal_revokes_and_removes_from_future_sessions(
    make_client, campus, audit_rows
) -> None:
    """TC-ONB-014: withdrawn students leave future rosters; history is retained."""
    async with make_client("system-admin") as staff:
        await _import_one(staff)
        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        user_id = roster[0]["user_id"]

        withdrawn = await staff.post(
            f"/onboarding/students/{user_id}/withdraw",
            data={"reason": "left the programme", "effective_from": date.today().isoformat()},
        )
        assert withdrawn.status_code == 202

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        future = await staff.get(
            f"/onboarding/sections/{campus['section_3a']}/roster", params={"as_of": tomorrow}
        )
    assert future.json() == []
    assert await audit_rows("onb.student.withdrawn")


async def test_transfer_is_system_admin_only(make_client, campus) -> None:
    """Transfers cross org-unit scopes — office staff cannot execute them."""
    async with make_client("system-admin") as staff:
        await _import_one(staff)
        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
        user_id = roster[0]["user_id"]

    body = {
        "user_id": user_id,
        "new_program_id": campus["other_program"],
        "effective_from": date.today().isoformat(),
    }
    async with make_client(user_id="plain.office") as nobody:
        assert (await nobody.post("/onboarding/transfers", json=body)).status_code == 403

    async with make_client("system-admin") as staff:
        moved = await staff.post("/onboarding/transfers", json=body)
        assert moved.status_code == 202
        # Section-less until re-allotment (ONB-FR-11 mirrors PRM's post-commit rule).
        roster = (await staff.get(f"/onboarding/sections/{campus['section_3a']}/roster")).json()
    assert roster == []


async def test_single_student_add_uses_same_pipeline(make_client, campus) -> None:
    """TC-ONB-... mid-term add: identical validation, so a bad Program still fails."""
    async with make_client("system-admin") as staff:
        good = await staff.post(
            "/onboarding/students",
            json={
                "erp_id": "ERP-99999",
                "full_name": "Late Joiner",
                "program_code": "BT-CSE",
                "section_label": "3A",
                "term_code": "2026-S1",
                "admission_year": 2026,
                "roll_number": "R-7777",
                "mobile": "9000099999",
            },
        )
        assert good.status_code == 201

        bad = await staff.post(
            "/onboarding/students",
            json={
                "erp_id": "ERP-99998",
                "full_name": "Wrong Program",
                "program_code": "DOES-NOT-EXIST",
                "section_label": "3A",
                "term_code": "2026-S1",
                "admission_year": 2026,
                "roll_number": "R-7778",
                "mobile": "9000099998",
            },
        )
    assert bad.status_code == 422
    assert "program_code" in bad.json()["detail"]
