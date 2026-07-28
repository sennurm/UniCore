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
        example={
            "type": "department",
            "code": "CSE",
            "name": "Computer Science & Engineering",
            "parent_path": "UNI.FET.SOCE",
            "campus_code": "MAIN",
        },
        notes=(
            "type is one of: faculty_division, school, department, program "
            "(university is created by bootstrap).",
            "parent_path is the parent's dotted code path, e.g. UNI.FET.SOCE — "
            "case-insensitive. Leave blank only for a university row.",
            "Row order does not matter: the importer creates parents before children.",
            "Re-uploading is safe — an existing unit at the same parent + code is left "
            "unchanged (names are updated).",
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
