# ============================================================
# PaySentinelIQ — Consumer Idempotency (Inbox Pattern)
# ============================================================
# ``try_mark_processed`` inserts (event_id, consumer) in the SAME
# transaction as the consumer's business writes. If the message is
# redelivered, the insert violates the unique constraint and the
# handler knows the event was already processed.
# ============================================================

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.orm_models import ProcessedEventModel

logger = logging.getLogger(__name__)


async def try_mark_processed(
    session: AsyncSession,
    *,
    event_id: str,
    consumer: str,
    event_type: str,
) -> bool:
    """Atomically record that ``consumer`` handled ``event_id``.

    Returns:
        True  — first time this consumer sees this event (caller proceeds)
        False — duplicate delivery (caller must skip side effects)

    The record is flushed inside a nested transaction (SAVEPOINT), so a
    duplicate does NOT poison the caller's outer transaction.
    """
    marker = ProcessedEventModel(
        event_id=event_id,
        consumer=consumer,
        event_type=event_type,
    )
    try:
        async with session.begin_nested():
            session.add(marker)
        return True
    except IntegrityError:
        logger.info(
            "Duplicate event delivery ignored: event_id=%s consumer=%s",
            event_id,
            consumer,
        )
        return False
