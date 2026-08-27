# ============================================================
# PaySentinelIQ — Email Handler (sentinel.email consumer)
# ============================================================
# Sends transactional emails for events that require delivery —
# currently `bill.due_soon`. Email NEVER runs inside an HTTP request.
#
# Idempotency: (event_id, "sentinel.email") is recorded in
# `processed_events` right after the send succeeds, so a redelivered
# message is skipped. If the send fails, the marker is not committed
# and the message is retried by the consumer (with backoff).
#
# Duplicate-email guard: the scheduler emits a DETERMINISTIC event_id
# per (bill, day), so even a buggy double-publish cannot double-send.
# ============================================================

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.application.dedupe import try_mark_processed
from app.messaging.domain.envelope import EventEnvelope
from app.messaging.domain.event_types import EventType
from app.messaging.domain.ports import PermanentProcessingError, TransientProcessingError
from app.notifications.domain.ports import EmailMessage, EmailService
from app.shared.database import get_session_factory
from app.shared.orm_models import UserModel, UserSettingsModel
from app.shared.settings import get_settings

logger = logging.getLogger(__name__)

EMAIL_CONSUMER = "sentinel.email"


class EmailEventHandler:
    """Consumer handler for the `sentinel.email` queue."""

    def __init__(self, session_factory=None, email_service: EmailService | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._email_service = email_service

    async def __call__(self, event: EventEnvelope) -> None:
        if event.event_type != EventType.BILL_DUE_SOON.value:
            logger.info(
                "event_not_handled",
                extra={"queue": EMAIL_CONSUMER, "event_type": event.event_type,
                       "event_id": event.event_id},
            )
            return

        # ── Phase 1: dedupe check + recipient data (short-lived session) ──
        recipient: dict | None
        async with self._session_factory() as session:
            if await self._already_processed(session, event.event_id):
                logger.info(
                    "email_duplicate_skipped",
                    extra={"event_id": event.event_id,
                           "event_type": event.event_type},
                )
                return
            recipient = await self._resolve_recipient(session, event)
            await session.commit()

        if recipient is None:
            # Email channel disabled for this user → record and move on.
            await self._mark_processed(event)
            return

        # ── Phase 2: deliver (NO DB transaction held open during I/O) ──
        await self._send(event, recipient)

        # ── Phase 3: record idempotency AFTER successful delivery ──
        await self._mark_processed(event)

    # ── Internals ────────────────────────────────────────────

    async def _already_processed(self, session: AsyncSession, event_id: str) -> bool:
        from app.shared.orm_models import ProcessedEventModel

        result = await session.execute(
            select(ProcessedEventModel.id).where(
                ProcessedEventModel.event_id == event_id,
                ProcessedEventModel.consumer == EMAIL_CONSUMER,
            )
        )
        return result.scalar_one_or_none() is not None

    async def _resolve_recipient(
        self, session: AsyncSession, event: EventEnvelope
    ) -> dict | None:
        """Load user + preferences. Returns None when email is disabled."""
        if not event.user_id:
            raise PermanentProcessingError("bill.due_soon without user_id")
        try:
            uid = uuid.UUID(event.user_id)
        except ValueError as exc:
            raise PermanentProcessingError(f"invalid user_id: {event.user_id}") from exc

        user = await session.get(UserModel, uid)
        if user is None or not user.is_active:
            raise PermanentProcessingError(f"user {uid} not found/inactive")

        settings_result = await session.execute(
            select(UserSettingsModel).where(UserSettingsModel.user_id == uid)
        )
        user_settings = settings_result.scalar_one_or_none()
        if user_settings is not None and user_settings.email_alerts is False:
            logger.info(
                "email_channel_disabled",
                extra={"user_id": str(uid), "event_id": event.event_id},
            )
            return None

        return {
            "email": user.email,
            "full_name": user.full_name,
            "user_id": str(uid),
        }

    async def _send(self, event: EventEnvelope, recipient: dict) -> None:
        from app.notifications.infrastructure.email_service import get_email_service
        from app.notifications.infrastructure.email_templates import (
            render_bill_due_soon_email,
        )

        service = self._email_service or get_email_service()
        payload = event.payload
        settings = get_settings()

        days = int(payload.get("days_until_due") or 0)
        subject, text_body, html_body = render_bill_due_soon_email(
            {
                "user_name": recipient["full_name"],
                "beneficiary": payload.get("beneficiary"),
                "amount": payload.get("amount"),
                "due_date": payload.get("due_date"),
                "days_until_due": days,
                "bill_id": payload.get("bill_id"),
                "app_link": f"{settings.APP_BASE_URL}/payroll",
            },
            base_url=settings.APP_BASE_URL,
        )

        try:
            await service.send(
                EmailMessage(
                    to=recipient["email"],
                    subject=subject,
                    body_text=text_body,
                    body_html=html_body,
                )
            )
        except Exception as exc:
            raise TransientProcessingError(f"Email delivery failed: {exc}") from exc

        logger.info(
            "email_dispatched",
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "user_id": recipient["user_id"],
                "correlation_id": event.correlation_id,
                "success": True,
            },
        )

    async def _mark_processed(self, event: EventEnvelope) -> None:
        """Record idempotency (committed only after delivery succeeded)."""
        async with self._session_factory() as session:
            try:
                await try_mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer=EMAIL_CONSUMER,
                    event_type=event.event_type,
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.warning(
                    "email_marker_conflict",
                    extra={"event_id": event.event_id,
                           "event_type": event.event_type},
                )
