# ============================================================
# PaySentinelIQ — Bill Due-Soon Scheduler (Use Case)
# ============================================================
# Scans payment_schedules and publishes `bill.due_soon` /
# `bill.overdue` events. The scheduler NEVER sends emails or creates
# notifications directly — those side effects belong to the
# `sentinel.notifications` and `sentinel.email` consumers.
#
# RabbitMQ is NOT used as a scheduler: the scan cadence comes from
# the worker loop (or a Celery beat), the broker only transports the
# resulting events.
#
# Detection is CONFIG-DRIVEN: a bill is "due soon" when its due date
# falls within BILL_DUE_SOON_DAYS. Channel preferences (email_alerts,
# in-app, ...) are honored downstream by the consumers, not here.
#
# Idempotency:
#   - `notified_at` prevents re-publishing the same bill twice a day;
#   - the event_id is DETERMINISTIC per (bill, day), so even if the
#     event is re-published, consumers dedupe it.
# ============================================================

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.domain.envelope import deterministic_event_id, new_event
from app.messaging.domain.event_types import EventType
from app.shared.database import get_session_factory
from app.shared.orm_models import PaymentScheduleModel
from app.shared.settings import get_settings

logger = logging.getLogger(__name__)


class BillDueSoonScheduler:
    """Use case: detect bills near their due date and publish events."""

    def __init__(self, session_factory=None, publisher=None, horizon_days: int | None = None):
        self._session_factory = session_factory or get_session_factory()
        self._publisher = publisher
        self._horizon_days = horizon_days

    async def run_once(self) -> dict[str, Any]:
        """Execute one scan. Returns run statistics for observability."""
        now = datetime.now(UTC)

        stats: dict[str, Any] = {
            "schedules_checked": 0,
            "due_soon_events": 0,
            "overdue_events": 0,
            "skipped_already_notified": 0,
            "publish_failures": 0,
            "timestamp": now.isoformat(),
        }

        async with self._session_factory() as session:
            result = await session.execute(
                select(PaymentScheduleModel).where(
                    PaymentScheduleModel.status == "pending"
                )
            )
            schedules = list(result.scalars().all())

            for schedule in schedules:
                stats["schedules_checked"] += 1
                await self._process_one(session, schedule, now, stats)

            await session.commit()

        logger.info("bill_scheduler_run", extra=stats)
        return stats

    # ── Per-bill logic ───────────────────────────────────────

    async def _process_one(
        self,
        session: AsyncSession,
        schedule: PaymentScheduleModel,
        now: datetime,
        stats: dict[str, Any],
    ) -> None:
        due = schedule.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        # Day-boundary semantics: "due tomorrow" = 1 regardless of the
        # current hour (timedelta.days would truncate 0.9d to 0).
        days_until_due = (due.date() - now.date()).days

        # ── Overdue ──────────────────────────────────────────
        if days_until_due < 0:
            schedule.status = "overdue"
            session.add(schedule)
            if not self._notified_today(schedule, now):
                await self._publish(
                    schedule, EventType.BILL_OVERDUE.value,
                    days_until_due=days_until_due,
                )
                schedule.notified_at = now
                stats["overdue_events"] += 1
            return

        # ── Already notified today (scheduler-level dedupe) ──
        if self._notified_today(schedule, now):
            stats["skipped_already_notified"] += 1
            return

        # ── Config-driven horizon: due soon within N days ────
        horizon = self._horizon_days or get_settings().BILL_DUE_SOON_DAYS
        if days_until_due > horizon:
            return

        reason = "Vence hoje" if days_until_due == 0 else f"Vence em {days_until_due} dia(s)"
        await self._publish(
            schedule,
            EventType.BILL_DUE_SOON.value,
            days_until_due=days_until_due,
            reason=reason,
        )
        schedule.notified_at = now
        session.add(schedule)
        stats["due_soon_events"] += 1

    @staticmethod
    def _notified_today(schedule: PaymentScheduleModel, now: datetime) -> bool:
        return bool(
            schedule.notified_at
            and schedule.notified_at.replace(tzinfo=UTC).date() == now.date()
        )

    async def _publish(
        self,
        schedule: PaymentScheduleModel,
        event_type: str,
        *,
        days_until_due: int,
        reason: str = "",
    ) -> None:
        if self._publisher is None:
            from app.messaging.infrastructure.factory import get_event_publisher

            self._publisher = get_event_publisher()

        due = schedule.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)

        # Deterministic id: same bill + same day → same event id forever.
        event_id = deterministic_event_id(
            "bill", str(schedule.id), due.date().isoformat(), event_type
        )

        event = new_event(
            event_type,
            payload={
                "bill_id": str(schedule.id),
                "due_date": due.isoformat(),
                "amount": schedule.amount,
                "beneficiary": schedule.beneficiary,
                "bank_code": schedule.bank_code,
                "days_until_due": days_until_due,
                "reason": reason,
                "reminder_horizon_days": self._horizon_days,
            },
            user_id=str(schedule.user_id),
            tenant_id=str(schedule.tenant_id),
            event_id=event_id,
        )
        ok = await self._publisher.publish(event)
        if not ok:
            logger.error(
                "bill_event_publish_failed",
                extra={
                    "event_id": event_id,
                    "event_type": event_type,
                    "bill_id": str(schedule.id),
                    "user_id": str(schedule.user_id),
                },
            )
