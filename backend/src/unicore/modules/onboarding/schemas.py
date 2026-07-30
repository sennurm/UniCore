"""Pydantic request/response models for the onboarding module."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from unicore.core.templates import CsvTemplate, register

# Import schema v1 (locked 27-07-2026; SIF rename 28-07-2026). ERP-issued roll
# numbers. Students carry two one-to-one ids: SIF (issued at admission — the
# join key here) and enrollment (issued later, uploaded separately). Both are
# opaque non-empty strings.
CSV_COLUMNS_V1 = (
    "sif_id",
    "full_name",
    "date_of_birth",
    "gender",
    "mobile",
    "email",
    "program_code",
    "section_label",
    "admission_year",
    "roll_number",
    "enrollment_id",
)


register(
    CsvTemplate(
        key="students",
        title="Student import",
        description="Bulk student provisioning from the ERP, one row per student.",
        columns=CSV_COLUMNS_V1,
        examples=(
            {
                "sif_id": "SIF-2026-000123",
                "full_name": "Ananya Raman",
                "date_of_birth": "15-08-2006",
                "gender": "F",
                "mobile": "9876543210",
                "email": "ananya.r@student.example.edu",
                "program_code": "BT-CSE",
                "section_label": "3B",
                "admission_year": "2026",
                "roll_number": "21CS1043",
                "enrollment_id": "",
            },
            {
                "sif_id": "SIF-2026-000124",
                "full_name": "Karthik Subramanian",
                "date_of_birth": "02-11-2005",
                "gender": "M",
                "mobile": "9876543211",
                "email": "karthik.s@student.example.edu",
                "program_code": "BT-CSE",
                "section_label": "3B",
                "admission_year": "2026",
                "roll_number": "21CS1044",
                "enrollment_id": "",
            },
            {
                # Mobile only — email may be blank as long as one channel exists.
                "sif_id": "SIF-2026-000125",
                "full_name": "Fatima Sheikh",
                "date_of_birth": "27-03-2006",
                "gender": "F",
                "mobile": "9876543212",
                "email": "",
                "program_code": "BT-CSE",
                "section_label": "3A",
                "admission_year": "2026",
                "roll_number": "21CS1045",
                "enrollment_id": "",
            },
            {
                # Email only, and a different Program + Section.
                "sif_id": "SIF-2026-000126",
                "full_name": "Joseph Mathew",
                "date_of_birth": "09-06-2006",
                "gender": "M",
                "mobile": "",
                "email": "joseph.m@student.example.edu",
                "program_code": "BT-AIDS",
                "section_label": "1A",
                "admission_year": "2026",
                "roll_number": "21AD1002",
            },
        ),
        notes=(
            "SAMPLE DATA — the four rows below show the expected shape (two students "
            "in one Section, one mobile-only, one email-only in another Program). "
            "Replace them with your own rows before uploading.",
            "Mandatory: sif_id, full_name, program_code, section_label, admission_year, "
            "roll_number. Optional: date_of_birth, gender.",
            "sif_id is the SIF number issued when admission completes — the key rows "
            "are matched on, because it is the only id a new student has.",
            "enrollment_id is the student's CANONICAL number but is issued later, so "
            "leave it blank at admission and assign it with the 'enrollment-ids' "
            "template. If you already have it, filling it in here works too.",
            "At least ONE of mobile/email is required — that is how initial credentials "
            "reach the student. Rows with neither are rejected.",
            "Dates are DD-MM-YYYY (15-08-2006). Roll numbers come from the ERP and must "
            "be unique within a Program + admission year.",
            "program_code must resolve within your scope, and the Section must already "
            "exist for the term you pick at upload time (see the 'sections' template).",
            "Re-uploading is safe: rows are matched on sif_id and updated, never "
            "duplicated.",
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

    sif_id: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=1, max_length=200)
    program_code: str
    section_label: str
    term_code: str
    admission_year: int = Field(ge=1900, le=2200)
    roll_number: str = Field(min_length=1, max_length=50)
    enrollment_id: str | None = Field(default=None, max_length=100)
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
    sif_id: str | None
    enrollment_id: str | None
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


# Enrollment numbers are issued weeks/months after admission, so they arrive in
# their own small file matched on SIF (locked 28-07-2026).
ENROLLMENT_CSV_COLUMNS = ("sif_id", "enrollment_id")

register(
    CsvTemplate(
        key="enrollment-ids",
        title="Enrollment numbers",
        description=(
            "Assign enrollment numbers to students already onboarded. Rows are "
            "matched on the SIF id issued at admission."
        ),
        columns=ENROLLMENT_CSV_COLUMNS,
        examples=(
            {"sif_id": "SIF-2026-000123", "enrollment_id": "TU2026CSE0001"},
            {"sif_id": "SIF-2026-000124", "enrollment_id": "TU2026CSE0002"},
            {"sif_id": "SIF-2026-000125", "enrollment_id": "TU2026CSE0003"},
        ),
        notes=(
            "SAMPLE DATA — replace the rows below with your own before uploading.",
            "sif_id identifies the student (issued when admission completed) and must "
            "already exist in UniCore; enrollment_id is the number being assigned.",
            "Enrollment numbers are unique across the whole university.",
            "Re-uploading is safe: a row whose enrollment number already matches is "
            "reported as unchanged. Correcting a number is allowed and audited.",
        ),
    )
)


class EnrollmentImportResult(BaseModel):
    rows_total: int
    rows_assigned: int
    rows_unchanged: int
    rows_rejected: int
    errors: list[RowErrorOut]
