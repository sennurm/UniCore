"""Pydantic request/response models for the org module."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from unicore.core.templates import CsvTemplate, register


class OrgUnitCreate(BaseModel):
    type: str
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    parent_id: uuid.UUID | None = None
    campus_code: str | None = None
    # Mandatory on a School, optional override on a Programme, meaningless
    # elsewhere (AUTH-FR-19). Rejected up front rather than by the DB constraint
    # so the caller gets a sentence instead of a check-violation.
    cadence: Literal["semester", "yearly"] | None = None
    class_size_cap: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def _cadence_placement(self) -> "OrgUnitCreate":
        if self.type == "school" and self.cadence is None:
            raise ValueError(
                "a School must declare its curriculum cadence ('semester' or 'yearly')"
            )
        if self.cadence is not None and self.type not in ("school", "program"):
            raise ValueError(f"a {self.type} does not carry a curriculum cadence")
        return self


class OrgUnitRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgUnitReparent(BaseModel):
    new_parent_id: uuid.UUID


class OrgUnitOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    code: str
    parent_id: uuid.UUID | None
    path: str
    campus_code: str | None
    status: str
    term_code: str | None
    level: str | None = None
    duration_years: int | None = None
    mode: str | None = None
    category: str | None = None
    industry_partner: str | None = None
    internship_months: int | None = None
    lateral_entry_semester: int | None = None
    cadence: str | None = None
    cadence_unconfirmed: bool = False
    class_size_cap: int | None = None
    position: int | None = None
    division_letter: str | None = None
    auto_created: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgUnitUpdate(BaseModel):
    """Editable fields from the org table. Code is immutable — it is embedded in
    this unit's path and every descendant's, so changing it would rewrite the
    subtree; create a new unit and deactivate the old one instead."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    level: str | None = None
    duration_years: int | None = Field(default=None, ge=1, le=10)
    mode: str | None = None
    category: str | None = None
    industry_partner: str | None = Field(default=None, max_length=120)
    internship_months: int | None = Field(default=None, ge=0, le=36)
    lateral_entry_semester: int | None = Field(default=None, ge=1, le=12)
    campus_code: str | None = None
    cadence: Literal["semester", "yearly"] | None = None
    class_size_cap: int | None = Field(default=None, ge=1, le=500)


# Flat catalogue import (locked 28-07-2026). One row per Programme, ancestors as
# columns — no `type` column, no parent_path chaining. Missing ancestors are
# created automatically. Codes are explicit (stakeholder decision) because they
# appear in paths, imports and reports.
ORG_CSV_COLUMNS = (
    "faculty_division_code",
    "faculty_division_name",
    "school_code",
    "school_name",
    "cadence",
    "department_code",
    "department_name",
    "programme_code",
    "programme_name",
    "level",
    "duration_years",
    "mode",
    "category",
    "industry_partner",
    "internship_months",
    "lateral_entry_semester",
)

PROGRAMME_CATEGORIES = (
    "Standard",
    "Industry Collaborated",
    "Industry Integrated",
    "Research",
)

PROGRAMME_LEVELS = (
    "Under Graduate",
    "Post Graduate",
    "PhD (Full-Time)",
    "PhD (Part-Time)",
    "Diploma/Certificate",
)
PROGRAMME_MODES = ("Full-Time", "Part-Time")

register(
    CsvTemplate(
        key="org-structure",
        title="Org structure / course catalogue",
        description=(
            "One row per Programme, with its Faculty Division, School and Department "
            "as columns. Ancestors are created automatically if they do not exist."
        ),
        columns=ORG_CSV_COLUMNS,
        examples=(
            {
                "faculty_division_code": "FET",
                "faculty_division_name": "Faculty of Engineering & Technology",
                "school_code": "SCOPE",
                "school_name": "School of Computational Engineering",
                "cadence": "semester",
                "department_code": "CSE",
                "department_name": "Computer Science & Engineering",
                "programme_code": "BT-CSE",
                "programme_name": "B.Tech - Computer Science & Engineering",
                "level": "Under Graduate",
                "duration_years": "4",
                "mode": "Full-Time",
                "category": "Standard",
                "industry_partner": "",
                "internship_months": "",
                "lateral_entry_semester": "",
            },
            {
                "faculty_division_code": "FET",
                "faculty_division_name": "Faculty of Engineering & Technology",
                "school_code": "SCOPE",
                "school_name": "School of Computational Engineering",
                "cadence": "semester",
                "department_code": "CSE",
                "department_name": "Computer Science & Engineering",
                "programme_code": "BT-CSE-CYBER",
                "programme_name": "B.Tech CSE (Cyber Security)",
                "level": "Under Graduate",
                "duration_years": "4",
                "mode": "Full-Time",
                "category": "Industry Collaborated",
                "industry_partner": "IBM",
                "internship_months": "",
                "lateral_entry_semester": "",
            },
            {
                # No Department: this School has none, so the importer creates a
                # default Department mirroring the School.
                "faculty_division_code": "FHS",
                "faculty_division_name": "Faculty of Health Sciences",
                "school_code": "SAHS",
                "school_name": "School of Allied Health Sciences",
                "cadence": "semester",
                "department_code": "",
                "department_name": "",
                "programme_code": "B-OPTOM",
                "programme_name": "Bachelor of Optometry",
                "level": "Under Graduate",
                "duration_years": "5",
                "mode": "Full-Time",
                "category": "Standard",
                "industry_partner": "",
                "internship_months": "12",
                "lateral_entry_semester": "",
            },
            {
                "faculty_division_code": "FHS",
                "faculty_division_name": "Faculty of Health Sciences",
                "school_code": "SOP",
                "school_name": "School of Pharmacy",
                "cadence": "semester",
                "department_code": "",
                "department_name": "",
                "programme_code": "B-PHARM",
                "programme_name": "B.Pharm - Bachelor of Pharmacy",
                "level": "Under Graduate",
                "duration_years": "4",
                "mode": "Full-Time",
                "category": "Standard",
                "industry_partner": "",
                "internship_months": "",
                "lateral_entry_semester": "3",
            },
        ),
        notes=(
            "SAMPLE DATA — the four rows below are a worked example (two Programmes in "
            "one Department, two in another Faculty Division). Replace them with your "
            "own rows before uploading.",
            "ONE ROW PER PROGRAMME. Repeat the Faculty Division / School / Department "
            "columns on every row — they are created once and reused.",
            "cadence is MANDATORY on every row: 'semester' or 'yearly', the School's "
            "curriculum rhythm. It sets each Programme's position ladder (a 4-year "
            "semester Programme has 8 positions, a yearly one has 4), so it drives "
            "Section generation, student positions and promotion. A Programme that "
            "genuinely differs from its School — a PhD under a semester School — is "
            "overridden individually afterwards, not in this file.",
            "Faculty Division, School and Programme codes/names are mandatory. "
            "DEPARTMENT IS OPTIONAL: leave both department columns blank for Schools "
            "that have no departments, and a default Department mirroring the School "
            "is created to carry the Programme (marked as auto-created in the org "
            "table).",
            "Codes are short identifiers used in paths and reports (e.g. CSE, BT-CSE); "
            "names are what users see.",
            "Codes are matched case-insensitively, and hyphens and underscores are "
            "treated the same (BT-CSE and bt_cse are the same code).",
            "level is one of: " + ", ".join(PROGRAMME_LEVELS) + ".",
            "mode is Full-Time or Part-Time. duration_years is a whole number of "
            "academic years, EXCLUDING internship.",
            "category is one of: " + ", ".join(PROGRAMME_CATEGORIES) + ". "
            "industry_partner names the collaborator (IBM, XEBIA, AWS…) where the "
            "programme has one; leave blank otherwise.",
            "internship_months is the internship period on top of duration_years "
            "(e.g. 12 for '4 years incl. 1-year internship' recorded as 4 + 12). "
            "lateral_entry_semester is the semester lateral entrants join (e.g. 3), "
            "blank when the programme has no lateral entry.",
            "Re-uploading is safe: existing units are matched on code and updated "
            "(names and Programme attributes), never duplicated.",
            "Sections are NOT created here — the Timetable Cell creates them per term "
            "(use the 'sections' template).",
        ),
    )
)


class OrgImportRowError(BaseModel):
    row_number: int
    field: str
    reason: str
    raw_row: str


class OrgImportResult(BaseModel):
    rows_total: int
    rows_created: int
    rows_updated: int
    rows_unchanged: int
    rows_rejected: int
    errors: list[OrgImportRowError]


# --- subjects, offerings and venues (master data for TTM) --------------------

SUBJECT_KINDS = ("core", "elective")
ELECTIVE_GROUPS = ("general", "professional", "open")
VENUE_KINDS = ("classroom", "lab", "seminar", "auditorium", "workshop")


class SubjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    department_id: uuid.UUID
    kind: Literal["core", "elective"] = "core"
    elective_group: Literal["general", "professional", "open"] | None = None
    credits: int = Field(default=0, ge=0, le=30)
    theory_hours: int = Field(default=0, ge=0, le=40)
    lab_hours: int = Field(default=0, ge=0, le=40)

    @model_validator(mode="after")
    def _group_matches_kind(self) -> "SubjectCreate":
        if self.kind == "elective" and self.elective_group is None:
            raise ValueError(
                "an elective needs an elective_group — it is what students choose within"
            )
        if self.kind == "core" and self.elective_group is not None:
            raise ValueError("a core subject is not chosen, so it carries no elective_group")
        return self


class SubjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    credits: int | None = Field(default=None, ge=0, le=30)
    theory_hours: int | None = Field(default=None, ge=0, le=40)
    lab_hours: int | None = Field(default=None, ge=0, le=40)


class SubjectOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    department_id: uuid.UUID
    kind: str
    elective_group: str | None
    credits: int
    theory_hours: int
    lab_hours: int
    status: str

    model_config = {"from_attributes": True}


class OfferingCreate(BaseModel):
    subject_id: uuid.UUID
    program_id: uuid.UUID
    position: int = Field(ge=1, le=12)


class OfferingOut(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    program_id: uuid.UUID
    position: int
    status: str
    subject: SubjectOut

    model_config = {"from_attributes": True}


class VenueCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=150)
    capacity: int = Field(ge=1, le=2000)
    kind: Literal["classroom", "lab", "seminar", "auditorium", "workshop"] = "classroom"
    campus_code: str | None = Field(default=None, max_length=50)
    building: str | None = Field(default=None, max_length=100)
    room: str | None = Field(default=None, max_length=50)


class VenueUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    capacity: int | None = Field(default=None, ge=1, le=2000)
    kind: Literal["classroom", "lab", "seminar", "auditorium", "workshop"] | None = None
    campus_code: str | None = Field(default=None, max_length=50)
    building: str | None = Field(default=None, max_length=100)
    room: str | None = Field(default=None, max_length=50)


class VenueOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    campus_code: str | None
    building: str | None
    room: str | None
    capacity: int
    kind: str
    status: str

    model_config = {"from_attributes": True}


SUBJECT_CSV_COLUMNS = (
    "subject_code",
    "subject_name",
    "department_code",
    "kind",
    "elective_group",
    "credits",
    "theory_hours",
    "lab_hours",
    "programme_code",
    "position",
)

register(
    CsvTemplate(
        key="subjects",
        title="Subject catalogue",
        description=(
            "Subjects and where they are taught. One row per subject-offering: a "
            "subject shared by three Programmes appears on three rows."
        ),
        columns=SUBJECT_CSV_COLUMNS,
        examples=(
            {
                "subject_code": "MA101",
                "subject_name": "Engineering Mathematics I",
                "department_code": "MATHS",
                "kind": "core",
                "elective_group": "",
                "credits": "4",
                "theory_hours": "4",
                "lab_hours": "0",
                "programme_code": "BT-CSE",
                "position": "1",
            },
            {
                # Same subject, offered to a second Programme — one definition,
                # two offerings. Attributes are read from the first row only.
                "subject_code": "MA101",
                "subject_name": "Engineering Mathematics I",
                "department_code": "MATHS",
                "kind": "core",
                "elective_group": "",
                "credits": "4",
                "theory_hours": "4",
                "lab_hours": "0",
                "programme_code": "BT-AIDS",
                "position": "1",
            },
            {
                "subject_code": "CS501",
                "subject_name": "Machine Learning",
                "department_code": "CSE",
                "kind": "elective",
                "elective_group": "professional",
                "credits": "3",
                "theory_hours": "3",
                "lab_hours": "2",
                "programme_code": "BT-CSE",
                "position": "5",
            },
            {
                "subject_code": "OE201",
                "subject_name": "Indian Constitution",
                "department_code": "HUM",
                "kind": "elective",
                "elective_group": "open",
                "credits": "2",
                "theory_hours": "2",
                "lab_hours": "0",
                "programme_code": "BT-CSE",
                "position": "3",
            },
        ),
        notes=(
            "SAMPLE DATA — replace the rows below with your own before uploading.",
            "ONE ROW PER OFFERING. A subject taught to several Programmes repeats, "
            "changing only programme_code and position; it is defined once and "
            "offered many times, so its credits and hours are read from the first "
            "row and later rows only place it.",
            "department_code is the department that OWNS the subject (Maths owns "
            "MA101), not the Programme that studies it — that is programme_code.",
            "kind is 'core' or 'elective'. An elective MUST name an "
            "elective_group: general, professional, or open. A core subject must "
            "leave elective_group blank.",
            "Students choose exactly one subject within each elective group they "
            "are offered for a term, so two electives in the same group at the "
            "same position are alternatives, not both taught to one student.",
            "position is the ladder position the subject is taught at — the "
            "semester number for a semester-cadence Programme, the year number "
            "for a yearly one.",
            "Re-uploading is safe: subjects match on subject_code and offerings on "
            "(subject, programme, position); neither is duplicated.",
        ),
    )
)

VENUE_CSV_COLUMNS = ("code", "name", "capacity", "kind", "campus_code", "building", "room")

register(
    CsvTemplate(
        key="venues",
        title="Venues",
        description="Rooms available for timetabling, with capacity and type.",
        columns=VENUE_CSV_COLUMNS,
        examples=(
            {
                "code": "A101",
                "name": "Lecture Hall A101",
                "capacity": "60",
                "kind": "classroom",
                "campus_code": "MAIN",
                "building": "Academic Block A",
                "room": "101",
            },
            {
                "code": "CSLAB1",
                "name": "Computer Lab 1",
                "capacity": "30",
                "kind": "lab",
                "campus_code": "MAIN",
                "building": "Academic Block B",
                "room": "G02",
            },
        ),
        notes=(
            "SAMPLE DATA — replace the rows below with your own before uploading.",
            "kind is one of: classroom, lab, seminar, auditorium, workshop. It "
            "matters because a lab block must be scheduled in a lab.",
            "Venues are university-wide, not owned by a School: clash detection "
            "spans the whole university, so a room cannot host two sessions even "
            "if the two Schools never speak to each other.",
            "capacity drives the soft over-capacity warning when a Section larger "
            "than the room is scheduled into it.",
            "Re-uploading is safe: rows match on code and are updated, never "
            "duplicated.",
        ),
    )
)
