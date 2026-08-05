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
    "position",
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
                "position": "1",
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
                "position": "3",
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
                "position": "",
                "roll_number": "21CS1045",
                "enrollment_id": "",
            },
            {
                # Enrollment No only — a continuing student whose extract no
                # longer carries the SIF. Matches on the enrollment number.
                "sif_id": "",
                "full_name": "Joseph Mathew",
                "date_of_birth": "09-06-2006",
                "gender": "M",
                "mobile": "",
                "email": "joseph.m@student.example.edu",
                "program_code": "BT-AIDS",
                "section_label": "1A",
                "admission_year": "2025",
                "position": "3",
                "roll_number": "21AD1002",
                "enrollment_id": "TU2025AID0002",
            },
        ),
        notes=(
            "SAMPLE DATA — the four rows below show the expected shape: two students "
            "in one Section, one mobile-only, and one continuing student named by "
            "enrollment number alone. Replace them with your own rows before uploading.",
            "Mandatory: full_name, program_code, section_label, admission_year, "
            "roll_number, and AT LEAST ONE OF sif_id / enrollment_id. "
            "Optional: date_of_birth, gender.",
            "IDENTIFIERS — every row must name the student by at least one of the two, "
            "and may carry both. sif_id is issued when admission completes, so it is "
            "the only id a brand-new student has; enrollment_id is the CANONICAL "
            "number, issued later. A row is matched on whichever it carries.",
            "A row with only an enrollment_id UPDATES an existing student — it never "
            "creates one, because an enrollment number that matches nobody is a typo "
            "rather than a new admission. New students must arrive with a sif_id.",
            "If a row carries both and they point at two different students, it is "
            "rejected: one of the two is wrong.",
            "At least ONE of mobile/email is required — that is how initial credentials "
            "reach the student. Rows with neither are rejected.",
            "Dates are DD-MM-YYYY (15-08-2006). Roll numbers come from the ERP and must "
            "be unique within a Batch (Program + joining year).",
            "position is where the student currently sits in the ladder: the SEMESTER "
            "number for a semester-cadence Programme (1, 2, 3 …), or the YEAR number "
            "for a yearly one. Leave it blank for a fresh intake — blank means "
            "position 1, first year first semester. The upload screen's picker fills "
            "blanks only, so a value here always wins.",
            "The student's Batch (admission cohort, e.g. BT-CSE-2026) is derived from "
            "program_code + admission_year and created automatically — there is no "
            "column for it. Lateral entrants joining above position 1 are placed in "
            "the cohort they will graduate with, not their literal joining year.",
            "program_code must resolve within your scope, and the Section must already "
            "exist for the term you pick at upload time (see the 'sections' template).",
            "Re-uploading is safe: rows are matched on whichever identifier they "
            "carry and updated, never duplicated.",
        ),
    )
)


class ImportRunOut(BaseModel):
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
    created_batches: list[str]
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


STAFF_CSV_COLUMNS = (
    "employee_id",
    "full_name",
    "designation",
    "department_code",
    "mobile",
    "email",
    "date_of_joining",
)

register(
    CsvTemplate(
        key="staff",
        title="Staff import",
        description="Bulk staff provisioning, one row per employee.",
        columns=STAFF_CSV_COLUMNS,
        examples=(
            {
                "employee_id": "EMP-1041",
                "full_name": "Dr. Suphalakshmi Anandan",
                "designation": "Professor",
                "department_code": "CSE",
                "mobile": "9840012345",
                "email": "suphalakshmi.a@takshashila.edu.in",
                "date_of_joining": "01-06-2018",
            },
            {
                "employee_id": "EMP-1042",
                "full_name": "Dr. Murugamani P",
                "designation": "HoD",
                "department_code": "CSE",
                "mobile": "9840012346",
                "email": "murugamani.p@takshashila.edu.in",
                "date_of_joining": "15-07-2015",
            },
            {
                "employee_id": "EMP-2210",
                "full_name": "Kavitha Ramesh",
                "designation": "Office Staff",
                "department_code": "CSE",
                "mobile": "",
                "email": "kavitha.r@takshashila.edu.in",
                "date_of_joining": "",
            },
        ),
        notes=(
            "SAMPLE DATA — replace the rows below with your own before uploading.",
            "employee_id is the key rows are matched on. Re-uploading is safe: an "
            "existing employee is updated, never duplicated.",
            "designation GRANTS THE ROLE at the named Department. Accepted values: "
            "Professor, Associate Professor, Assistant Professor, Tutor, Assistant "
            "Teaching Staff, HoD, Office Staff, Timetable Cell.",
            "HoD is a singleton — one per Department. A row naming a second HoD for "
            "a Department is rejected rather than silently double-heading it; use "
            "the supersede flow on Users & roles to replace the holder.",
            "department_code is the Department the person belongs to, and the scope "
            "their role is granted at.",
            "At least ONE of mobile/email is required — that is how initial "
            "credentials reach them.",
            "Dates are DD-MM-YYYY. date_of_joining is optional.",
        ),
    )
)


class ElectiveChoiceRequest(BaseModel):
    offering_id: uuid.UUID
    term_code: str = Field(min_length=1, max_length=50)


class ElectiveOptionOut(BaseModel):
    """One choosable subject within a group, for the student's own position."""

    offering_id: uuid.UUID
    subject_code: str
    subject_name: str
    elective_group: str
    credits: int
    hours: dict[str, int]
    capacity: int | None
    seats_taken: int
    seats_left: int | None  # None when the offering is unlimited
    chosen: bool


class ElectiveGroupOut(BaseModel):
    elective_group: str
    chosen_offering_id: uuid.UUID | None
    options: list[ElectiveOptionOut]
