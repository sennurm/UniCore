"""Business rules for the audit module. The only layer other modules may call.

`record()` writes an audit event in the CALLER'S transaction, so the audit row
commits or rolls back atomically with the business change it documents. The
outbox dispatcher and scoped read API arrive in Phase 4.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.logging import get_logger
from unicore.modules.audit.models import AuditEvent


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    scope: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            scope=scope,
            before=before,
            after=after,
            reason=reason,
        )
    )
    get_logger().info("audit event recorded", action=action, object_type=object_type)
