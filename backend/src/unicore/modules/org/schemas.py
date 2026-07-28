"""Pydantic request/response models for the org module."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from unicore.core.templates import CsvTemplate, register


class OrgUnitCreate(BaseModel):
    type: str
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    parent_id: uuid.UUID | None = None
    campus_code: str | None = None


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
    campus_code: str | None = None


# Flat catalogue import (locked 28-07-2026). One row per Programme, ancestors as
# columns — no `type` column, no parent_path chaining. Missing ancestors are
# created automatically. Codes are explicit (stakeholder decision) because they
# appear in paths, imports and reports.
ORG_CSV_COLUMNS = (
    "faculty_division_code",
    "faculty_division_name",
    "school_code",
    "school_name",
    "department_code",
    "department_name",
    "programme_code",
    "programme_name",
    "level",
    "duration_years",
    "mode",
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
                "school_code": "SOCE",
                "school_name": "School of Computational Engineering",
                "department_code": "CSE",
                "department_name": "Computer Science & Engineering",
                "programme_code": "BT-CSE",
                "programme_name": "B.Tech Computer Science & Engineering",
                "level": "Under Graduate",
                "duration_years": "4",
                "mode": "Full-Time",
            },
            {
                "faculty_division_code": "FET",
                "faculty_division_name": "Faculty of Engineering & Technology",
                "school_code": "SOCE",
                "school_name": "School of Computational Engineering",
                "department_code": "CSE",
                "department_name": "Computer Science & Engineering",
                "programme_code": "MT-CSE",
                "programme_name": "M.Tech Computer Science & Engineering",
                "level": "Post Graduate",
                "duration_years": "2",
                "mode": "Full-Time",
            },
            {
                "faculty_division_code": "FSC",
                "faculty_division_name": "Faculty of Science",
                "school_code": "SOBS",
                "school_name": "School of Basic Sciences",
                "department_code": "BSC",
                "department_name": "Basic Sciences",
                "programme_code": "BSC-PHY",
                "programme_name": "B.Sc - Physics",
                "level": "Under Graduate",
                "duration_years": "3",
                "mode": "Full-Time",
            },
            {
                "faculty_division_code": "FSC",
                "faculty_division_name": "Faculty of Science",
                "school_code": "SOBS",
                "school_name": "School of Basic Sciences",
                "department_code": "BSC",
                "department_name": "Basic Sciences",
                "programme_code": "PHD-PHY-FT",
                "programme_name": "PhD (Full-Time) - Physics",
                "level": "PhD (Full-Time)",
                "duration_years": "5",
                "mode": "Full-Time",
            },
        ),
        notes=(
            "SAMPLE DATA — the four rows below are a worked example (two Programmes in "
            "one Department, two in another Faculty Division). Replace them with your "
            "own rows before uploading.",
            "ONE ROW PER PROGRAMME. Repeat the Faculty Division / School / Department "
            "columns on every row — they are created once and reused.",
            "All *_code and *_name columns are mandatory. Codes are short identifiers "
            "used in paths and reports (e.g. CSE, BT-CSE); names are what users see.",
            "Codes are matched case-insensitively, and hyphens and underscores are "
            "treated the same (BT-CSE and bt_cse are the same code).",
            "level is one of: " + ", ".join(PROGRAMME_LEVELS) + ".",
            "mode is Full-Time or Part-Time. duration_years is a whole number of years.",
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
