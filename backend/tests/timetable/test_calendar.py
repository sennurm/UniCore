"""University holidays, School working days, and the resolver over them.

Maps TTM-FR-26/27/28 and §4 rules 11–13 (TC-TTM-045…060).
"""

from collections.abc import Callable, Iterator
from datetime import date

import httpx
import pytest

from unicore.modules.timetable import service as ttm_service

TERM = "2026-S1"

# November 2026: Saturdays fall on the 7th, 14th, 21st and 28th — so the 7th is
# the 1st Saturday and the 21st is the 3rd, which is what the nth-weekday rules
# below are asserting against.
SAT_1ST, SAT_2ND, SAT_3RD, SAT_4TH = "2026-11-07", "2026-11-14", "2026-11-21", "2026-11-28"

# January 2026 has five Saturdays — 3, 10, 17, 24, 31 — which is the boundary
# "1st and 3rd" is specified against.
JAN_SATURDAYS = ["2026-01-03", "2026-01-10", "2026-01-17", "2026-01-24", "2026-01-31"]

ALL_SEVEN = {str(n): True for n in range(1, 8)}
MON_TO_FRI = {str(n): True for n in range(1, 6)}
ALTERNATE_SATURDAYS = {**MON_TO_FRI, "6": [1, 3]}


async def _pattern(
    admin: httpx.AsyncClient, school_id: str, days: dict, term_code: str | None = None
):
    body: dict = {"days": days}
    if term_code is not None:
        body["term_code"] = term_code
    return await admin.put(f"/timetable/schools/{school_id}/working-pattern", json=body)


async def _resolve(client: httpx.AsyncClient, school_id: str, start: str, end: str) -> list[dict]:
    response = await client.get(
        f"/timetable/schools/{school_id}/calendar",
        params={"from_date": start, "to_date": end},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _day(client: httpx.AsyncClient, school_id: str, on: str) -> dict:
    return (await _resolve(client, school_id, on, on))[0]


# --- working patterns (TTM-FR-27) ---------------------------------------------


async def test_unconfigured_school_falls_back_visibly(make_client, campus) -> None:
    """TC-TTM-057. A School nobody configured must not resolve to zero teaching
    days — but the default must be visible, not passed off as a decision."""
    async with make_client("super-admin") as admin:
        wednesday = await _day(admin, campus["school"], "2026-11-11")
        sunday = await _day(admin, campus["school"], "2026-11-08")
        pattern = (
            await admin.get(f"/timetable/schools/{campus['school']}/working-pattern")
        ).json()

    assert wednesday["teaching"] is True
    assert sunday["teaching"] is False
    assert wednesday["decided_by"] == "school-pattern-default"
    assert pattern["is_default"] is True
    assert "no pattern set" in wednesday["detail"]


async def test_a_school_can_teach_all_seven_days(make_client, campus) -> None:
    """TC-TTM-045. Nursing and Medicine run clinical postings at the weekend."""
    async with make_client("super-admin") as admin:
        saved = await _pattern(admin, campus["school"], ALL_SEVEN)
        sunday = await _day(admin, campus["school"], "2026-11-08")
    assert saved.status_code == 200, saved.text
    assert sunday["teaching"] is True
    assert sunday["decided_by"] == "school-pattern"


async def test_alternate_saturdays_resolve_by_occurrence(make_client, campus) -> None:
    """TC-TTM-047. "1st and 3rd" counts occurrences within the calendar month —
    never ISO week numbers, which drift across month boundaries."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALTERNATE_SATURDAYS)
        saturdays = {
            d: (await _day(admin, campus["school"], d))["teaching"]
            for d in (SAT_1ST, SAT_2ND, SAT_3RD, SAT_4TH)
        }
    assert saturdays == {SAT_1ST: True, SAT_2ND: False, SAT_3RD: True, SAT_4TH: False}


async def test_a_fifth_saturday_is_not_among_the_first_and_third(make_client, campus) -> None:
    """§8 boundary. Occurrences are counted within the calendar month, so a
    month with five Saturdays simply has two extra non-teaching ones — an ISO
    week-number reading would drift and start teaching them."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALTERNATE_SATURDAYS)
        teaching = {
            d: (await _day(admin, campus["school"], d))["teaching"] for d in JAN_SATURDAYS
        }
    assert teaching == {
        "2026-01-03": True,   # 1st
        "2026-01-10": False,  # 2nd
        "2026-01-17": True,   # 3rd
        "2026-01-24": False,  # 4th
        "2026-01-31": False,  # 5th — not among "1st and 3rd"
    }


async def test_a_school_with_no_teaching_day_is_refused(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        empty = await _pattern(admin, campus["school"], {"6": False})
    assert empty.status_code == 422
    assert "at least one weekday" in empty.text


async def test_a_term_may_override_the_standing_pattern(make_client, campus) -> None:
    """The pattern stands on the School so a new term inherits it; a term that
    genuinely differs overrides without disturbing the School's normal week."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], MON_TO_FRI)
        await _pattern(admin, campus["school"], ALL_SEVEN, term_code=TERM)

        standing = (
            await admin.get(f"/timetable/schools/{campus['school']}/working-pattern")
        ).json()
        overridden = (
            await admin.get(
                f"/timetable/schools/{campus['school']}/working-pattern",
                params={"term_code": TERM},
            )
        ).json()
    assert standing["days"] == MON_TO_FRI
    assert overridden["days"] == ALL_SEVEN


# --- university holidays (TTM-FR-26) ------------------------------------------


async def test_a_vacation_block_is_one_entry(make_client, campus) -> None:
    """TC-TTM-048. Fourteen rows for one semester break is how calendars rot."""
    async with make_client("super-admin") as admin:
        created = await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-09", "to_date": "2026-11-22",
                "label": "Semester break", "kind": "vacation",
            },
        )
        assert created.status_code == 201, created.text
        days = await _resolve(admin, campus["school"], "2026-11-09", "2026-11-22")

    assert all(d["teaching"] is False for d in days)
    assert {d["decided_by"] for d in days} == {"university-holiday"}
    assert days[0]["detail"] == "Semester break (vacation)"


async def test_a_campus_tagged_holiday_leaves_other_campuses_open(make_client, campus) -> None:
    """TC-TTM-049. A regional festival closes where it is observed, not everywhere."""
    async with make_client("super-admin") as admin:
        schools = {}
        for code, campus_code in (("SOA", "CHENNAI"), ("SOB", "COIMBATORE")):
            schools[campus_code] = (
                await admin.post(
                    "/org/units",
                    json={
                        "type": "school", "name": code, "code": code,
                        "parent_id": campus["faculty_division"],
                        "cadence": "semester", "campus_code": campus_code,
                    },
                )
            ).json()["id"]
        await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-11",
                "label": "Local temple festival", "kind": "local",
                "campus_codes": ["CHENNAI"],
            },
        )
        elsewhere = await _day(admin, schools["COIMBATORE"], "2026-11-11")
        observed = await _day(admin, schools["CHENNAI"], "2026-11-11")

    assert elsewhere["teaching"] is True
    assert observed["teaching"] is False
    assert observed["decided_by"] == "university-holiday"


async def test_withdrawing_a_holiday_reopens_the_day(make_client, campus) -> None:
    """TC-TTM-056 in the other direction: widening is always allowed."""
    async with make_client("super-admin") as admin:
        holiday = (
            await admin.post(
                "/timetable/holidays",
                json={
                    "from_date": "2026-11-11", "to_date": "2026-11-11",
                    "label": "Founder's Day", "kind": "local",
                },
            )
        ).json()
        closed = await _day(admin, campus["school"], "2026-11-11")
        await admin.post(f"/timetable/holidays/{holiday['id']}/withdraw")
        reopened = await _day(admin, campus["school"], "2026-11-11")

        listed = (await admin.get("/timetable/holidays")).json()
    assert closed["teaching"] is False and reopened["teaching"] is True
    # Kept, not deleted — a date that used to be closed is what audits ask about.
    assert [h["status"] for h in listed] == ["withdrawn"]


async def test_overlapping_holidays_are_allowed_and_the_narrower_one_decides(
    make_client, campus
) -> None:
    """§8. A public holiday inside a vacation block is normal, not a conflict —
    and the narrower entry is the more specific answer, so it is the one named."""
    async with make_client("super-admin") as admin:
        await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-09", "to_date": "2026-11-22",
                "label": "Semester break", "kind": "vacation",
            },
        )
        await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-11",
                "label": "Republic Day", "kind": "public",
            },
        )
        inside = await _day(admin, campus["school"], "2026-11-11")
        elsewhere = await _day(admin, campus["school"], "2026-11-12")

    assert inside["teaching"] is False and elsewhere["teaching"] is False
    assert "Republic Day" in inside["detail"], "the block was named over the specific day"
    assert "Semester break" in elsewhere["detail"]


async def test_a_school_may_work_part_of_a_vacation_block(make_client, campus) -> None:
    """§8. A Nursing School running ward postings through the first week of a
    break declares that week — not the whole block."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALL_SEVEN)
        await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-09", "to_date": "2026-11-22",
                "label": "Semester break", "kind": "vacation",
            },
        )
        for day in ("2026-11-09", "2026-11-10"):
            declared = await admin.post(
                f"/timetable/schools/{campus['school']}/exceptions",
                json={"on_date": day, "working": True, "reason": "Ward postings continue"},
            )
            assert declared.status_code == 201, declared.text
        days = await _resolve(admin, campus["school"], "2026-11-09", "2026-11-22")

    teaching = [d["on_date"] for d in days if d["teaching"]]
    assert teaching == ["2026-11-09", "2026-11-10"]
    assert all(d["decided_by"] == "school-override" for d in days if d["teaching"])


async def test_a_holiday_on_a_day_the_school_never_teaches_is_a_no_op(
    make_client, campus
) -> None:
    """§8. The date is already non-working, so the holiday changes nothing and
    the School needs no exception to say so."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], MON_TO_FRI)
        before = await _day(admin, campus["school"], "2026-11-08")  # a Sunday
        created = await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-08", "to_date": "2026-11-08",
                "label": "Founder's Day", "kind": "public",
            },
        )
        after = await _day(admin, campus["school"], "2026-11-08")

    assert created.status_code == 201, created.text
    assert before["teaching"] is False and after["teaching"] is False


# --- precedence (§4 rule 11) --------------------------------------------------


async def test_a_school_may_work_through_a_university_holiday(make_client, campus) -> None:
    """TC-TTM-050. The ward does not close for Pongal."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALL_SEVEN)
        await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-11",
                "label": "Pongal", "kind": "public",
            },
        )
        declared = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={
                "on_date": "2026-11-11", "working": True,
                "reason": "Clinical postings continue — the ward does not close.",
            },
        )
        assert declared.status_code == 201, declared.text
        resolved = await _day(admin, campus["school"], "2026-11-11")

    assert resolved["teaching"] is True
    # Working *through a holiday* is a different act from an ordinary working
    # exception, and the answer says which it was.
    assert resolved["decided_by"] == "school-override"
    assert "Pongal" in resolved["detail"]


async def test_the_narrowest_layer_decides_and_says_so(make_client, campus) -> None:
    """TC-TTM-051. All four layers set on one date; exactly one decides."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALL_SEVEN)  # layer 4 says teaching
        await admin.post(  # layer 3 says closed
            "/timetable/holidays",
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-11",
                "label": "Public holiday", "kind": "public",
            },
        )
        await admin.post(  # layer 1/2 says working
            f"/timetable/schools/{campus['school']}/exceptions",
            json={"on_date": "2026-11-11", "working": True, "reason": "Ward duty"},
        )
        resolved = await _day(admin, campus["school"], "2026-11-11")
    assert (resolved["teaching"], resolved["decided_by"]) == (True, "school-override")


async def test_a_dated_exception_closes_a_day_the_pattern_teaches(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        closed = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={"on_date": "2026-11-11", "working": False, "reason": "Convocation"},
        )
        assert closed.status_code == 201, closed.text
        resolved = await _day(admin, campus["school"], "2026-11-11")
    assert (resolved["teaching"], resolved["decided_by"]) == (False, "school-exception")
    assert resolved["detail"] == "Convocation"


# --- compensatory days --------------------------------------------------------


async def test_a_compensatory_saturday_follows_mondays_timetable(make_client, campus) -> None:
    """TC-TTM-052. The common Indian make-up day — without `follows`, the
    Sessions for that date could not be generated at all."""
    async with make_client("super-admin") as admin:
        created = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={
                "on_date": SAT_1ST, "working": True, "follows_day_of_week": 1,
                "reason": "Compensating for the Deepavali holiday",
            },
        )
        assert created.status_code == 201, created.text
        resolved = await _day(admin, campus["school"], SAT_1ST)

    assert resolved["teaching"] is True
    # A Saturday that runs Monday's schedule: the day it *follows* is what the
    # session expansion must use, not the date's own weekday.
    assert resolved["effective_day_of_week"] == 1
    assert "Monday" in resolved["detail"]


async def test_a_compensatory_day_cannot_follow_an_untaught_weekday(make_client, campus) -> None:
    """TC-TTM-053. A working day that runs nothing is not a working day."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], MON_TO_FRI)
        refused = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={
                "on_date": SAT_1ST, "working": True, "follows_day_of_week": 7,
                "reason": "Make-up day",
            },
        )
    assert refused.status_code == 422, refused.text
    assert "does not teach Sunday" in refused.json()["detail"]


async def test_a_working_exception_on_an_untaught_weekday_is_refused(make_client, campus) -> None:
    """Same rule reached the other way: no `follows`, and the date's own weekday
    is not taught, so the day would be empty."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], MON_TO_FRI)
        refused = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={"on_date": SAT_1ST, "working": True, "reason": "Extra class"},
        )
    assert refused.status_code == 422
    assert "does not teach Saturday" in refused.json()["detail"]


async def test_a_non_working_date_cannot_follow_a_weekday(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        refused = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={
                "on_date": SAT_1ST, "working": False, "follows_day_of_week": 1,
                "reason": "Closed",
            },
        )
    assert refused.status_code == 422


# --- the narrowing guards (§4 rule 13) ----------------------------------------


@pytest.fixture
def captured_attendance(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, bool]]:
    """Stand in for ATT, which does not exist yet. The guard is enforced through
    a registered probe, so it is live and testable now and correct the day ATT
    lands — the same wiring org already uses for ONB's position reader.

    Yields a switch, because a test that needs attendance to appear *after* some
    setup would otherwise be blocked from doing the setup at all.
    """
    state = {"captured": True}

    async def probe(session, school_id, start, end) -> list[str]:
        return ["11-11-2026 P1 MA101 · 1A"] if state["captured"] else []

    monkeypatch.setattr(ttm_service, "_attendance_probe", probe)
    yield state


async def test_a_holiday_is_refused_over_captured_attendance(
    make_client, campus, captured_attendance
) -> None:
    """TC-TTM-054. Attendance gates exam eligibility under UGC norms, so an
    administrative edit never voids it."""
    async with make_client("super-admin") as admin:
        refused = await admin.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-11",
                "label": "Declared late", "kind": "local",
            },
        )
        listed = (await admin.get("/timetable/holidays")).json()

    assert refused.status_code == 409, refused.text
    assert "attendance has already been captured" in refused.json()["detail"]
    assert "MA101" in refused.json()["detail"]
    assert listed == [], "the holiday was written despite the refusal"


async def test_closing_a_date_is_refused_over_captured_attendance(
    make_client, campus, captured_attendance
) -> None:
    async with make_client("super-admin") as admin:
        refused = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={"on_date": "2026-11-11", "working": False, "reason": "Closed retroactively"},
        )
    assert refused.status_code == 409
    assert "attendance has already been captured" in refused.json()["detail"]


async def test_widening_a_holiday_to_more_campuses_is_guarded(
    make_client, campus, captured_attendance
) -> None:
    """Regression. The guard used to watch only the dates, so dropping a campus
    tag turned a one-campus holiday university-wide and closed dates that other
    Schools had already taught and marked — silently, with a 200."""
    async with make_client("super-admin") as admin:
        captured_attendance["captured"] = False  # nothing marked yet
        holiday = (
            await admin.post(
                "/timetable/holidays",
                json={
                    "from_date": "2026-11-11", "to_date": "2026-11-11",
                    "label": "Local festival", "kind": "local",
                    "campus_codes": ["CHENNAI"],
                },
            )
        ).json()
        # Schools on the other campuses taught that date and marked attendance.
        captured_attendance["captured"] = True

        # Same dates, but now every campus: newly closed for everyone else.
        widened = await admin.put(
            f"/timetable/holidays/{holiday['id']}", json={"campus_codes": []}
        )
        added = await admin.put(
            f"/timetable/holidays/{holiday['id']}",
            json={"campus_codes": ["CHENNAI", "COIMBATORE"]},
        )
        # Narrowing the campus list re-opens dates, so it stays allowed.
        narrowed = await admin.put(
            f"/timetable/holidays/{holiday['id']}", json={"campus_codes": ["CHENNAI"]}
        )
        after = (await admin.get("/timetable/holidays")).json()[0]

    assert widened.status_code == 409, widened.text
    assert "more campuses" in widened.json()["detail"]
    assert added.status_code == 409, added.text
    assert narrowed.status_code == 200, narrowed.text
    assert after["campus_codes"] == ["CHENNAI"], "a refused widen still wrote"


async def test_a_pattern_change_is_not_retroactive_for_history(
    make_client, campus
) -> None:
    """TC-TTM-058. The resolver is a pure function of current configuration, so
    a past date does re-answer under a new pattern — that is correct, because
    what protects history is not the resolver. ATT materialises dated Sessions
    when they happen, and the §4 rule 13 guards stop a taught day being
    withdrawn underneath them. This test pins the resolver's statelessness so a
    future change to it is a deliberate one."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALL_SEVEN)
        taught = await _day(admin, campus["school"], "2026-01-04")  # a Sunday, past
        await _pattern(admin, campus["school"], MON_TO_FRI)
        reanswered = await _day(admin, campus["school"], "2026-01-04")

    assert taught["teaching"] is True
    assert reanswered["teaching"] is False


async def test_declaring_a_working_day_is_never_blocked_by_attendance(
    make_client, campus, captured_attendance
) -> None:
    """Widening the calendar cannot orphan anything, so it passes the guard."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALL_SEVEN)
        allowed = await admin.post(
            f"/timetable/schools/{campus['school']}/exceptions",
            json={"on_date": "2026-11-08", "working": True, "reason": "Ward duty"},
        )
    assert allowed.status_code == 201, allowed.text


# --- the authoring guard (§4 rule 12) -----------------------------------------

GRID = {
    "name": "Standard day",
    "periods": [
        {"name": "P1", "sequence": 1, "start_time": "09:00", "end_time": "10:00"},
    ],
}


async def _placeable(admin: httpx.AsyncClient, campus: dict) -> dict:
    """A grid, a draft, and one offering/venue/teacher — enough to place a class."""
    grid = (
        await admin.post("/timetable/grids", json={"school_id": campus["school"], **GRID})
    ).json()
    draft = (
        await admin.post(
            "/timetable/drafts", json={"school_id": campus["school"], "term_code": TERM}
        )
    ).json()
    subject = (
        await admin.post(
            "/org/subjects",
            json={
                "code": "MA101", "name": "Maths", "department_id": campus["department"],
                "kind": "core", "credits": 4,
            },
        )
    ).json()
    offering = (
        await admin.post(
            "/org/offerings",
            json={"subject_id": subject["id"], "program_id": campus["program"], "position": 1},
        )
    ).json()
    venue = (
        await admin.post("/org/venues", json={"code": "A1", "name": "A1", "capacity": 60})
    ).json()
    teacher = (
        await admin.post(
            "/user", json={"username": "prof.c", "full_name": "Prof C", "kind": "staff"}
        )
    ).json()
    return {
        "draft_id": draft["id"], "period_id": grid["periods"][0]["id"],
        "offering_id": offering["id"], "venue_id": venue["id"], "faculty_id": teacher["id"],
    }


async def _place(admin: httpx.AsyncClient, campus: dict, setup: dict, day: int):
    return await admin.post(
        f"/timetable/drafts/{setup['draft_id']}/entries",
        json={
            "section_id": campus["section_3a"],
            "day_of_week": day,
            "period_id": setup["period_id"],
            "offering_id": setup["offering_id"],
            "faculty_user_id": setup["faculty_id"],
            "venue_id": setup["venue_id"],
        },
    )


async def test_a_class_cannot_be_placed_on_a_day_the_school_does_not_teach(
    make_client, campus
) -> None:
    """TC-TTM-046. Before this guard the entry saved, held the room against every
    other School's clash checks, and was invisible in every view."""
    async with make_client("super-admin") as admin:
        setup = await _placeable(admin, campus)
        await _pattern(admin, campus["school"], MON_TO_FRI)
        refused = await _place(admin, campus, setup, day=7)
    assert refused.status_code == 422, refused.text
    assert "does not teach Sunday" in refused.json()["detail"]
    assert "Mon, Tue, Wed, Thu, Fri" in refused.json()["detail"]


async def test_a_seven_day_school_may_place_a_sunday_class(make_client, campus) -> None:
    async with make_client("super-admin") as admin:
        setup = await _placeable(admin, campus)
        await _pattern(admin, campus["school"], ALL_SEVEN)
        placed = await _place(admin, campus, setup, day=7)
    assert placed.status_code == 201, placed.text


async def test_an_alternate_saturday_school_may_still_teach_saturday(make_client, campus) -> None:
    """A weekly entry asks "is this weekday taught at all" — the nth-weekday rule
    then decides which dates it actually runs on."""
    async with make_client("super-admin") as admin:
        setup = await _placeable(admin, campus)
        await _pattern(admin, campus["school"], ALTERNATE_SATURDAYS)
        placed = await _place(admin, campus, setup, day=6)
    assert placed.status_code == 201, placed.text


async def test_turning_off_a_taught_weekday_is_refused(make_client, campus) -> None:
    """TC-TTM-055. Withdrawing a day the published timetable teaches would orphan
    those classes silently."""
    async with make_client("super-admin") as admin:
        setup = await _placeable(admin, campus)
        await _place(admin, campus, setup, day=1)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        published = await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")
        assert published.status_code == 200, published.text

        refused = await _pattern(admin, campus["school"], {"2": True, "3": True})
    assert refused.status_code == 409, refused.text
    assert "Cannot stop teaching Monday" in refused.json()["detail"]
    assert "Republish" in refused.json()["detail"]


async def test_widening_the_pattern_is_always_allowed(make_client, campus) -> None:
    """TC-TTM-056."""
    async with make_client("super-admin") as admin:
        setup = await _placeable(admin, campus)
        await _place(admin, campus, setup, day=1)
        await admin.post(
            f"/timetable/drafts/{setup['draft_id']}/approvals",
            json={"department_id": campus["department"], "approve": True},
        )
        await admin.post(f"/timetable/drafts/{setup['draft_id']}/publish")
        widened = await _pattern(admin, campus["school"], ALL_SEVEN)
    assert widened.status_code == 200, widened.text


# --- access (§3/§4) -----------------------------------------------------------


async def test_holidays_are_not_writable_by_a_school_incharge(
    make_client: Callable[..., httpx.AsyncClient], campus
) -> None:
    """TC-TTM-059. Holidays are university master data; a School Incharge owns
    their own week, not everyone's."""
    async with make_client("school-incharge", user_id="si.one") as incharge:
        refused = await incharge.post(
            "/timetable/holidays",
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-11",
                "label": "Unilateral", "kind": "local",
            },
        )
    assert refused.status_code == 403


async def test_a_school_incharge_cannot_set_another_schools_week(
    make_client: Callable[..., httpx.AsyncClient], campus
) -> None:
    """TC-TTM-059, scope half: the role is right, the School is not."""
    async with make_client("super-admin") as admin:
        other = (
            await admin.post(
                "/org/units",
                json={
                    "type": "school", "name": "SOMED", "code": "SOMED",
                    "parent_id": campus["faculty_division"], "cadence": "semester",
                },
            )
        ).json()
        grant = await admin.post(
            "/rbac/grants",
            json={"user_id": (
                await admin.post(
                    "/user",
                    json={"username": "si.other", "full_name": "SI", "kind": "staff"},
                )
            ).json()["id"], "role_code": "school-incharge", "org_unit_id": other["id"]},
        )
        assert grant.status_code == 201, grant.text

    async with make_client("school-incharge", user_id="si.other") as incharge:
        own = await _pattern(incharge, other["id"], ALL_SEVEN)
        foreign = await _pattern(incharge, campus["school"], ALL_SEVEN)
    assert own.status_code == 200, own.text
    assert foreign.status_code == 403


async def test_the_range_form_answers_a_whole_term_in_one_call(make_client, campus) -> None:
    """TC-TTM-060. ATT expands a term against this — one query per date would
    not survive contact with a real term."""
    async with make_client("super-admin") as admin:
        await _pattern(admin, campus["school"], ALTERNATE_SATURDAYS)
        days = await _resolve(admin, campus["school"], "2026-07-01", "2026-11-30")
    assert len(days) == 153
    assert {d["on_date"] for d in days} >= {"2026-07-01", "2026-11-30"}
    by_date = {date.fromisoformat(d["on_date"]): d["teaching"] for d in days}
    assert not any(teaching for d, teaching in by_date.items() if d.isoweekday() == 7)
    assert all(teaching for d, teaching in by_date.items() if d.isoweekday() == 3)
    saturdays = {d: t for d, t in by_date.items() if d.isoweekday() == 6}
    assert sum(saturdays.values()) == 10  # two per month across five months
