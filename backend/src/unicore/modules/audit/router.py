"""HTTP endpoints for the audit module. Read-only by design (AUTH-FR-08):
no update or delete route exists anywhere — immutability is structural."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_session
from unicore.core.security import AuthContext
from unicore.modules.audit import service
from unicore.modules.rbac.service import require_permission

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events")
async def list_events(
    object_type: str | None = None,
    action: str | None = None,
    limit: int = Query(default=50, le=500),
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(require_permission("audit:read")),
) -> list[dict[str, Any]]:
    events = await service.list_events(session, object_type, action, limit)
    return [
        {
            "id": str(e.id),
            "occurred_at": e.occurred_at.isoformat(),
            "actor": e.actor,
            "action": e.action,
            "object_type": e.object_type,
            "object_id": e.object_id,
            "scope": e.scope,
            "before": e.before,
            "after": e.after,
            "reason": e.reason,
        }
        for e in events
    ]
