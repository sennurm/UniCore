"""Pydantic request/response models for the timetable module."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TermCreate(BaseModel):
    school_id: uuid.UUID
    term_code: str = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date
    exam_ranges: list[dict[str, Any]] = Field(default_factory=list)
    special_events: list[dict[str, Any]] = Field(default_factory=list)
    archival_backstop_date: date | None = None

    @model_validator(mode="after")
    def _dates_ordered(self) -> "TermCreate":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class TermOut(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    term_code: str
    version: int
    start_date: date
    end_date: date
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
