"""Pydantic request/response models for the onboarding module."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

# Import schema v1 (locked 27-07-2026). ERP-issued roll numbers; ERP ID is an
# opaque non-empty string.
CSV_COLUMNS_V1 = (
    "erp_id",
    "full_name",
    "date_of_birth",
    "gender",
    "mobile",
    "email",
    "program_code",
    "section_label",
    "admission_year",
    "roll_number",
)


class BatchOut(BaseModel):
    id: uuid.UUID
    filename: str
    term_code: str
    status: str
    rows_total: int
    rows_created: int
    rows_updated: int
    rows_unchanged: int
    rows_rejected: int
    uploaded_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RowErrorOut(BaseModel):
    row_number: int
    field: str
    reason: str
    raw_row: str

    model_config = {"from_attributes": True}


class SingleStudentAdd(BaseModel):
    """Mid-term add — same validation and activation pipeline as a bulk row."""

    erp_id: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=1, max_length=200)
    program_code: str
    section_label: str
    term_code: str
    admission_year: int = Field(ge=1900, le=2200)
    roll_number: str = Field(min_length=1, max_length=50)
    date_of_birth: date | None = None
    gender: str | None = None
    mobile: str | None = None
    email: str | None = None


class AllotRequest(BaseModel):
    user_id: uuid.UUID
    section_id: uuid.UUID
    effective_from: date


class TransferRequest(BaseModel):
    user_id: uuid.UUID
    new_program_id: uuid.UUID
    new_section_id: uuid.UUID | None = None
    effective_from: date


class StudentOut(BaseModel):
    user_id: uuid.UUID
    erp_id: str | None
    full_name: str
    status: str
    roll_number: str
    admission_year: int
    program_id: uuid.UUID
    credential_delivery: str
    delivery_channel: str | None
    section_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class MembershipOut(BaseModel):
    section_id: uuid.UUID
    effective_from: date
    effective_to: date | None

    model_config = {"from_attributes": True}
