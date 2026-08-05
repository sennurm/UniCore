"""Business rules for the rbac module. The only layer other modules may call.

Implements the grant engine (AUTH-FR-04/05/13-17): scoped, time-bound grants;
singleton + atomic supersede; term-closure revoke/restore; and the DB-backed
`require_permission` dependency every non-public endpoint must declare
(project security rule, 25-07-2026). Permission results come from a per-process
cache with a 60 s TTL (AUTH §4 rule 7), invalidated on any grant write.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_sessionmaker
from unicore.core.logging import get_logger
from unicore.core.security import AuthContext
from unicore.modules.audit import service as audit_service
from unicore.modules.org import service as org_service
from unicore.modules.rbac import dao
from unicore.modules.rbac.models import Grant
from unicore.modules.rbac.schemas import GrantCreate, SupersedeRequest

# Action registry: which roles may perform which action. Org-unit scope is
# checked by the owning service via `ensure_scope_covers` where a target unit
# exists. Unknown actions fail closed.
ACTIONS: dict[str, tuple[str, ...]] = {
    "org:create": ("super-admin",),
    "org:update": ("super-admin",),
    "org:deactivate": ("super-admin",),
    "org:reparent": ("super-admin",),
    "org:read": ("super-admin", "system-admin"),
    # Subjects are academic master data: HoDs own their Department's catalogue,
    # and everyone who builds or teaches a timetable needs to read it.
    "subject:write": ("super-admin", "system-admin", "school-incharge", "hod"),
    "subject:read": ("super-admin", "system-admin", "school-incharge", "hod",
                     "timetable-cell", "office-staff", "student"),
    # Rooms are physical estate, maintained centrally (TTM-FR §4).
    "venue:write": ("super-admin", "system-admin"),
    "venue:read": ("super-admin", "system-admin", "timetable-cell", "school-incharge", "hod"),
    "user:create": ("super-admin", "system-admin"),
    "user:read": ("super-admin", "system-admin"),
    "user:deactivate": ("super-admin", "system-admin"),
    "rbac:grant": ("super-admin", "system-admin", "hod"),
    "rbac:read": ("super-admin", "system-admin"),
    "audit:read": ("super-admin", "system-admin"),
    "device:approve": ("super-admin", "system-admin"),
    "grievance:resolve": ("super-admin", "system-admin"),
    "ttm:term-upload": ("super-admin", "system-admin", "office-staff"),
    "ttm:term-approve": ("super-admin", "school-incharge"),
    # Applying one calendar across Schools is a university-level act: a School's
    # own staff may upload only for their School (ttm:term-upload).
    "ttm:term-upload-multi": ("super-admin", "system-admin", "registrar",
                              "dean-academic-affairs"),
    # Narrow, one-way backfill of a field added after calendars existed; it is the
    # Timetable Cell that is blocked by a missing parity, so they hold it too.
    "ttm:term-set-parity": ("super-admin", "system-admin", "office-staff",
                            "school-incharge", "timetable-cell"),
    "ttm:term-read": ("super-admin", "system-admin", "office-staff", "school-incharge",
                      "timetable-cell", "hod"),
    "ttm:section-create": ("super-admin", "system-admin", "timetable-cell"),
    # Holidays are university master data, alongside venues and campus settings.
    "ttm:holiday-write": ("super-admin", "system-admin"),
    # A School's working week is a School Incharge power, like every other
    # School-scoped decision; the scope check is in the owning service.
    "ttm:calendar-write": ("super-admin", "system-admin", "school-incharge"),
    # Everyone who schedules, teaches, or attends needs to know which days are
    # working — the resolver is read by TTM, ATT, LVE and TSK alike.
    "ttm:calendar-read": ("super-admin", "system-admin", "school-incharge", "timetable-cell",
                          "hod", "office-staff", "faculty", "student"),
    # The Timetable Cell authors; HoDs approve their Department's portion; the
    # Cell publishes. Reading a draft is wider — faculty and students see only
    # published timetables, which is a separate read path.
    "ttm:grid-write": ("super-admin", "system-admin", "timetable-cell"),
    "ttm:draft-write": ("super-admin", "system-admin", "timetable-cell"),
    "ttm:draft-read": ("super-admin", "system-admin", "timetable-cell", "school-incharge",
                       "hod"),
    "ttm:draft-approve": ("super-admin", "system-admin", "hod", "school-incharge"),
    "ttm:draft-publish": ("super-admin", "system-admin", "timetable-cell"),
    "ttm:section-read": ("super-admin", "system-admin", "timetable-cell", "school-incharge",
                         "office-staff", "hod"),
    # CSV upload templates carry column definitions and sample rows, no real data —
    # but the project rule admits no unrolechecked endpoint, so they gate on the
    # roles that actually perform bulk uploads.
    "templates:read": ("super-admin", "system-admin", "office-staff", "timetable-cell",
                       "school-incharge"),
    "onb:import": ("super-admin", "system-admin", "office-staff"),
    # Staff provisioning grants roles, so it stays with the admins who may
    # already issue them rather than with School office staff.
    "onb:staff-import": ("super-admin", "system-admin"),
    "onb:read": ("super-admin", "system-admin", "office-staff", "school-incharge", "hod",
                 "class-incharge"),
    "onb:allot": ("super-admin", "system-admin", "office-staff"),
    "onb:transfer": ("super-admin", "system-admin"),
    "onb:withdraw": ("super-admin", "system-admin", "office-staff"),
    "rbac:bulk-roles": ("super-admin", "system-admin"),
}

# Who may issue a given role (AUTH §4 matrix). Default: admins only.
GRANTERS: dict[str, tuple[str, ...]] = {
    "class-incharge": ("hod", "system-admin", "super-admin"),  # AUTH-FR-15
    # Student grants are issued by the import that provisions the student, and
    # imports are run by School office staff within their own scope.
    "student": ("office-staff", "system-admin", "super-admin"),
}
DEFAULT_GRANTERS: tuple[str, ...] = ("system-admin", "super-admin")

_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class GrantView:
    grant_id: uuid.UUID
    role_code: str
    org_unit_id: uuid.UUID | None
    unit_path: str | None
    singleton: bool
    valid_from: datetime | None
    valid_until: datetime | None

    def valid_now(self, now: datetime) -> bool:
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True


_grant_cache: dict[str, tuple[float, list[GrantView]]] = {}


def invalidate_user(user_id: uuid.UUID | str) -> None:
    _grant_cache.pop(str(user_id), None)


async def _load_grants(user_id: str) -> list[GrantView]:
    cached = _grant_cache.get(user_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    async with get_sessionmaker()() as session:
        rows = await dao.active_grants_for_user(session, uuid.UUID(user_id))
        unit_ids = [g.org_unit_id for g, _ in rows if g.org_unit_id is not None]
        paths = await org_service.get_unit_paths(session, unit_ids)
    views = [
        GrantView(
            grant_id=g.id,
            role_code=g.role_code,
            org_unit_id=g.org_unit_id,
            unit_path=paths.get(g.org_unit_id) if g.org_unit_id else None,
            singleton=r.singleton,
            valid_from=g.valid_from,
            valid_until=g.valid_until,
        )
        for g, r in rows
    ]
    _grant_cache[user_id] = (time.monotonic() + _CACHE_TTL_SECONDS, views)
    return views


async def effective_grants(ctx: AuthContext) -> list[GrantView]:
    """The caller's active grants, validity-evaluated at check time (AUTH RBAC rule)."""
    now = datetime.now(UTC)
    return [g for g in await _load_grants(ctx.user_id) if g.valid_now(now)]


async def check_permission(request: Request, action: str) -> AuthContext:
    """Assert the authenticated caller may perform `action`. Fails closed."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None:  # gate bypassed somehow — refuse rather than trust
        raise HTTPException(status_code=401, detail="Unauthenticated.")
    allowed = ACTIONS.get(action)
    if allowed is None:
        raise HTTPException(status_code=403, detail=f"Unknown action: {action}.")
    matched = [g for g in await effective_grants(ctx) if g.role_code in allowed]
    if not matched:
        get_logger().warning("permission denied", action=action)
        raise HTTPException(status_code=403, detail=f"Not permitted: {action}.")
    for g in matched:
        if g.singleton and not await _singleton_intact(g):
            get_logger().error(
                "singleton anomaly: multiple active holders — failing closed",
                role=g.role_code,
            )
            raise HTTPException(status_code=403, detail="Role state anomaly; access denied.")
    return ctx


def require_permission(action: str):  # noqa: ANN201 — returns a FastAPI dependency
    """Dependency factory over `check_permission`, declared per endpoint."""

    async def dependency(request: Request) -> AuthContext:
        return await check_permission(request, action)

    return dependency


async def _singleton_intact(grant: GrantView) -> bool:
    """AUTH-FR-16: a data-level singleton anomaly fails closed at check time."""
    async with get_sessionmaker()() as session:
        holders = await dao.active_grants_for_role_unit(
            session, grant.role_code, grant.org_unit_id
        )
    return len(holders) == 1


async def scope_paths_for(ctx: AuthContext, roles: tuple[str, ...]) -> list[str] | None:
    """ltree paths the caller's matching grants cover. None = university-wide."""
    paths: list[str] = []
    for g in await effective_grants(ctx):
        if g.role_code not in roles:
            continue
        if g.org_unit_id is None:
            return None  # university scope covers everything
        if g.unit_path:
            paths.append(g.unit_path)
    return paths


async def ensure_scope_covers(
    session: AsyncSession,
    ctx: AuthContext,
    granter_roles: tuple[str, ...],
    target_unit_id: uuid.UUID | None,
) -> None:
    """The actor must hold one of `granter_roles` whose scope covers the target unit."""
    target_path: str | None = None
    if target_unit_id is not None:
        target_path = (await org_service.get_unit(session, target_unit_id)).path
    for g in await effective_grants(ctx):
        if g.role_code not in granter_roles:
            continue
        if g.org_unit_id is None:  # university-wide scope covers everything
            return
        if target_path is not None and (
            target_path == g.unit_path or target_path.startswith(f"{g.unit_path}.")
        ):
            return
    raise HTTPException(status_code=403, detail="Target is outside your scope.")


async def list_roles(session: AsyncSession) -> list[dict[str, object]]:
    """The role registry, so callers stop hardcoding it (the Users & roles screen
    carried a literal list that had already drifted out of date)."""
    return [
        {
            "code": r.code,
            "name": r.name,
            "unit_type": r.unit_type,
            "singleton": r.singleton,
            "term_bound": r.term_bound,
        }
        for r in await dao.list_roles(session)
    ]


async def ensure_sole_grant(
    session: AsyncSession,
    ctx: AuthContext,
    user_id: uuid.UUID,
    role_code: str,
    org_unit_id: uuid.UUID,
    reason: str,
) -> bool:
    """Make `org_unit_id` the *only* unit at which the user holds `role_code`.

    Grants it if missing, and revokes the same role on any other unit. Returns
    True if anything changed, so callers can report it as a real diff.

    Used for the `student` role, which is one-Programme-at-a-time: a transfer
    must move the grant, not add a second one. Idempotent, so student import can
    call it on every row — an intake of 15,000 cannot be granted by hand, and a
    re-import must not pile up duplicates.
    """
    changed = False
    held_here = False
    for grant, _role in await dao.active_grants_for_user(session, user_id):
        if grant.role_code != role_code:
            continue
        if grant.org_unit_id == org_unit_id:
            held_here = True
        else:
            await revoke_grant(session, ctx, grant.id, reason)
            changed = True
    if not held_here:
        await create_grant(
            session,
            ctx,
            GrantCreate(user_id=user_id, role_code=role_code, org_unit_id=org_unit_id),
        )
        changed = True
    return changed


async def create_grant(session: AsyncSession, ctx: AuthContext, data: GrantCreate) -> Grant:
    role = await dao.get_role(session, data.role_code)
    if role is None:
        raise HTTPException(status_code=422, detail=f"Unknown role '{data.role_code}'.")

    if role.unit_type == "university":
        if data.org_unit_id is not None:
            raise HTTPException(
                status_code=422, detail="University-scope roles are granted without an org unit."
            )
    else:
        if data.org_unit_id is None:
            raise HTTPException(
                status_code=422, detail=f"Role '{role.code}' requires a {role.unit_type} unit."
            )
        unit = await org_service.get_unit(session, data.org_unit_id)
        if unit.type != role.unit_type:
            raise HTTPException(
                status_code=422,
                detail=f"Role '{role.code}' binds to a {role.unit_type}, not a {unit.type}.",
            )
        if unit.status != "active":
            raise HTTPException(status_code=409, detail="Cannot grant on a deactivated unit.")
    if role.term_bound and not data.term_code:
        raise HTTPException(
            status_code=422, detail=f"Role '{role.code}' is term-bound: term_code is required."
        )

    granters = GRANTERS.get(role.code, DEFAULT_GRANTERS)
    try:
        await ensure_scope_covers(session, ctx, granters, data.org_unit_id)
    except HTTPException:
        await audit_service.record(
            session,
            actor=ctx.user_id,
            action="rbac.grant.denied",
            object_type="grant",
            object_id=f"{data.role_code}@{data.org_unit_id}",
            reason="actor scope does not cover target unit",
        )
        await session.commit()  # the denial audit must survive the 403
        raise

    if role.singleton:
        holders = await dao.active_grants_for_role_unit(session, role.code, data.org_unit_id)
        if holders:
            raise HTTPException(
                status_code=409,
                detail=f"'{role.code}' already has an active holder here — "
                "use the supersede flow (one active holder per unit).",
            )

    grant = _new_grant(data, granted_by=ctx.user_id)
    session.add(grant)
    try:
        await session.flush()
    except IntegrityError:  # singleton race lost at the partial unique index
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"'{role.code}' already has an active holder here — use the supersede flow.",
        ) from None
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="rbac.grant.issued",
        object_type="grant",
        object_id=str(grant.id),
        scope=str(data.org_unit_id) if data.org_unit_id else "university",
        after=_snapshot(grant),
    )
    await session.commit()
    invalidate_user(data.user_id)
    return grant


async def revoke_grant(
    session: AsyncSession, ctx: AuthContext, grant_id: uuid.UUID, reason: str
) -> Grant:
    grant = await dao.get_grant(session, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found.")
    granters = GRANTERS.get(grant.role_code, DEFAULT_GRANTERS)
    await ensure_scope_covers(session, ctx, granters, grant.org_unit_id)
    if grant.status == "revoked":
        return grant
    _revoke(grant, cause="manual")
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="rbac.grant.revoked",
        object_type="grant",
        object_id=str(grant.id),
        after=_snapshot(grant),
        reason=reason,
    )
    await session.commit()
    invalidate_user(grant.user_id)
    return grant


async def supersede(session: AsyncSession, ctx: AuthContext, data: SupersedeRequest) -> Grant:
    """Atomic holder replacement (AUTH-FR-16/17): revoke + issue in ONE transaction."""
    role = await dao.get_role(session, data.role_code)
    if role is None or not role.singleton:
        raise HTTPException(status_code=422, detail="Supersede applies to singleton roles only.")
    granters = GRANTERS.get(role.code, DEFAULT_GRANTERS)
    await ensure_scope_covers(session, ctx, granters, data.org_unit_id)

    holders = await dao.active_grants_for_role_unit(session, role.code, data.org_unit_id)
    if not holders:
        raise HTTPException(
            status_code=404, detail="No active holder to supersede — issue a normal grant."
        )
    old = holders[0]
    _revoke(old, cause="superseded")
    await session.flush()  # revoke must land before the insert (singleton index)
    new = _new_grant(
        GrantCreate(
            user_id=data.new_user_id,
            role_code=data.role_code,
            org_unit_id=data.org_unit_id,
            term_code=data.term_code or old.term_code,
        ),
        granted_by=ctx.user_id,
    )
    session.add(new)
    await session.flush()
    await audit_service.record(
        session,
        actor=ctx.user_id,
        action="rbac.grant.superseded",
        object_type="grant",
        object_id=str(new.id),
        before=_snapshot(old),
        after=_snapshot(new),
    )
    await session.commit()  # one atomic transition: never 0, never 2 holders
    invalidate_user(old.user_id)
    invalidate_user(new.user_id)
    return new


async def handle_term_closure(
    session: AsyncSession, term_code: str, section_unit_ids: list[uuid.UUID]
) -> int:
    """PRM term-closure event: revoke term-bound grants on the closed Sections (AUTH-FR-13)."""
    grants = await dao.term_bound_grants_on_units(
        session, section_unit_ids, status="active", revoke_cause=None
    )
    for grant in grants:
        _revoke(grant, cause="term-closure")
        await audit_service.record(
            session,
            actor="system:prm-term-closure",
            action="rbac.grant.revoked",
            object_type="grant",
            object_id=str(grant.id),
            after=_snapshot(grant),
            reason=f"term closure {term_code}",
        )
        invalidate_user(grant.user_id)
    await session.commit()
    return len(grants)


async def handle_term_rollback(
    session: AsyncSession, term_code: str, section_unit_ids: list[uuid.UUID]
) -> int:
    """PRM in-window rollback: restore grants the closure revoked (AUTH-FR-14)."""
    grants = await dao.term_bound_grants_on_units(
        session, section_unit_ids, status="revoked", revoke_cause="term-closure"
    )
    restored = 0
    for grant in grants:
        if grant.term_code != term_code:
            continue
        occupied = await dao.active_grants_for_role_unit(
            session, grant.role_code, grant.org_unit_id
        )
        if occupied:
            get_logger().warning(
                "rollback restore skipped: unit already has an active holder",
                role=grant.role_code,
                org_unit_id=str(grant.org_unit_id),
            )
            continue
        grant.status = "active"
        grant.revoke_cause = None
        grant.revoked_at = None
        await audit_service.record(
            session,
            actor="system:prm-rollback",
            action="rbac.grant.restored",
            object_type="grant",
            object_id=str(grant.id),
            after=_snapshot(grant),
            reason=f"term rollback {term_code}",
        )
        invalidate_user(grant.user_id)
        restored += 1
    await session.commit()
    return restored


async def list_user_grants(
    session: AsyncSession, user_id: uuid.UUID
) -> list[Grant]:
    return list(await dao.grants_for_user(session, user_id))


def _new_grant(data: GrantCreate, granted_by: str) -> Grant:
    return Grant(
        user_id=data.user_id,
        role_code=data.role_code,
        org_unit_id=data.org_unit_id,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        term_code=data.term_code,
        additional_charge=data.additional_charge,
        granted_by=granted_by,
    )


def _revoke(grant: Grant, cause: str) -> None:
    grant.status = "revoked"
    grant.revoke_cause = cause
    grant.revoked_at = datetime.now(UTC)


def _snapshot(grant: Grant) -> dict[str, str | None]:
    return {
        "user_id": str(grant.user_id),
        "role_code": grant.role_code,
        "org_unit_id": str(grant.org_unit_id) if grant.org_unit_id else None,
        "status": grant.status,
        "term_code": grant.term_code,
        "revoke_cause": grant.revoke_cause,
    }


# --- domain-event subscriptions (Phase 4 outbox) ------------------------------


def register_event_handlers() -> None:
    """Idempotent; called at app startup. PRM publishes these topics for real
    in its milestone — the handlers are live from now on."""
    from unicore.modules.audit import service as audit_svc

    audit_svc.subscribe("prm.term-closure", _on_term_closure)
    audit_svc.subscribe("prm.term-rollback", _on_term_rollback)


async def _on_term_closure(payload: dict) -> None:
    async with get_sessionmaker()() as session:
        await handle_term_closure(
            session,
            payload["term_code"],
            [uuid.UUID(x) for x in payload["section_unit_ids"]],
        )


async def _on_term_rollback(payload: dict) -> None:
    async with get_sessionmaker()() as session:
        await handle_term_rollback(
            session,
            payload["term_code"],
            [uuid.UUID(x) for x in payload["section_unit_ids"]],
        )


# --- reporting-chain resolution (AUTH-FR-18) ----------------------------------


async def resolve_reporting(session: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """For each of the user's active grants: the next reporting role, its
    holder(s) at the nearest covering scope, and vacancy status — consumed by
    LVE routing/cascade and TSK escalation."""
    rows = await dao.active_grants_for_user(session, user_id)
    unit_ids = [g.org_unit_id for g, _ in rows if g.org_unit_id is not None]
    paths = await org_service.get_unit_paths(session, unit_ids)

    results: list[dict] = []
    for grant, _role in rows:
        edge = await dao.reporting_edge(session, grant.role_code)
        if edge is None:
            results.append(
                {"role": grant.role_code, "reports_to": None, "status": "terminal", "holders": []}
            )
            continue
        grant_path = paths.get(grant.org_unit_id) if grant.org_unit_id else None
        holder_grants = await dao.active_grants_by_role(session, edge.to_role)
        holder_unit_ids = [g.org_unit_id for g in holder_grants if g.org_unit_id is not None]
        holder_paths = await org_service.get_unit_paths(session, holder_unit_ids)
        holders = []
        for hg in holder_grants:
            hp = holder_paths.get(hg.org_unit_id) if hg.org_unit_id else None
            covers = hp is None or (
                grant_path is not None
                and (grant_path == hp or grant_path.startswith(f"{hp}."))
            )
            if covers:
                holders.append(str(hg.user_id))
        results.append(
            {
                "role": grant.role_code,
                "reports_to": edge.to_role,
                "status": "active" if holders else "vacant",
                "holders": holders,
            }
        )
    return results


# --- user + role directory (combined Users & roles screen) -------------------

ROLES_CSV_COLUMNS = ("username", "full_name", "kind", "status", "roles")
_ROLE_SEPARATOR = ";"


def _format_grant(role_code: str, unit_path: str | None) -> str:
    """`role@unit.path`, or bare `role` for university-scope grants."""
    return f"{role_code}@{unit_path}" if unit_path else role_code


def parse_role_spec(spec: str) -> tuple[str, str | None]:
    role_code, _, unit_path = spec.partition("@")
    return role_code.strip(), (unit_path.strip() or None)


async def directory(
    session: AsyncSession,
    *,
    search: str | None = None,
    role_code: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, object]]:
    """Users with their active grants — the combined Users & roles table."""
    from unicore.modules.user import service as user_service

    users = await user_service.list_users(session, search, status, limit)
    rows: list[dict[str, object]] = []
    for user in users:
        grants = await dao.active_grants_for_user(session, user.id)
        unit_ids = [g.org_unit_id for g, _ in grants if g.org_unit_id is not None]
        paths = await org_service.get_unit_paths(session, unit_ids)
        entries = [
            {
                "grant_id": str(g.id),
                "role_code": g.role_code,
                "org_unit_id": str(g.org_unit_id) if g.org_unit_id else None,
                "unit_path": paths.get(g.org_unit_id) if g.org_unit_id else None,
                "spec": _format_grant(
                    g.role_code, paths.get(g.org_unit_id) if g.org_unit_id else None
                ),
            }
            for g, _ in grants
        ]
        if role_code and not any(e["role_code"] == role_code for e in entries):
            continue
        rows.append(
            {
                "user_id": str(user.id),
                "username": user.username,
                "full_name": user.full_name,
                "kind": user.kind,
                "status": user.status,
                "sif_id": user.sif_id,
                "enrollment_id": user.enrollment_id,
                "roles": entries,
            }
        )
    return rows


def directory_csv(rows: list[dict[str, object]]) -> str:
    """Round-trip export: edit the roles column and upload the same file back."""
    import csv as _csv
    import io as _io

    buffer = _io.StringIO()
    buffer.write(
        "# Edit the 'roles' column and upload this file back to apply role changes.\n"
    )
    buffer.write(
        "# roles is a ';'-separated list of role@org_unit_path "
        "(e.g. hod@uni.fet.soce.cse); university-scope roles need no @path.\n"
    )
    buffer.write(
        "# Removing a role from the list REVOKES it; adding one ISSUES it. "
        "username identifies the row and must not be changed.\n"
    )
    writer = _csv.writer(buffer)
    writer.writerow(ROLES_CSV_COLUMNS)
    for row in rows:
        entries = cast(list[dict[str, object]], row["roles"])
        specs = _ROLE_SEPARATOR.join(str(entry["spec"]) for entry in entries)
        writer.writerow(
            [row["username"], row["full_name"], row["kind"], row["status"], specs]
        )
    return buffer.getvalue()


async def apply_roles_csv(
    session: AsyncSession, ctx: AuthContext, content: bytes
) -> dict[str, object]:
    """Diff each row's roles against the user's current grants: issue what was
    added, revoke what was removed. Every change goes through the same singleton,
    scope and audit rules as a single grant — no bulk bypass."""
    import csv as _csv
    import io as _io

    from unicore.modules.user import service as user_service

    if not content:
        raise HTTPException(status_code=422, detail="File is empty.")
    try:
        text_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded.") from None

    body = "\n".join(
        line for line in text_content.splitlines() if not line.lstrip().startswith("#")
    )
    reader = _csv.DictReader(_io.StringIO(body))
    missing = {"username", "roles"} - set(h.strip() for h in (reader.fieldnames or []))
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Header must include: {', '.join(sorted(missing))}.",
        )

    granted = revoked = unchanged = 0
    errors: list[dict[str, object]] = []

    for row_number, raw in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        username = row.get("username", "")
        if not username:
            continue
        try:
            user = await user_service.get_by_username(session, username)
            if user is None:
                raise ValueError(f"no user '{username}'")
            desired = {
                spec.strip() for spec in row.get("roles", "").split(_ROLE_SEPARATOR) if spec.strip()
            }
            current_grants = await dao.active_grants_for_user(session, user.id)
            unit_ids = [g.org_unit_id for g, _ in current_grants if g.org_unit_id is not None]
            paths = await org_service.get_unit_paths(session, unit_ids)
            current = {
                _format_grant(g.role_code, paths.get(g.org_unit_id) if g.org_unit_id else None): g
                for g, _ in current_grants
            }

            for spec in desired - set(current):
                role_code, unit_path = parse_role_spec(spec)
                org_unit_id = None
                if unit_path:
                    unit = await org_service.get_unit_by_path(session, unit_path)
                    if unit is None:
                        raise ValueError(f"no org unit at path '{unit_path}'")
                    org_unit_id = unit.id
                await create_grant(
                    session,
                    ctx,
                    GrantCreate(user_id=user.id, role_code=role_code, org_unit_id=org_unit_id),
                )
                granted += 1
            for spec in set(current) - desired:
                await revoke_grant(
                    session, ctx, current[spec].id, reason="bulk role update (CSV)"
                )
                revoked += 1
            if desired == set(current):
                unchanged += 1
        except (ValueError, HTTPException) as err:
            detail = err.detail if isinstance(err, HTTPException) else str(err)
            errors.append(
                {"row_number": row_number, "username": username, "reason": str(detail)}
            )

    return {
        "grants_issued": granted,
        "grants_revoked": revoked,
        "rows_unchanged": unchanged,
        "rows_rejected": len(errors),
        "errors": errors,
    }
