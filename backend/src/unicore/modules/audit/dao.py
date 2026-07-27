"""Data access for the audit module. All SQLAlchemy queries for its tables live here."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.modules.audit.models import AuditEvent


async def list_events(
    session: AsyncSession,
    object_type: str | None,
    action: str | None,
    limit: int,
) -> Sequence[AuditEvent]:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if object_type:
        query = query.where(AuditEvent.object_type == object_type)
    if action:
        query = query.where(AuditEvent.action == action)
    result = await session.execute(query)
    return result.scalars().all()
