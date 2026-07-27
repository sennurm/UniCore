"""Business rules for the audit module. The only layer other modules may call.

`record()` writes an audit event in the CALLER'S transaction, so the audit row
commits or rolls back atomically with the business change it documents. The
outbox dispatcher and scoped read API arrive in Phase 4.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unicore.core.db import get_sessionmaker
from unicore.core.logging import get_logger
from unicore.modules.audit.models import AuditEvent, DomainEvent


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


async def list_events(
    session: AsyncSession,
    object_type: str | None,
    action: str | None,
    limit: int,
) -> list[AuditEvent]:
    """Scoped read API (AUTH-FR-08); write path is record() only — never update."""
    from unicore.modules.audit import dao

    return list(await dao.list_events(session, object_type, action, limit))


# --- transactional outbox (Phase 4) ------------------------------------------

Handler = Callable[[dict[str, Any]], Awaitable[None]]
_handlers: dict[str, list[Handler]] = {}


def subscribe(topic: str, handler: Handler) -> None:
    handlers = _handlers.setdefault(topic, [])
    if handler not in handlers:
        handlers.append(handler)


async def publish(session: AsyncSession, topic: str, payload: dict[str, Any]) -> None:
    """Insert into the outbox in the CALLER'S transaction (guaranteed delivery)."""
    session.add(DomainEvent(topic=topic, payload=payload))


async def dispatch_pending(limit: int = 100) -> int:
    """Deliver undispatched events to subscribed handlers (at-least-once).

    Run by a background loop in deployment; callable directly (tests, CLI).
    """
    dispatched = 0
    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(DomainEvent)
            .where(DomainEvent.dispatched_at.is_(None))
            .order_by(DomainEvent.created_at)
            .limit(limit)
        )
        events = result.scalars().all()
        for event in events:
            for handler in _handlers.get(event.topic, []):
                await handler(dict(event.payload))
            event.dispatched_at = datetime.now(UTC)
            dispatched += 1
            get_logger().info("domain event dispatched", topic=event.topic)
        await session.commit()
    return dispatched
