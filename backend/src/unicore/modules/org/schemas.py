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
    category: str | None = None
    industry_partner: str | None = None
    internship_months: int | None = None
    lateral_entry_semester: int | None = None
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
