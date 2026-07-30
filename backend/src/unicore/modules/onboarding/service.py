"""Business rules for the onboarding module. The only layer other modules may call.

Import-only provisioning (ONB): students arrive from the ERP as CSV, are validated
row by row, committed partially (valid rows in, invalid rows to a downloadable error
report), and upserted idempotently on SIF id. Sections are per-term instances
created by the Timetable Cell; allotment is dated so history stays immutable.
"""

import csv
import hashlib
import io
import uuid
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.logging import get_logger, timed
from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.auth import service as auth_service
from unicore.modules.onboarding import dao
from unicore.modules.onboarding.models import (
    RISKY_CHANGE_THRESHOLD,
    ImportBatch,
    ImportRowError,
    SectionMembership,
    StudentProfile,
)
from unicore.modules.onboarding.schemas import (
    CSV_COLUMNS_V1,
    ENROLLMENT_CSV_COLUMNS,
    SingleStudentAdd,
)
from unicore.modules.org import service as org_service
from unicore.modules.rbac import service as rbac_service
from unicore.modules.user import service as user_service

MAX_FILE_BYTES = 50 * 1024 * 1024  # ONB §8 pre-parse gate
IMPORT_ROLES = ("super-admin", "system-admin", "office-staff")


class RowError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


# --- import pipeline ---------------------------------------------------------


async def import_csv(
    session: AsyncSession,
    ctx: AuthContext,
    filename: str,
    content: bytes,
    term_code: str,
) -> ImportBatch:
    """Pre-parse gate → row validation → partial commit → batch summary."""
    _pre_parse_gate(content)
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    reader = csv.DictReader(io.StringIO(text_content))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = set(CSV_COLUMNS_V1) - set(header)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header does not match schema v1 — missing: {', '.join(sorted(missing))}.",
        )

    batch = ImportBatch(
        filename=filename,
        file_hash=hashlib.sha256(content).hexdigest(),
        term_code=term_code,
        uploaded_by=ctx.user_id,
    )
    session.add(batch)
    await session.flush()

    scope_paths = await rbac_service.scope_paths_for(ctx, IMPORT_ROLES)
    seen_sif_ids: set[str] = set()
    created = updated = unchanged = rejected = 0
    risky_changes = 0

    with timed("import batch processed", **{"db.system.name": "postgresql"}):
        for index, raw in enumerate(reader, start=2):  # row 1 is the header
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            try:
                sif_id = _require(row, "sif_id")
                if sif_id in seen_sif_ids:
                    raise RowError("sif_id", "in-file duplicate — first occurrence wins")
                seen_sif_ids.add(sif_id)
                outcome, risky = await _upsert_row(session, ctx, row, term_code, scope_paths)
            except RowError as err:
                rejected += 1
                session.add(
                    ImportRowError(
                        batch_id=batch.id,
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

    batch.rows_total = created + updated + unchanged + rejected
    batch.rows_created, batch.rows_updated = created, updated
    batch.rows_unchanged, batch.rows_rejected = unchanged, rejected

    committed_rows = created + updated + unchanged
    if committed_rows and risky_changes / committed_rows > RISKY_CHANGE_THRESHOLD:
        # ONB §8: a feed rewriting org mapping/DOB wholesale pauses for confirmation.
        batch.status = "needs-review"
        get_logger().warning(
            "import batch parked for review",
            risky_changes=risky_changes,
            committed_rows=committed_rows,
        )
    else:
        batch.status = "committed"

    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.import.completed",
        object_type="import_batch",
        object_id=str(batch.id),
        after={
            "filename": filename,
            "file_hash": batch.file_hash,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "rejected": rejected,
            "status": batch.status,
        },
    )
    await session.commit()
    return batch


def _pre_parse_gate(content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 50 MB limit.")


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
) -> tuple[str, bool]:
    """Validate and upsert one row. Returns (outcome, risky_change)."""
    sif_id = _require(row, "sif_id")
    enrollment_id = row.get("enrollment_id", "").strip()
    full_name = _require(row, "full_name")
    roll_number = _require(row, "roll_number")
    program_code = _require(row, "program_code")
    section_label = _require(row, "section_label")
    admission_year = _parse_year(row.get("admission_year", ""))
    dob = _parse_date(row.get("date_of_birth", ""))
    mobile = row.get("mobile") or None
    email = row.get("email") or None
    if not mobile and not email:
        raise RowError("mobile/email", "no contact channel — credentials cannot be delivered")

    program = await _resolve_program(session, program_code, scope_paths)
    section = await org_service.find_section(session, program.id, section_label, term_code)
    if section is None:
        raise RowError(
            "section_label",
            f"no Section '{section_label}' for term {term_code} — Timetable Cell must create it",
        )

    # SIF is the join key at admission (the only id that exists then); once an
    # enrollment number is issued either id resolves the same student, so a file
    # carrying enrollment_id still matches (canonical-id decision, 28-07-2026).
    existing = await user_service.get_by_sif_id(session, sif_id)
    if existing is None and enrollment_id:
        existing = await user_service.get_by_enrollment_id(session, enrollment_id)
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
        session.add(
            StudentProfile(
                user_id=user.id,
                program_id=program.id,
                roll_number=roll_number,
                admission_year=admission_year,
                date_of_birth=dob,
                gender=row.get("gender") or None,
            )
        )
        if enrollment_id:
            await user_service.set_enrollment_id(session, ctx, user, enrollment_id)
        await session.flush()
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
        if profile.program_id != program.id:
            profile.program_id = program.id
            changed = risky = True
        if dob and profile.date_of_birth != dob:
            profile.date_of_birth = dob
            changed = risky = True
        if profile.roll_number != roll_number:
            profile.roll_number = roll_number
            changed = True
    if enrollment_id and await user_service.set_enrollment_id(
        session, ctx, existing, enrollment_id
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
    session: AsyncSession, ctx: AuthContext, batch_id: uuid.UUID
) -> dict[str, int]:
    """Activation pipeline: credential generation → delivery → ACTIVE (ONB-FR-06)."""
    batch = await dao.get_batch(session, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if batch.status == "needs-review":
        raise HTTPException(
            status_code=409, detail="Batch is held for review — confirm it before delivery."
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
    session: AsyncSession, section_id: uuid.UUID, as_of: date | None = None
) -> list[dict[str, object]]:
    memberships = await dao.section_roster_as_of(session, section_id, as_of or date.today())
    roster: list[dict[str, object]] = []
    for m in memberships:
        user = await user_service.get_user(session, m.user_id)
        profile = await dao.get_profile(session, m.user_id)
        roster.append(
            {
                "user_id": str(user.id),
                "sif_id": user.sif_id,
                "enrollment_id": user.enrollment_id,
                "full_name": user.full_name,
                "status": user.status,
                "roll_number": profile.roll_number if profile else None,
                "credential_delivery": profile.credential_delivery if profile else None,
            }
        )
    return roster


async def list_batches(session: AsyncSession, limit: int) -> list[ImportBatch]:
    return list(await dao.list_batches(session, limit))


async def batch_errors(session: AsyncSession, batch_id: uuid.UUID) -> list[ImportRowError]:
    return list(await dao.batch_errors(session, batch_id))


async def confirm_batch(
    session: AsyncSession, ctx: AuthContext, batch_id: uuid.UUID
) -> ImportBatch:
    """System Admin confirmation for a batch parked by the risky-change guardrail."""
    batch = await dao.get_batch(session, batch_id)
    if batch is None or batch.status != "needs-review":
        raise HTTPException(status_code=404, detail="No batch awaiting review.")
    batch.status = "committed"
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="onb.import.confirmed",
        object_type="import_batch",
        object_id=str(batch.id),
        after={"status": "committed"},
    )
    await session.commit()
    return batch


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
    "batch_errors",
    "confirm_batch",
    "deliver_credentials",
    "error_report_csv",
    "import_csv",
    "list_batches",
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

    body = "\n".join(
        line for line in text_content.splitlines() if not line.lstrip().startswith("#")
    )
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
