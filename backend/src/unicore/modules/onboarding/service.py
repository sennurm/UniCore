"""Business rules for the onboarding module. The only layer other modules may call.

Import-only provisioning (ONB): students arrive from the ERP as CSV, are validated
row by row, committed partially (valid rows in, invalid rows to a downloadable error
report), and upserted idempotently on either student identifier — the SIF id
issued at admission or the Enrollment No issued later. Sections are per-term instances
created by the Timetable Cell; allotment is dated so history stays immutable.
"""

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.logging import get_logger, timed
from unicore.core.security import AuthContext
from unicore.core.templates import strip_comments
from unicore.modules.audit import service as audit_service
from unicore.modules.auth import service as auth_service
from unicore.modules.onboarding import dao
from unicore.modules.onboarding.models import (
    RISKY_CHANGE_THRESHOLD,
    Batch,
    ImportRowError,
    ImportRun,
    SectionMembership,
    StaffProfile,
    StudentElectiveChoice,
    StudentProfile,
)
from unicore.modules.onboarding.schemas import (
    CSV_COLUMNS_V1,
    ENROLLMENT_CSV_COLUMNS,
    STAFF_CSV_COLUMNS,
    SingleStudentAdd,
)
from unicore.modules.org import service as org_service
from unicore.modules.rbac import service as rbac_service
from unicore.modules.user import service as user_service

MAX_FILE_BYTES = 50 * 1024 * 1024  # ONB §8 pre-parse gate
STUDENT_ROLE = "student"
IMPORT_ROLES = ("super-admin", "system-admin", "office-staff")
# Holders of `onb:read` (rbac ACTIONS). Most are org-scoped, so every read of
# student data must be checked against the caller's subtree — `onb:read` says the
# caller may read *rosters*, not that they may read *this* roster (ONB §4).
READ_ROLES = (
    "super-admin",
    "system-admin",
    "office-staff",
    "school-incharge",
    "hod",
    "class-incharge",
)


class RowError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


@dataclass(frozen=True)
class ImportDefaults:
    """Values chosen on the upload screen that fill **blank** cells only.

    The file is authoritative wherever it speaks (ONB-FR-21): a single-Programme
    intake needs no per-row values, a mixed ERP extract works untouched, and a
    default can never silently overwrite a stated one.
    """

    program_code: str | None = None
    position: int | None = None


def _parse_position(raw: str, defaults: ImportDefaults | None) -> int:
    """File value, else the screen default, else 1 (first year, first semester)."""
    value = raw.strip()
    if not value:
        return (defaults.position if defaults and defaults.position else None) or 1
    try:
        position = int(value)
    except ValueError:
        raise RowError("position", f"'{value}' is not a number") from None
    if position < 1:
        raise RowError("position", "position starts at 1")
    return position


# --- import pipeline ---------------------------------------------------------


async def import_csv(
    session: AsyncSession,
    ctx: AuthContext,
    filename: str,
    content: bytes,
    term_code: str,
    defaults: ImportDefaults | None = None,
) -> ImportRun:
    """Pre-parse gate → row validation → partial commit → run summary."""
    _pre_parse_gate(content)
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    # A downloaded template carries `#` notes; it must upload back unchanged.
    reader = csv.DictReader(io.StringIO(strip_comments(text_content)))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = set(CSV_COLUMNS_V1) - set(header)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header does not match schema v1 — missing: {', '.join(sorted(missing))}.",
        )

    run = ImportRun(
        filename=filename,
        file_hash=hashlib.sha256(content).hexdigest(),
        term_code=term_code,
        uploaded_by=ctx.user_id,
    )
    session.add(run)
    await session.flush()

    scope_paths = await rbac_service.scope_paths_for(ctx, IMPORT_ROLES)
    seen_ids: set[tuple[str, str]] = set()
    new_batches: set[str] = set()
    created = updated = unchanged = rejected = 0
    risky_changes = 0

    with timed("import run processed", **{"db.system.name": "postgresql"}):
        for index, raw in enumerate(reader, start=2):  # row 1 is the header
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            try:
                # A row is identified by either id, so a duplicate of *either*
                # is a duplicate of the student (ONB-FR-05).
                identifiers = _row_identifiers(row)
                clash = next((i for i in identifiers if i in seen_ids), None)
                if clash is not None:
                    raise RowError(clash[0], "in-file duplicate — first occurrence wins")
                seen_ids.update(identifiers)
                outcome, risky = await _upsert_row(
                    session, ctx, row, term_code, scope_paths, defaults, new_batches
                )
            except RowError as err:
                rejected += 1
                session.add(
                    ImportRowError(
                        run_id=run.id,
                        row_number=index,
                        field=err.field,
                        reason=err.reason,
                        raw_row=",".join(f"{k}={v}" for k, v in row.items()),
                    )
                )
                continue
            risky_changes += 1 if risky else 0
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1

    run.created_batches = sorted(new_batches)
    run.rows_total = created + updated + unchanged + rejected
    run.rows_created, run.rows_updated = created, updated
    run.rows_unchanged, run.rows_rejected = unchanged, rejected

    committed_rows = created + updated + unchanged
    if committed_rows and risky_changes / committed_rows > RISKY_CHANGE_THRESHOLD:
        # ONB §8: a feed rewriting org mapping/DOB wholesale pauses for confirmation.
        run.status = "needs-review"
        get_logger().warning(
            "import run parked for review",
            risky_changes=risky_changes,
            committed_rows=committed_rows,
        )
    else:
        run.status = "committed"

    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.import.completed",
        object_type="import_run",
        object_id=str(run.id),
        after={
            "filename": filename,
            "file_hash": run.file_hash,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "rejected": rejected,
            "status": run.status,
            "batches_created": sorted(new_batches),
        },
    )
    await session.commit()
    return run


def _pre_parse_gate(content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 50 MB limit.")


def _row_identifiers(row: dict[str, str]) -> list[tuple[str, str]]:
    """The (field, value) identifiers a row carries — SIF id, Enrollment No, or both.

    A student may be named by either, so in-file duplicate detection has to look
    at both: two rows sharing only an enrollment number are still the same person.
    """
    return [
        (field, row.get(field, "").strip())
        for field in ("sif_id", "enrollment_id")
        if row.get(field, "").strip()
    ]


def _require(row: dict[str, str], field: str) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise RowError(field, "mandatory field is missing")
    return value


async def _upsert_row(
    session: AsyncSession,
    ctx: AuthContext,
    row: dict[str, str],
    term_code: str,
    scope_paths: list[str] | None,
    defaults: ImportDefaults | None = None,
    new_batches: set[str] | None = None,
) -> tuple[str, bool]:
    """Validate and upsert one row. Returns (outcome, risky_change)."""
    sif_id = row.get("sif_id", "").strip()
    enrollment_id = row.get("enrollment_id", "").strip()
    if not sif_id and not enrollment_id:
        raise RowError(
            "sif_id/enrollment_id",
            "give at least one identifier — the SIF id issued at admission or the "
            "Enrollment No issued later",
        )
    full_name = _require(row, "full_name")
    roll_number = _require(row, "roll_number")
    program_code = row.get("program_code", "").strip() or (
        defaults.program_code if defaults else ""
    )
    if not program_code:
        raise RowError("program_code", "mandatory field is missing")
    section_label = _require(row, "section_label")
    admission_year = _parse_year(row.get("admission_year", ""))
    dob = _parse_date(row.get("date_of_birth", ""))
    mobile = row.get("mobile") or None
    email = row.get("email") or None
    if not mobile and not email:
        raise RowError("mobile/email", "no contact channel — credentials cannot be delivered")

    program = await _resolve_program(session, program_code, scope_paths)
    # Screen pickers fill blanks only — a value in the file always wins (ONB-FR-21).
    position = _parse_position(row.get("position", ""), defaults)
    await validate_position(session, program, position)
    section = await org_service.find_section(session, program.id, section_label, term_code)
    if section is None:
        raise RowError(
            "section_label",
            f"no Section '{section_label}' for term {term_code} — Timetable Cell must create it",
        )

    # Either id resolves the same student (canonical-id decision, 28-07-2026).
    # A file may carry one or the other: the SIF exists from admission, the
    # Enrollment No is issued later, and a mid-programme extract often has only
    # the latter.
    by_sif = await user_service.get_by_sif_id(session, sif_id) if sif_id else None
    by_enrollment = (
        await user_service.get_by_enrollment_id(session, enrollment_id)
        if enrollment_id
        else None
    )
    if by_sif is not None and by_enrollment is not None and by_sif.id != by_enrollment.id:
        raise RowError(
            "enrollment_id",
            f"'{sif_id}' and '{enrollment_id}' identify two different students — "
            "one of them is wrong",
        )
    existing = by_sif or by_enrollment

    if existing is None and not sif_id:
        # Enrollment numbers are issued to students who already exist, so one
        # matching nobody is a typo, not a new admission. Creating here would
        # mint a phantom student that no later SIF-bearing feed could reconcile.
        raise RowError(
            "enrollment_id",
            f"no student holds Enrollment No '{enrollment_id}'; a new student must "
            "arrive with the SIF id issued at admission",
        )

    if existing is None:
        user = await user_service.provision_student(
            session,
            ctx,
            username=_username_for(roll_number, sif_id),
            full_name=full_name,
            sif_id=sif_id,
            email=email,
            mobile=mobile,
        )
        await _assert_roll_free(session, program.id, admission_year, roll_number, user.id)
        batch, batch_is_new = await resolve_batch(session, program, admission_year, position)
        if batch_is_new and new_batches is not None:
            new_batches.add(batch.code)
        session.add(
            StudentProfile(
                user_id=user.id,
                program_id=program.id,
                roll_number=roll_number,
                admission_year=admission_year,
                batch_id=batch.id,
                position=position,
                date_of_birth=dob,
                gender=row.get("gender") or None,
            )
        )
        if enrollment_id:
            await user_service.set_enrollment_id(session, ctx, user, enrollment_id)
        await session.flush()
        # A student with no role can sign in and do nothing, so provisioning
        # grants it in the same transaction (AUTH §1: students are in the RBAC
        # model). Programme-scoped: Sections are per-term, membership is ONB's.
        await rbac_service.ensure_sole_grant(
            session, ctx, user.id, STUDENT_ROLE, program.id, "student provisioned"
        )
        await _reallot(session, user.id, section.id, date.today())
        return "created", False

    # Idempotent update — ERP is master for identity fields; diffs are audited.
    profile = await dao.get_profile(session, existing.id)
    await _assert_roll_free(session, program.id, admission_year, roll_number, existing.id)
    changed = False
    risky = False
    if existing.full_name != full_name:
        existing.full_name = full_name
        changed = True
    for attr, value in (("email", email), ("mobile", mobile)):
        if value and getattr(existing, attr) != value:
            setattr(existing, attr, value)
            changed = True
    if profile is not None:
        programme_moved = profile.program_id != program.id
        if programme_moved:
            profile.program_id = program.id
            changed = risky = True
        if dob and profile.date_of_birth != dob:
            profile.date_of_birth = dob
            changed = risky = True
        if profile.roll_number != roll_number:
            profile.roll_number = roll_number
            changed = True
        if profile.position != position:
            profile.position = position
            changed = True
        # A cohort is decided once, at first import, and recorded (ONB-FR-19) —
        # re-deriving it on every re-import would let an edited admission_year
        # quietly move students between cohorts. Two exceptions: a student who
        # predates batches gets backfilled, and a Programme move necessarily
        # changes cohort (that move is itself flagged risky above, so the §8
        # guardrail already parks a feed doing it wholesale).
        if profile.batch_id is None or programme_moved:
            batch, batch_is_new = await resolve_batch(
                session, program, admission_year, position
            )
            if batch_is_new and new_batches is not None:
                new_batches.add(batch.code)
            if profile.batch_id != batch.id:
                profile.batch_id = batch.id
                changed = True
        else:
            current = await dao.get_batch_by_id(session, profile.batch_id)
            target_year = await cohort_year_for(session, program, admission_year, position)
            if current is not None and current.joining_year != target_year:
                raise RowError(
                    "admission_year",
                    f"student is already in batch '{current.code}'; moving cohorts is an "
                    "explicit correction, not an import side effect",
                )
    if enrollment_id and await user_service.set_enrollment_id(
        session, ctx, existing, enrollment_id
    ):
        changed = True
    # Idempotent, so a re-import backfills students provisioned before the role
    # existed rather than needing a separate migration pass.
    # Sole, not merely present: a Programme move must relocate the grant rather
    # than leave the student holding one on the Programme they left.
    if await rbac_service.ensure_sole_grant(
        session, ctx, existing.id, STUDENT_ROLE, program.id, "programme changed on import"
    ):
        changed = True
    membership = await dao.open_membership(session, existing.id)
    if membership is None or membership.section_id != section.id:
        await _reallot(session, existing.id, section.id, date.today())
        changed = True
    await session.flush()
    return ("updated" if changed else "unchanged"), risky


async def _resolve_program(
    session: AsyncSession, program_code: str, scope_paths: list[str] | None
):
    matches = await org_service.resolve_by_code(session, program_code, "program", scope_paths)
    if not matches:
        raise RowError("program_code", f"unknown Program '{program_code}' in your scope")
    if len(matches) > 1:
        raise RowError("program_code", f"'{program_code}' is ambiguous — matches {len(matches)}")
    return matches[0]


async def _assert_roll_free(
    session: AsyncSession,
    program_id: uuid.UUID,
    admission_year: int,
    roll_number: str,
    user_id: uuid.UUID,
) -> None:
    holder = await dao.roll_number_holder(session, program_id, admission_year, roll_number)
    if holder is not None and holder.user_id != user_id:
        raise RowError("roll_number", f"'{roll_number}' already used in this Program + year")


def _parse_year(value: str) -> int:
    try:
        year = int(value)
    except ValueError:
        raise RowError("admission_year", "must be a four-digit year") from None
    if not 1900 <= year <= 2200:
        raise RowError("admission_year", "outside the plausible range")
    return year


def _parse_date(value: str) -> date | None:
    """DD-MM-YYYY per the localisation rule; ISO accepted as a courtesy."""
    if not value:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise RowError("date_of_birth", "must be DD-MM-YYYY")


def _username_for(roll_number: str, sif_id: str) -> str:
    base = "".join(ch for ch in roll_number.lower() if ch.isalnum()) or sif_id.lower()
    return base[:100]


# --- lifecycle ---------------------------------------------------------------


async def add_single_student(
    session: AsyncSession, ctx: AuthContext, data: SingleStudentAdd
) -> uuid.UUID:
    """Mid-term add through the identical validation path as a bulk row."""
    scope_paths = await rbac_service.scope_paths_for(ctx, IMPORT_ROLES)
    row = {
        "sif_id": data.sif_id,
        "full_name": data.full_name,
        "date_of_birth": data.date_of_birth.strftime("%d-%m-%Y") if data.date_of_birth else "",
        "gender": data.gender or "",
        "mobile": data.mobile or "",
        "email": data.email or "",
        "program_code": data.program_code,
        "section_label": data.section_label,
        "admission_year": str(data.admission_year),
        "roll_number": data.roll_number,
        "enrollment_id": data.enrollment_id or "",
    }
    try:
        await _upsert_row(session, ctx, row, data.term_code, scope_paths)
    except RowError as err:
        raise HTTPException(status_code=422, detail=f"{err.field}: {err.reason}") from None
    user = await user_service.get_by_sif_id(session, data.sif_id)
    assert user is not None
    await session.commit()
    return user.id


async def allot_section(
    session: AsyncSession, ctx: AuthContext, user_id: uuid.UUID, section_id: uuid.UUID, when: date
) -> SectionMembership:
    """Re-allotment closes the previous membership; past attendance stays with it."""
    section = await org_service.get_unit(session, section_id)
    if section.type != "section":
        raise HTTPException(status_code=422, detail="Target must be a Section instance.")
    membership = await _reallot(session, user_id, section_id, when)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.section.alloted",
        object_type="user",
        object_id=str(user_id),
        scope=section.path,
        after={"section_id": str(section_id), "effective_from": when.isoformat()},
    )
    await session.commit()
    return membership


async def _reallot(
    session: AsyncSession, user_id: uuid.UUID, section_id: uuid.UUID, when: date
) -> SectionMembership:
    current = await dao.open_membership(session, user_id)
    if current is not None:
        if current.section_id == section_id:
            return current
        current.effective_to = when
        await session.flush()
    membership = SectionMembership(
        user_id=user_id, section_id=section_id, effective_from=when
    )
    session.add(membership)
    await session.flush()
    return membership


async def transfer_student(
    session: AsyncSession,
    ctx: AuthContext,
    user_id: uuid.UUID,
    new_program_id: uuid.UUID,
    new_section_id: uuid.UUID | None,
    when: date,
) -> None:
    """System Admin only — crosses org-unit scopes; history is closed, never deleted."""
    profile = await dao.get_profile(session, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    program = await org_service.get_unit(session, new_program_id)
    if program.type != "program":
        raise HTTPException(status_code=422, detail="Target must be a Program.")
    before = {"program_id": str(profile.program_id)}
    profile.program_id = new_program_id
    # Authorization follows the student: the grant on the old Programme is
    # revoked in the same transaction, so they never hold one where they no
    # longer study (AUTH §8).
    await rbac_service.ensure_sole_grant(
        session, ctx, user_id, STUDENT_ROLE, new_program_id, "programme transfer"
    )
    if new_section_id is not None:
        await _reallot(session, user_id, new_section_id, when)
    else:
        current = await dao.open_membership(session, user_id)
        if current is not None:
            current.effective_to = when  # section-less until re-allotment
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.student.transferred",
        object_type="user",
        object_id=str(user_id),
        before=before,
        after={"program_id": str(new_program_id), "effective_from": when.isoformat()},
    )
    await session.commit()


async def withdraw_student(
    session: AsyncSession, ctx: AuthContext, user_id: uuid.UUID, when: date, reason: str
) -> None:
    """State change + immediate session revocation; history retained for statute."""
    user = await user_service.get_user(session, user_id)
    membership = await dao.open_membership(session, user_id)
    if membership is not None:
        membership.effective_to = when  # removed from future Sessions
    user.status = "withdrawn"
    await auth_service.revoke_user_sessions(user.id)
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.student.withdrawn",
        object_type="user",
        object_id=str(user_id),
        after={"status": "withdrawn", "effective_to": when.isoformat()},
        reason=reason,
    )
    await session.commit()


# --- activation / credentials ------------------------------------------------


async def deliver_credentials(
    session: AsyncSession, ctx: AuthContext, run_id: uuid.UUID
) -> dict[str, int]:
    """Activation pipeline: credential generation → delivery → ACTIVE (ONB-FR-06)."""
    run = await dao.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Import run not found.")
    if run.status == "needs-review":
        raise HTTPException(
            status_code=409, detail="Import run is held for review — release it before delivery."
        )
    pending = await dao.list_pending_delivery(session)
    delivered = failed = 0
    for profile in pending:
        user = await user_service.get_user(session, profile.user_id)
        try:
            channel = await auth_service.set_temp_password(session, ctx, user.id)
            profile.credential_delivery = "delivered"
            profile.delivery_channel = channel
            user.status = "active"
            delivered += 1
        except Exception:  # noqa: BLE001 — delivery failure is a per-student flag
            profile.credential_delivery = "failed"
            failed += 1
            get_logger().warning("credential delivery failed", user_id=str(user.id))
    await session.commit()
    return {"delivered": delivered, "failed": failed}


# --- reads -------------------------------------------------------------------


async def membership_as_of(
    session: AsyncSession, user_id: uuid.UUID, as_of: date
) -> SectionMembership | None:
    """Consumed by TTM/ATT (ONB-FR-10)."""
    return await dao.membership_as_of(session, user_id, as_of)


async def section_roster(
    session: AsyncSession,
    ctx: AuthContext,
    section_id: uuid.UUID,
    as_of: date | None = None,
) -> list[dict[str, object]]:
    """A Section's roster as of a date (ONB-FR-17).

    The Section id is client-supplied, so it is authorised against the caller's
    own scope before any student data is read — otherwise any `onb:read` holder
    (HoD, Class In-charge) could name a Section in another School and receive its
    students' names, SIF/enrollment ids and credential-delivery state.
    """
    await rbac_service.ensure_scope_covers(session, ctx, READ_ROLES, section_id)
    memberships = await dao.section_roster_as_of(session, section_id, as_of or date.today())
    batch_codes: dict[uuid.UUID, str] = {}
    roster: list[dict[str, object]] = []
    for m in memberships:
        user = await user_service.get_user(session, m.user_id)
        profile = await dao.get_profile(session, m.user_id)
        year: int | None = None
        batch_code: str | None = None
        if profile is not None:
            programme = await org_service.get_unit(session, profile.program_id)
            cadence = await org_service.effective_cadence(session, programme)
            # Year is derived from position, never stored (ONB-FR-20).
            year = org_service.year_of(cadence, profile.position)
            if profile.batch_id is not None:
                if profile.batch_id not in batch_codes:
                    batch = await dao.get_batch_by_id(session, profile.batch_id)
                    batch_codes[profile.batch_id] = batch.code if batch else ""
                batch_code = batch_codes[profile.batch_id] or None
        roster.append(
            {
                "user_id": str(user.id),
                "sif_id": user.sif_id,
                "enrollment_id": user.enrollment_id,
                "full_name": user.full_name,
                "status": user.status,
                "roll_number": profile.roll_number if profile else None,
                "credential_delivery": profile.credential_delivery if profile else None,
                "position": profile.position if profile else None,
                "year": year,
                "batch_code": batch_code,
            }
        )
    return roster


async def positions_in_programme(
    session: AsyncSession, program_id: uuid.UUID
) -> set[int]:
    """Which ladder positions currently hold students — org uses this to refuse a
    cadence change that would strand them off the new, shorter ladder."""
    return set(await dao.count_students_by_position(session, program_id))


async def headcount_by_position(
    session: AsyncSession, program_id: uuid.UUID
) -> dict[int, int]:
    """Active students of a Programme per position — TTM sizes Sections with this."""
    return await dao.count_students_by_position(session, program_id)


async def cohort_year_for(
    session: AsyncSession, program: org_service.OrgUnit, joining_year: int, position: int
) -> int:
    """The joining year of the cohort this student belongs to.

    Normally their own joining year. A **lateral entrant** belongs to the cohort
    they will *graduate* with instead: entering at semester 3 in 2026 means
    sitting, being timetabled and graduating with the 2025 intake.

    The offset comes from the Programme's declared `lateral_entry_semester`
    (ONB-FR-19), **not** from whatever position the row happens to carry. Those
    are different things — on a mid-programme backfill the position is where the
    student is *now*, so treating it as an entry position would push a 2024
    admission sitting in semester 3 into the 2023 cohort. A student is lateral
    only when the Programme declares a lateral entry point and they sit on it.
    """
    lateral_at = program.lateral_entry_semester
    if not lateral_at or lateral_at <= 1 or position != lateral_at:
        return joining_year
    cadence = await org_service.effective_cadence(session, program)
    return joining_year - (org_service.year_of(cadence, lateral_at) - 1)


async def resolve_batch(
    session: AsyncSession, program: org_service.OrgUnit, joining_year: int, position: int
) -> tuple[Batch, bool]:
    """The student's admission cohort, created on first use (ONB-FR-19).

    A lateral entrant belongs to the cohort they will *graduate* with, not their
    literal joining year: entering at semester 3 in 2026 means sitting, being
    timetabled and graduating with the 2025 intake.

    The offset comes from the Programme's declared `lateral_entry_semester`
    (ONB-FR-19), **not** from whatever position the row happens to carry. Those
    are different things: on a mid-programme backfill the position is where the
    student is *now*, so treating it as an entry position would push a 2024
    admission at semester 3 into the 2023 cohort. A student is lateral only when
    the Programme declares a lateral entry point and they are sitting on it.
    """
    cohort_year = await cohort_year_for(session, program, joining_year, position)
    existing = await dao.find_batch(session, program.id, cohort_year)
    if existing is not None:
        return existing, False

    template = await org_service.get_setting(session, "batch_name_template")
    code = template.format(programme_code=program.code, joining_year=cohort_year)
    batch = Batch(program_id=program.id, joining_year=cohort_year, code=code)
    session.add(batch)
    await session.flush()
    get_logger().info(
        "batch created", batch_code=code, program_code=program.code, joining_year=cohort_year
    )
    return batch, True


async def validate_position(
    session: AsyncSession, program: org_service.OrgUnit, position: int
) -> None:
    """A position outside the Programme's ladder is rejected, never clamped —
    clamping would place a student in a term that does not exist."""
    cadence = await org_service.effective_cadence(session, program)
    ladder = org_service.position_ladder(cadence, program.duration_years)
    if not ladder:
        # No duration on the Programme means no known upper bound. Blocking the
        # student would punish them for an incomplete catalogue row; Section
        # generation is where the missing duration surfaces as a warning.
        return
    if position not in ladder:
        raise RowError(
            "position", f"position {position} is outside 1..{ladder[-1]} for '{program.code}'"
        )


async def _own_uploads_only(ctx: AuthContext) -> str | None:
    """The uploader whose runs the caller may see, or None for university-wide.

    ONB §4: nobody outside their scope sees another scope's import runs. An
    ImportRun carries no org unit of its own — a file may span Programmes — so a
    scoped caller is limited to the runs they uploaded themselves, which is the
    strictest reading and never over-shares. University-wide roles see everything.
    """
    scope_paths = await rbac_service.scope_paths_for(ctx, IMPORT_ROLES)
    return None if scope_paths is None else ctx.user_id


async def list_runs(session: AsyncSession, ctx: AuthContext, limit: int) -> list[ImportRun]:
    return list(await dao.list_runs(session, limit, await _own_uploads_only(ctx)))


async def run_errors(
    session: AsyncSession, ctx: AuthContext, run_id: uuid.UUID
) -> list[ImportRowError]:
    """Error rows quote the raw CSV line, so they carry the same PII as the import
    itself and are gated by the same rule as the run that produced them."""
    await _require_run_visible(session, ctx, run_id)
    return list(await dao.run_errors(session, run_id))


async def _require_run_visible(
    session: AsyncSession, ctx: AuthContext, run_id: uuid.UUID
) -> ImportRun:
    run = await dao.get_run(session, run_id)
    uploader = await _own_uploads_only(ctx)
    # 404, not 403: a scoped caller learns nothing about runs outside their scope.
    if run is None or (uploader is not None and run.uploaded_by != uploader):
        raise HTTPException(status_code=404, detail="Import run not found.")
    return run


async def confirm_run(
    session: AsyncSession, ctx: AuthContext, run_id: uuid.UUID
) -> ImportRun:
    """System Admin release of a run parked by the risky-change guardrail."""
    run = await dao.get_run(session, run_id)
    if run is None or run.status != "needs-review":
        raise HTTPException(status_code=404, detail="No import run awaiting review.")
    run.status = "committed"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.import.confirmed",
        object_type="import_run",
        object_id=str(run.id),
        after={"status": "committed"},
    )
    await session.commit()
    return run


def error_report_csv(errors: list[ImportRowError]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["row_number", "field", "reason", "raw_row"])
    for e in errors:
        writer.writerow([e.row_number, e.field, e.reason, e.raw_row])
    return buffer.getvalue()


__all__ = [
    "add_single_student",
    "allot_section",
    "run_errors",
    "confirm_run",
    "deliver_credentials",
    "error_report_csv",
    "import_csv",
    "list_runs",
    "membership_as_of",
    "section_roster",
    "transfer_student",
    "withdraw_student",
]


# --- enrollment numbers (issued after admission) -----------------------------


async def import_enrollment_ids(
    session: AsyncSession, ctx: AuthContext, content: bytes
) -> dict[str, object]:
    """Assign enrollment numbers to already-onboarded students, matched on SIF.

    Partial commit like every other import: valid rows land, invalid rows return
    an error report. Re-uploading is safe; correcting a number is allowed and
    audited (ONB-FR-18).
    """
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    try:
        text_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    body = strip_comments(text_content)
    reader = csv.DictReader(io.StringIO(body))
    missing = set(ENROLLMENT_CSV_COLUMNS) - {h.strip() for h in (reader.fieldnames or [])}
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header must include: {', '.join(sorted(missing))}.",
        )

    assigned = unchanged = 0
    errors: list[dict[str, object]] = []
    seen: set[str] = set()

    for row_number, raw in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        sif_id = row.get("sif_id", "")
        enrollment_id = row.get("enrollment_id", "")
        try:
            if not sif_id or not enrollment_id:
                raise RowError(
                    "sif_id" if not sif_id else "enrollment_id", "mandatory field is missing"
                )
            if enrollment_id in seen:
                raise RowError("enrollment_id", "in-file duplicate — enrollment ids are unique")
            seen.add(enrollment_id)
            student = await user_service.get_by_sif_id(session, sif_id)
            if student is None:
                raise RowError("sif_id", f"no student with SIF id '{sif_id}'")
            changed = await user_service.set_enrollment_id(session, ctx, student, enrollment_id)
            assigned += 1 if changed else 0
            unchanged += 0 if changed else 1
        except RowError as err:
            errors.append(
                {
                    "row_number": row_number,
                    "field": err.field,
                    "reason": err.reason,
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items() if v),
                }
            )
        except HTTPException as err:  # e.g. the number belongs to another student
            errors.append(
                {
                    "row_number": row_number,
                    "field": "enrollment_id",
                    "reason": str(err.detail),
                    "raw_row": ",".join(f"{k}={v}" for k, v in row.items() if v),
                }
            )
    await session.commit()
    return {
        "rows_total": assigned + unchanged + len(errors),
        "rows_assigned": assigned,
        "rows_unchanged": unchanged,
        "rows_rejected": len(errors),
        "errors": errors,
    }


# --- staff import (mirrors the student pipeline) ------------------------------

# A designation names the role the staff member holds at their Department.
# Singleton roles (hod) still go through the normal grant rules, so a file
# cannot quietly double-head a Department.
DESIGNATION_ROLES: dict[str, str] = {
    "professor": "professor",
    "associate professor": "associate-professor",
    "assistant professor": "assistant-professor",
    "tutor": "tutor",
    "assistant teaching staff": "assistant-teaching-staff",
    "hod": "hod",
    "head of department": "hod",
    "office staff": "office-staff",
    "timetable cell": "timetable-cell",
}


async def import_staff(
    session: AsyncSession, ctx: AuthContext, filename: str, content: bytes
) -> ImportRun:
    """Bulk staff provisioning — same shape as the student import: partial
    commit, per-row error report, idempotent upsert on employee id.

    The designation column grants the matching role at the named Department, so
    ~2,000 staff do not need their roles issued one screen at a time.
    """
    _pre_parse_gate(content)
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    reader = csv.DictReader(io.StringIO(strip_comments(text_content)))
    missing = set(STAFF_CSV_COLUMNS) - {h.strip() for h in (reader.fieldnames or [])}
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header does not match the staff template — missing: "
            f"{', '.join(sorted(missing))}.",
        )

    run = ImportRun(
        filename=filename,
        file_hash=hashlib.sha256(content).hexdigest(),
        term_code="-",  # staff are not term-scoped
        uploaded_by=ctx.user_id,
    )
    session.add(run)
    await session.flush()

    seen: set[str] = set()
    created = updated = unchanged = rejected = 0
    with timed("staff import processed", **{"db.system.name": "postgresql"}):
        for index, raw in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            try:
                employee_id = _require(row, "employee_id")
                if employee_id in seen:
                    raise RowError("employee_id", "in-file duplicate — first occurrence wins")
                seen.add(employee_id)
                outcome = await _upsert_staff_row(session, ctx, row, employee_id)
            except RowError as err:
                rejected += 1
                session.add(
                    ImportRowError(
                        run_id=run.id,
                        row_number=index,
                        field=err.field,
                        reason=err.reason,
                        raw_row=",".join(f"{k}={v}" for k, v in row.items()),
                    )
                )
                continue
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1

    run.rows_total = created + updated + unchanged + rejected
    run.rows_created, run.rows_updated = created, updated
    run.rows_unchanged, run.rows_rejected = unchanged, rejected
    run.status = "committed"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.staff-import.completed",
        object_type="import_run",
        object_id=str(run.id),
        after={"created": created, "updated": updated, "rejected": rejected},
    )
    await session.commit()
    return run


async def _upsert_staff_row(
    session: AsyncSession, ctx: AuthContext, row: dict[str, str], employee_id: str
) -> str:
    full_name = _require(row, "full_name")
    designation = _require(row, "designation").lower()
    role_code = DESIGNATION_ROLES.get(designation)
    if role_code is None:
        raise RowError(
            "designation",
            f"'{designation}' is not a known designation — one of: "
            f"{', '.join(sorted(DESIGNATION_ROLES))}",
        )
    department_code = _require(row, "department_code")
    departments = await org_service.resolve_by_code(session, department_code, "department", None)
    if not departments:
        raise RowError("department_code", f"unknown Department '{department_code}'")
    department = departments[0]

    mobile = row.get("mobile") or None
    email = row.get("email") or None
    if not mobile and not email:
        raise RowError("mobile/email", "no contact channel — credentials cannot be delivered")

    profile = await dao.get_staff_by_employee_id(session, employee_id)
    if profile is None:
        user = await user_service.provision_staff(
            session,
            ctx,
            username=_username_for(employee_id, employee_id),
            full_name=full_name,
            email=email,
            mobile=mobile,
        )
        session.add(
            StaffProfile(
                user_id=user.id,
                employee_id=employee_id,
                department_id=department.id,
                designation=designation,
                date_of_joining=_parse_date(row.get("date_of_joining", "")),
            )
        )
        await session.flush()
        await _grant_designation(session, ctx, user.id, role_code, department.id)
        return "created"

    user = await user_service.get_user(session, profile.user_id)
    changed = False
    if user.full_name != full_name:
        user.full_name = full_name
        changed = True
    for attr, value in (("email", email), ("mobile", mobile)):
        if value and getattr(user, attr) != value:
            setattr(user, attr, value)
            changed = True
    if profile.designation != designation or profile.department_id != department.id:
        profile.designation = designation
        profile.department_id = department.id
        changed = True
    if await _grant_designation(session, ctx, user.id, role_code, department.id):
        changed = True
    await session.flush()
    return "updated" if changed else "unchanged"


async def _grant_designation(
    session: AsyncSession, ctx: AuthContext, user_id: uuid.UUID, role_code: str,
    department_id: uuid.UUID,
) -> bool:
    """Issue the designation's role, surfacing a singleton clash as a row error
    rather than letting a file quietly double-head a Department."""
    try:
        return await rbac_service.ensure_sole_grant(
            session, ctx, user_id, role_code, department_id, "designation changed on import"
        )
    except HTTPException as err:
        raise RowError("designation", str(err.detail)) from None


# --- student elective selection ----------------------------------------------


async def elective_options(
    session: AsyncSession, ctx: AuthContext, term_code: str
) -> list[dict[str, object]]:
    """The elective groups open to the caller this term, and what they picked.

    Resolved from the AuthContext, never a client-supplied id (project rule):
    a student sees their own Programme and position, and nobody else's.
    """
    profile = await dao.get_profile(session, uuid.UUID(ctx.user_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="No student record for this account.")

    offerings = await org_service.list_offerings(
        session, profile.program_id, profile.position, kind="elective"
    )
    chosen = {
        choice.elective_group: choice
        for choice in await dao.elective_choices_for(session, profile.user_id, term_code)
    }

    groups: dict[str, list[dict[str, object]]] = {}
    for row in offerings:
        subject = cast(Any, row["subject"])
        group = str(subject.elective_group)
        groups.setdefault(group, []).append(
            {
                "offering_id": row["id"],
                "subject_code": subject.code,
                "subject_name": subject.name,
                "elective_group": group,
                "credits": subject.credits,
                "theory_hours": subject.theory_hours,
                "lab_hours": subject.lab_hours,
                "chosen": group in chosen and chosen[group].offering_id == row["id"],
            }
        )

    return [
        {
            "elective_group": group,
            "chosen_offering_id": chosen[group].offering_id if group in chosen else None,
            "options": sorted(options, key=lambda o: str(o["subject_code"])),
        }
        for group, options in sorted(groups.items())
    ]


async def choose_elective(
    session: AsyncSession, ctx: AuthContext, offering_id: uuid.UUID, term_code: str
) -> StudentElectiveChoice:
    """Record the student's pick. Changing it replaces the previous choice for
    that group — the database enforces one per group per term, so a
    double-submit cannot leave them enrolled in two alternatives."""
    profile = await dao.get_profile(session, uuid.UUID(ctx.user_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="No student record for this account.")

    offering = await org_service.get_offering(session, offering_id)
    subject = await org_service.get_subject(session, offering.subject_id)
    if subject.kind != "elective":
        raise HTTPException(
            status_code=422, detail=f"'{subject.code}' is a core subject — it is not chosen."
        )
    if offering.program_id != profile.program_id:
        raise HTTPException(
            status_code=403, detail="That subject is not offered to your Programme."
        )
    if offering.position != profile.position:
        raise HTTPException(
            status_code=422,
            detail=f"'{subject.code}' is taught at position {offering.position}; "
            f"you are at {profile.position}.",
        )

    existing = await dao.elective_choice_for_group(
        session, profile.user_id, term_code, str(subject.elective_group)
    )
    if existing is not None:
        if existing.offering_id == offering_id:
            return existing
        existing.offering_id = offering_id
        choice = existing
    else:
        choice = StudentElectiveChoice(
            user_id=profile.user_id,
            offering_id=offering_id,
            term_code=term_code,
            elective_group=str(subject.elective_group),
        )
        session.add(choice)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.elective.chosen",
        object_type="student_elective_choice",
        object_id=str(choice.id),
        after={"subject": subject.code, "group": subject.elective_group, "term": term_code},
    )
    await session.commit()
    return choice
