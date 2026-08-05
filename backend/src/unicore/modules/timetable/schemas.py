"""Pydantic request/response models for the timetable module."""

import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from unicore.core.templates import CsvTemplate, register


class TermCreate(BaseModel):
    school_id: uuid.UUID
    term_code: str = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date
    parity: Literal["odd", "even"] | None = None
    exam_ranges: list[dict[str, Any]] = Field(default_factory=list)
    special_events: list[dict[str, Any]] = Field(default_factory=list)
    archival_backstop_date: date | None = None

    @model_validator(mode="after")
    def _dates_ordered(self) -> "TermCreate":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class MultiSchoolTermCreate(BaseModel):
    """One set of term dates applied to several Schools (TTM-FR-25).

    Fans out into an independent draft per School — never a shared record — so
    each School Incharge still approves, and may amend, their own.
    """

    school_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    term_code: str = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date
    parity: Literal["odd", "even"] | None = None
    exam_ranges: list[dict[str, Any]] = Field(default_factory=list)
    special_events: list[dict[str, Any]] = Field(default_factory=list)
    archival_backstop_date: date | None = None

    @model_validator(mode="after")
    def _dates_ordered(self) -> "MultiSchoolTermCreate":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class TermParitySet(BaseModel):
    parity: Literal["odd", "even"]


class SchoolTermResult(BaseModel):
    """Per-School outcome of a multi-School apply: created / versioned / skipped."""

    school_id: uuid.UUID
    school_name: str
    outcome: str
    version: int | None = None
    detail: str | None = None


class TermOut(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    term_code: str
    version: int
    start_date: date
    end_date: date
    # Null on calendars uploaded before parity existed; Section generation needs
    # it, so the screen must be able to see that it is missing.
    parity: str | None
    exam_ranges: list[dict[str, Any]]
    special_events: list[dict[str, Any]]
    archival_backstop_date: date | None
    status: str
    approved_by: str | None
    approved_at: datetime | None

    model_config = {"from_attributes": True}


class SectionCreate(BaseModel):
    program_id: uuid.UUID
    label: str = Field(min_length=1, max_length=50)
    term_code: str = Field(min_length=1, max_length=50)


class SectionOut(BaseModel):
    """TTM's own view of a created Section instance (the org module owns the table;
    each module publishes its own response contract — ARCHITECTURE.md)."""

    id: uuid.UUID
    name: str
    code: str
    path: str
    term_code: str | None
    status: str

    model_config = {"from_attributes": True}


class ProgrammeRefOut(BaseModel):
    """Just enough of a Programme to place a Section under it — TTM has no business
    republishing the org module's catalogue attributes."""

    id: uuid.UUID
    name: str
    code: str
    path: str

    model_config = {"from_attributes": True}


class ProgrammeSectionsOut(BaseModel):
    programme: ProgrammeRefOut
    sections: list[SectionOut]


class ProposedSectionOut(BaseModel):
    """One position's proposal: what exists, what generation would add, and the
    arithmetic that produced the number — so the Timetable Cell can see *why*."""

    programme_id: uuid.UUID
    programme_name: str
    programme_code: str
    cadence: str
    position: int
    year: int
    headcount: int
    headcount_source: str  # "roster" | "expected" | "none"
    class_size_cap: int
    required: int
    existing: list[SectionOut]
    to_create: list[str]  # rendered labels


class GenerationPlanOut(BaseModel):
    term_code: str
    parity: str
    school_id: uuid.UUID
    school_name: str
    rows: list[ProposedSectionOut]
    warnings: list[str]


class GenerationRequest(BaseModel):
    """Expected intake per (programme_id, position) for positions with no roster
    yet — a first-year cohort that has not been imported has nothing to count."""

    term_code: str = Field(min_length=1, max_length=50)
    expected_intake: dict[str, int] = Field(default_factory=dict)


class GenerationResultOut(BaseModel):
    created: list[SectionOut]
    existing: int
    warnings: list[str]


SECTION_CSV_COLUMNS = ("program_path", "label")

register(
    CsvTemplate(
        key="sections",
        title="Section instances",
        description=(
            "Per-term Section instances created during term setup. The term is chosen "
            "at upload time and must already be approved for the owning School."
        ),
        columns=SECTION_CSV_COLUMNS,
        examples=(
            {"program_path": "UNI.FET.SOCE.CSE.BT-CSE", "label": "3A"},
            {"program_path": "UNI.FET.SOCE.CSE.BT-CSE", "label": "3B"},
            {"program_path": "UNI.FET.SOCE.AIDS.BT-AIDS", "label": "1A"},
        ),
        notes=(
            "SAMPLE DATA — the three rows below create two Sections for one Program and "
            "one for another. Replace them with your own rows before uploading.",
            "program_path is the Program's dotted code path, e.g. "
            "UNI.FET.SOCE.CSE.BT-CSE. Case-insensitive; hyphens and underscores are "
            "treated the same.",
            "label is the Section name students see, e.g. 3B. Labels may repeat across "
            "terms — every term gets its own Section instance.",
            "The School's academic term must be APPROVED before its Sections can be "
            "created; the term is selected on the upload screen, not in this file.",
        ),
    )
)


# --- period grids, drafts and entries (TTM-FR-02/03/04/08/09) -----------------


class PeriodSpec(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    sequence: int = Field(ge=1, le=20)
    start_time: time
    end_time: time


class PeriodGridCreate(BaseModel):
    school_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    periods: list[PeriodSpec] = Field(min_length=1, max_length=20)


class PeriodOut(BaseModel):
    id: uuid.UUID
    name: str
    sequence: int
    start_time: time
    end_time: time

    model_config = {"from_attributes": True}


class PeriodGridOut(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    version: int
    name: str
    status: str
    periods: list[PeriodOut]


class DraftCreate(BaseModel):
    school_id: uuid.UUID
    term_code: str = Field(min_length=1, max_length=50)


class DraftOut(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    term_code: str
    version: int
    grid_id: uuid.UUID
    status: str

    model_config = {"from_attributes": True}


class EntryCreate(BaseModel):
    section_id: uuid.UUID
    day_of_week: int = Field(ge=1, le=7, description="ISO: Monday = 1")
    period_id: uuid.UUID
    offering_id: uuid.UUID
    faculty_user_id: uuid.UUID
    venue_id: uuid.UUID
    # TTM-FR-12: a venue smaller than the Section is a judgement call, so it
    # warns and needs recorded acknowledgment — unlike a clash, which is refused.
    acknowledge_capacity: bool = False


class EntryOut(BaseModel):
    id: uuid.UUID
    draft_id: uuid.UUID
    section_id: uuid.UUID
    day_of_week: int
    period_id: uuid.UUID
    offering_id: uuid.UUID
    faculty_user_id: uuid.UUID
    venue_id: uuid.UUID

    model_config = {"from_attributes": True}


class EntryResult(BaseModel):
    entry: EntryOut
    warnings: list[str]


class ApprovalDecision(BaseModel):
    department_id: uuid.UUID
    approve: bool
    reason: str | None = Field(default=None, max_length=500)


class ApprovalOut(BaseModel):
    department_id: uuid.UUID
    department_name: str
    status: str
    reason: str | None


class DraftStatusOut(BaseModel):
    draft_id: uuid.UUID
    school_id: uuid.UUID
    term_code: str
    version: int
    status: str
    entry_count: int
    approvals: list[ApprovalOut]
    publishable: bool
    blocking: list[str]


class PersonalRowOut(BaseModel):
    day_of_week: int
    period_name: str
    start_time: time
    end_time: time
    subject_code: str
    subject_name: str
    elective_group: str | None
    section_name: str
    faculty_name: str
    venue_code: str


class PersonalTimetableOut(BaseModel):
    role: str
    section_name: str | None
    rows: list[PersonalRowOut]
    note: str | None


class TimetableRowOut(BaseModel):
    entry_id: uuid.UUID
    section_id: uuid.UUID
    section_name: str
    day_of_week: int
    period_name: str
    start_time: time
    end_time: time
    subject_code: str
    subject_name: str
    faculty_user_id: uuid.UUID
    faculty_name: str
    venue_code: str


# --- calendar (TTM-FR-26/27/28) ----------------------------------------------


class HolidayCreate(BaseModel):
    """A closed date range. A single-day holiday is a one-day range."""

    from_date: date
    to_date: date
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["public", "vacation", "local"] = "public"
    # Empty means university-wide; naming campuses limits it to those.
    campus_codes: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _range_ordered(self) -> "HolidayCreate":
        if self.to_date < self.from_date:
            raise ValueError("to_date cannot be before from_date")
        return self


class HolidayUpdate(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["public", "vacation", "local"] | None = None
    campus_codes: list[str] | None = Field(default=None, max_length=50)


class HolidayOut(BaseModel):
    id: uuid.UUID
    from_date: date
    to_date: date
    label: str
    kind: str
    campus_codes: list[str]
    status: str

    model_config = {"from_attributes": True}


class WorkingPatternUpdate(BaseModel):
    """Which weekdays the School teaches.

    `days` maps an ISO weekday ("1" = Monday) to `true` — every occurrence — or
    to the occurrence numbers within the calendar month, so "Saturdays: 1st and
    3rd" is `{"6": [1, 3]}`. A weekday left out is not taught.
    """

    days: dict[str, bool | list[int]]
    # Omitted sets the School's standing pattern; supplying a term overrides it
    # for that term alone.
    term_code: str | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def _days_are_sane(self) -> "WorkingPatternUpdate":
        for key, value in self.days.items():
            if key not in {"1", "2", "3", "4", "5", "6", "7"}:
                raise ValueError(f"'{key}' is not an ISO weekday — use 1 (Monday) to 7")
            if isinstance(value, list):
                if not value:
                    raise ValueError(
                        f"weekday {key} lists no occurrences — omit the day to stop "
                        f"teaching it, rather than listing none"
                    )
                if any(n < 1 or n > 5 for n in value):
                    raise ValueError(
                        f"weekday {key}: occurrences are 1 to 5 within a calendar month"
                    )
        if not any(self.days.values()):
            raise ValueError("a School must teach at least one weekday")
        return self


class WorkingPatternOut(BaseModel):
    school_id: uuid.UUID
    term_code: str | None
    days: dict[str, Any]
    #: True when nothing is configured and the shipped Mon–Sat default is in
    #: force — so the caller can show that rather than imply a decision.
    is_default: bool


class CalendarExceptionCreate(BaseModel):
    on_date: date
    working: bool
    #: On a working exception only: the weekday whose timetable this date runs,
    #: which is how a compensatory Saturday follows Monday.
    follows_day_of_week: int | None = Field(default=None, ge=1, le=7)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _follows_needs_working(self) -> "CalendarExceptionCreate":
        if self.follows_day_of_week is not None and not self.working:
            raise ValueError(
                "a non-working date runs no timetable, so it cannot follow a weekday"
            )
        return self


class CalendarExceptionOut(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    on_date: date
    working: bool
    follows_day_of_week: int | None
    reason: str

    model_config = {"from_attributes": True}


class ResolvedDayOut(BaseModel):
    """One date's answer, naming the layer that decided it."""

    on_date: date
    teaching: bool
    #: The weekday whose timetable runs — normally the date's own, but a
    #: compensatory day runs the weekday it follows.
    effective_day_of_week: int | None
    decided_by: Literal[
        "school-exception", "school-override", "university-holiday",
        "school-pattern", "school-pattern-default",
    ]
    detail: str
