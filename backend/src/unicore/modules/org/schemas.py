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
    created_at: datetime

    model_config = {"from_attributes": True}


# Org import schema v1 (locked 27-07-2026). Rows reference their parent by dotted
# path so codes reused across branches stay unambiguous; Sections are excluded —
# they are per-term instances created by the Timetable Cell (TTM-FR-19).
ORG_CSV_COLUMNS = ("type", "code", "name", "parent_path", "campus_code")

register(
    CsvTemplate(
        key="org-structure",
        title="Org structure",
        description=(
            "Faculty Divisions, Schools, Departments and Programs. "
            "Sections are created per term by the Timetable Cell, not here."
        ),
        columns=ORG_CSV_COLUMNS,
        examples=(
            # A complete, self-consistent subtree: each row's parent_path is built
            # by the rows above it, so this file imports cleanly as-is under a
            # university whose code is UNI.
            {
                "type": "faculty_division",
                "code": "FET",
                "name": "Faculty of Engineering & Technology",
                "parent_path": "UNI",
                "campus_code": "MAIN",
            },
            {
                "type": "school",
                "code": "SOCE",
                "name": "School of Computational Engineering",
                "parent_path": "UNI.FET",
                "campus_code": "MAIN",
            },
            {
                "type": "department",
                "code": "CSE",
                "name": "Computer Science & Engineering",
                "parent_path": "UNI.FET.SOCE",
                "campus_code": "MAIN",
            },
            {
                "type": "department",
                "code": "AIDS",
                "name": "Artificial Intelligence & Data Science",
                "parent_path": "UNI.FET.SOCE",
                "campus_code": "MAIN",
            },
            {
                "type": "program",
                "code": "BT-CSE",
                "name": "B.Tech Computer Science & Engineering",
                "parent_path": "UNI.FET.SOCE.CSE",
                "campus_code": "MAIN",
            },
            {
                "type": "program",
                "code": "BT-AIDS",
                "name": "B.Tech Artificial Intelligence & Data Science",
                "parent_path": "UNI.FET.SOCE.AIDS",
                "campus_code": "MAIN",
            },
        ),
        notes=(
            "SAMPLE DATA — the six rows below are a complete worked example "
            "(Faculty Division -> School -> two Departments -> two Programs). "
            "Replace them with your own rows before uploading.",
            "type is one of: faculty_division, school, department, program. "
            "The university row itself is created by bootstrap, not by this file.",
            "parent_path is the parent's dotted code path, built from the codes above "
            "it, e.g. UNI.FET.SOCE. Case-insensitive; hyphens and underscores are "
            "treated the same (BT-CSE and BT_CSE both match).",
            "Row order does not matter — the importer creates parents before children.",
            "campus_code is optional; blank inherits the parent's campus.",
            "Re-uploading is safe: an existing unit at the same parent + code is left "
            "unchanged, and only its name is updated.",
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
