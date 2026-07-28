"""Pydantic request/response models for the onboarding module."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from unicore.core.templates import CsvTemplate, register

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


register(
    CsvTemplate(
        key="students",
        title="Student import",
        description="Bulk student provisioning from the ERP, one row per student.",
        columns=CSV_COLUMNS_V1,
        example={
            "erp_id": "ERP-000123",
            "full_name": "Ananya Raman",
            "date_of_birth": "15-08-2006",
            "gender": "F",
            "mobile": "9876543210",
            "email": "ananya.r@student.example.edu",
            "program_code": "BT-CSE",
            "section_label": "3B",
            "admission_year": "2026",
            "roll_number": "21CS1043",
        },
        notes=(
            "Dates are DD-MM-YYYY. Delete these comment lines before uploading if your "
            "spreadsheet tool keeps them.",
            "erp_id, full_name, program_code, section_label, admission_year and "
            "roll_number are mandatory; at least one of mobile/email is required so "
            "credentials can be delivered.",
            "program_code must resolve within your scope; the Section must already exist "
            "for the term you select at upload time.",
            "Re-uploading the same file is safe: rows are matched on erp_id and updated, "
            "never duplicated.",
        ),
    )
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
