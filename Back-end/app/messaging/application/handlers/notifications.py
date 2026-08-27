# ============================================================
# PaySentinelIQ — Notification Handler (sentinel.notifications)
# ============================================================
# Creates in-app notifications (NotificationModel) for events such as
# `bill.due_soon` / `bill.overdue` and pushes them to connected
# browsers through the existing Redis Pub/Sub → WebSocket bridge.
#
# This is a worker for the EXISTING Notification Center — no second
# notification system is created. WhatsApp/Telegram/Slack can be
# added later as additional channels inside this same handler.
# ============================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.application.dedupe import try_mark_processed
from app.messaging.domain.envelope import EventEnvelope
from app.messaging.domain.event_types import EventType
from app.messaging.domain.ports import PermanentProcessingError, TransientProcessingError
from app.shared.database import get_session_factory
from app.shared.orm_models import NotificationModel

logger = logging.getLogger(__name__)

NOTIFICATIONS_CONSUMER = "sentinel.notifications"

_HANDLED_TYPES = frozenset({EventType.BILL_DUE_SOON.value, EventType.BILL_OVERDUE.value})


def _build_notification(event: EventEnvelope) -> dict[str, Any]:
    """Render the in-app notification content for a bill event."""
    payload = event.payload
    beneficiary = str(payload.get("beneficiary") or "beneficiário desconhecido")
    amount = float(payload.get("amount") or 0)
    due_date = payload.get("due_date")
    days = int(payload.get("days_until_due") or 0)
    reason = str(payload.get("reason") or "")

    if event.event_type == EventType.BILL_OVERDUE.value:
        title = f"Boleto vencido — {beneficiary}"
        message = (
            f"Boleto de {beneficiary} no valor de R$ {amount:,.2f} está vencido. "
            f"Vencimento: {_fmt_date(due_date)}."
        )
        severity = "critical"
    else:
        horizon = "hoje" if days == 0 else f"em {days} dia(s)"
        title = f"Pagamento pendente — {beneficiary}" if not reason else f"Pagamento pendente — {reason}"
        message = (
            f"Boleto de {beneficiary} no valor de R$ {amount:,.2f} "
            f"vence {horizon}. Vencimento: {_fmt_date(due_date)}."
        )
        severity = "warning" if days <= 2 else "normal"

    return {
        "type": "payment",
        "title": title,
        "message": message,
        "severity": severity,
        "action_url": "/payroll",
        "metadata": {
            "bill_id": payload.get("bill_id"),
            "due_date": due_date,
            "amount": amount,
            "beneficiary": beneficiary,
            "days_until_due": days,
            "event_id": event.event_id,
        },
    }


def _fmt_date(iso_value: str | None) -> str:
    if not iso_value:
        return "—"
    try:
        return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso_value)


class NotificationEventHandler:
    """Consumer handler for the `sentinel.notifications` queue."""

    def __init__(self, session_factory=None, publisher=None) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._publisher = publisher

    async def __call__(self, event: EventEnvelope) -> None:
        if event.event_type not in _HANDLED_TYPES:
            logger.info(
                "event_not_handled",
                extra={"queue": NOTIFICATIONS_CONSUMER,
                       "event_type": event.event_type,
                       "event_id": event.event_id},
            )
            return

        notification: NotificationModel | None = None
        async with self._session_factory() as session:
            try:
                notification = await self._persist(session, event)
                await session.commit()
            except (PermanentProcessingError, TransientProcessingError):
                await session.rollback()
                raise
            except Exception as exc:
                await session.rollback()
                raise TransientProcessingError(
                    f"Failed to create notification: {exc}"
                ) from exc

        # ── Side effects AFTER the durable commit ─────────────
        if notification is not None:
            await self._push_realtime(event, notification)
            await self._publish_notification_created(event, notification)

    async def _persist(
        self, session: AsyncSession, event: EventEnvelope
    ) -> NotificationModel | None:
        if not await try_mark_processed(
            session,
            event_id=event.event_id,
            consumer=NOTIFICATIONS_CONSUMER,
            event_type=event.event_type,
        ):
            return None

        if not event.user_id or not event.tenant_id:
            raise PermanentProcessingError("bill event without user/tenant — cannot notify")

        try:
            user_id = uuid.UUID(event.user_id)
            tenant_id = uuid.UUID(event.tenant_id)
        except ValueError as exc:
            raise PermanentProcessingError(
                f"Invalid user/tenant id in {event.event_type}"
            ) from exc

        content = _build_notification(event)
        notification = NotificationModel(
            user_id=user_id,
            tenant_id=tenant_id,
            type=content["type"],
            title=content["title"],
            message=content["message"],
            severity=content["severity"],
            action_url=content["action_url"],
            metadata_=content["metadata"],
        )
        session.add(notification)
        await session.flush()
        return notification

    async def _push_realtime(
        self, event: EventEnvelope, notification: NotificationModel
    ) -> None:
        """Relay the notification to browsers via Redis Pub/Sub bridge."""
        try:
            from app.websocket.router import publish_via_redis

            await publish_via_redis(
                {
                    "id": str(notification.id),
                    "type": notification.type,
                    "title": notification.title,
                    "message": notification.message,
                    "severity": notification.severity,
                    "action_url": notification.action_url,
                    "created_at": (
                        notification.created_at.isoformat()
                        if notification.created_at
                        else None
                    ),
                },
                tenant_id=event.tenant_id,
                user_id=event.user_id,
            )
        except Exception as exc:
            logger.warning(
                "ws_push_failed",
                extra={"event_id": event.event_id, "error": str(exc)},
            )

    async def _publish_notification_created(
        self, event: EventEnvelope, notification: NotificationModel
    ) -> None:
        """Feed the audit trail with `notification.created` (non-fatal)."""
        try:
            if self._publisher is None:
                from app.messaging.infrastructure.factory import get_event_publisher

                self._publisher = get_event_publisher()

            from app.messaging.domain.envelope import new_event

            created = new_event(
                EventType.NOTIFICATION_CREATED.value,
                payload={
                    "notification_id": str(notification.id),
                    "notification_type": notification.type,
                    "trigger_event_id": event.event_id,
                },
                user_id=event.user_id,
                tenant_id=event.tenant_id,
                correlation_id=event.correlation_id,
            )
            await self._publisher.publish(created)
        except Exception as exc:
            logger.warning(
                "notification_created_publish_failed",
                extra={"event_id": event.event_id, "error": str(exc)},
            )
