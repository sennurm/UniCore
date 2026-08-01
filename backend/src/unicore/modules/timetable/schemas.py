"""Pydantic request/response models for the timetable module."""

import uuid
from datetime import date, datetime
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
