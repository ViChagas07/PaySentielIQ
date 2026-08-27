# ============================================================
# PaySentinelIQ — Audit Handler (sentinel.audit consumer)
# ============================================================
# Persists meaningful business events into the immutable
# `audit_logs` table. This is the DEFINITIVE activity history —
# RabbitMQ is only the transport, never the store.
#
# Idempotency: (event_id, consumer) is recorded in the same
# transaction as the audit row, so redeliveries never duplicate.
# ============================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.application.dedupe import try_mark_processed
from app.messaging.domain.envelope import EventEnvelope
from app.messaging.domain.event_types import AUDITED_EVENT_TYPES, EventType
from app.messaging.domain.ports import PermanentProcessingError, TransientProcessingError
from app.shared.database import get_session_factory
from app.shared.orm_models import AuditLogModel, UserModel

logger = logging.getLogger(__name__)

AUDIT_CONSUMER = "sentinel.audit"


def _entity_of(event: EventEnvelope) -> tuple[str, str]:
    """Derive (entity_type, entity_id) from the event payload."""
    payload = event.payload
    if event.event_type.startswith("bill."):
        return "payment_schedule", str(payload.get("bill_id") or event.event_id)
    if "analysis" in event.event_type:
        return "document", str(payload.get("document_id") or event.event_id)
    if event.event_type.startswith("notification."):
        return "notification", str(payload.get("notification_id") or event.event_id)
    if event.event_type == EventType.USER_ACTION.value:
        return "user", str(event.user_id or event.event_id)
    if event.event_type == EventType.REPORT_VIEWED.value:
        return "report", str(payload.get("report") or "activity_history")
    return str(payload.get("entity_type") or "unknown"), str(
        payload.get("entity_id") or event.event_id
    )


def _action_of(event: EventEnvelope) -> str:
    """Map an event to the audit `action` string (frontend-compatible)."""
    if event.event_type == EventType.USER_ACTION.value:
        verb = str(event.payload.get("action") or "unknown")
        return f"user.{verb}"
    return event.event_type


class AuditEventHandler:
    """Consumer handler for the `sentinel.audit` queue."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def __call__(self, event: EventEnvelope) -> None:
        if event.event_type not in AUDITED_EVENT_TYPES:
            logger.info(
                "event_not_audited", extra={"event_type": event.event_type,
                                             "event_id": event.event_id}
            )
            return

        async with self._session_factory() as session:
            try:
                await self._persist(session, event)
                await session.commit()
            except IntegrityError:
                # Race/redelivery landed after a concurrent insert.
                await session.rollback()
                logger.info(
                    "audit_duplicate_ignored",
                    extra={"event_id": event.event_id,
                           "event_type": event.event_type},
                )
            except (PermanentProcessingError, TransientProcessingError):
                await session.rollback()
                raise
            except Exception as exc:
                await session.rollback()
                raise TransientProcessingError(
                    f"Failed to persist audit log: {exc}"
                ) from exc

        logger.info(
            "audit_persisted",
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "user_id": event.user_id,
                "correlation_id": event.correlation_id,
            },
        )

    async def _persist(self, session: AsyncSession, event: EventEnvelope) -> None:
        if not await try_mark_processed(
            session,
            event_id=event.event_id,
            consumer=AUDIT_CONSUMER,
            event_type=event.event_type,
        ):
            return  # already processed — no side effects

        user_id, user_name = await self._resolve_user(session, event)
        entity_type, entity_id = _entity_of(event)

        details: dict[str, Any] = dict(event.payload)
        details.update(
            {
                "occurred_at": event.occurred_at,
                "correlation_id": event.correlation_id,
                "source": event.source,
                "event_version": event.version,
            }
        )

        occurred_at = None
        try:
            occurred_at = datetime.fromisoformat(event.occurred_at)
        except (ValueError, TypeError):
            occurred_at = None

        session.add(
            AuditLogModel(
                id=uuid.uuid4(),
                tenant_id=self._uuid_or_none(event.tenant_id, required=True),
                user_id=user_id,
                user_name=user_name,
                action=_action_of(event),
                entity_type=entity_type,
                entity_id=str(entity_id)[:100],
                details=details,
                ip_address=event.payload.get("ip_address"),
                user_agent=event.payload.get("user_agent"),
                event_id=event.event_id,
                occurred_at=occurred_at,
            )
        )

    async def _resolve_user(
        self, session: AsyncSession, event: EventEnvelope
    ) -> tuple[uuid.UUID | None, str]:
        """Look up the actor's id + name (events are idempotent regardless)."""
        if not event.user_id:
            return None, "system"
        try:
            uid = uuid.UUID(event.user_id)
        except ValueError:
            return None, "system"

        result = await session.execute(select(UserModel).where(UserModel.id == uid))
        user = result.scalar_one_or_none()
        if user is None:
            return uid, "unknown"
        return uid, user.full_name

    @staticmethod
    def _uuid_or_none(value: str | None, *, required: bool = False) -> uuid.UUID | None:
        if not value:
            if required:
                raise PermanentProcessingError("Event has no tenant_id — cannot audit")
            return None
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise PermanentProcessingError(
                f"Invalid tenant_id in event: {value}"
            ) from exc
