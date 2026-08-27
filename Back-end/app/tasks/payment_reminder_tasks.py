# ============================================================
# PaySentinelIQ — Payment Reminder Celery Task (Event-Driven)
# ============================================================
# The legacy reminder task now DELEGATES to the BillDueSoonScheduler
# use case: it scans pending payment schedules and PUBLISHES
# `bill.due_soon` / `bill.overdue` events to RabbitMQ.
#
# The scheduler NEVER creates notifications or sends emails directly —
# those side effects belong to the `sentinel.notifications` and
# `sentinel.email` consumers.
#
# NOTE: The canonical trigger is the standalone scheduler worker
# (`python -m app.workers.scheduler`, docker service `scheduler`).
# This Celery task is kept for environments that prefer Celery beat.
# ============================================================

import asyncio
import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    name="check_payment_reminders",
)
def check_payment_reminders(self: Any) -> dict[str, Any]:
    """
    Daily scan that publishes bill.due_soon / bill.overdue events for
    pending payment schedules, honoring each user's reminder
    preferences (see BillDueSoonScheduler).
    """
    logger.info("Starting payment reminder check (event-driven)...")

    try:
        from app.messaging.application.bill_scheduler import BillDueSoonScheduler
        from app.messaging.infrastructure.factory import (
            close_event_publisher,
            get_event_publisher,
        )

        async def _run() -> dict[str, Any]:
            scheduler = BillDueSoonScheduler(publisher=get_event_publisher())
            return await scheduler.run_once()

        loop = asyncio.new_event_loop()
        try:
            stats = loop.run_until_complete(_run())
        finally:
            # Celery tasks spin ephemeral event loops — never leave an
            # AMQP connection bound to a dead loop behind.
            loop.run_until_complete(close_event_publisher())
            loop.close()

        return {
            "status": "completed",
            "schedules_checked": stats["schedules_checked"],
            "due_soon_events": stats["due_soon_events"],
            "overdue_events": stats["overdue_events"],
            "timestamp": stats["timestamp"],
        }

    except Exception as exc:
        logger.error("Payment reminder check failed: %s", exc)
        raise self.retry(exc=exc) from exc
