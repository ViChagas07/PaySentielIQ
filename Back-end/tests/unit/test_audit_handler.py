# ============================================================
# PaySentinelIQ — Audit Handler Tests (activity_logs persistence)
# ============================================================
# Verifies that the sentinel.audit consumer persists events to
# `audit_logs` and that redeliveries never duplicate rows.
# No RabbitMQ involved — the handler is driven directly.

import uuid

import pytest
from sqlalchemy import func, select

from app.messaging.application.handlers.audit import AuditEventHandler
from app.messaging.domain.envelope import new_event
from app.messaging.domain.event_types import EventType
from app.messaging.domain.ports import PermanentProcessingError
from app.shared.orm_models import AuditLogModel


def _event(event_type: str = EventType.BILL_SCHEDULED.value, **overrides):
    return new_event(
        event_type,
        payload={"bill_id": str(uuid.uuid4()), "amount": 129.9},
        user_id="00000000-0000-0000-0000-00000000cafe",
        tenant_id="00000000-0000-0000-0000-00000000feed",
        **overrides,
    )


@pytest.mark.asyncio
async def test_persists_audit_log(session_factory):
    handler = AuditEventHandler(session_factory)
    event = _event()

    await handler(event)

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.event_id == event.event_id)
            )
        ).scalar_one()
        assert row.action == EventType.BILL_SCHEDULED.value
        assert row.entity_type == "payment_schedule"
        assert row.entity_id == event.payload["bill_id"]
        assert row.tenant_id == uuid.UUID(event.tenant_id)
        assert row.user_id == uuid.UUID(event.user_id)
        assert row.details["amount"] == 129.9
        assert row.occurred_at is not None


@pytest.mark.asyncio
async def test_redelivery_is_idempotent(session_factory):
    handler = AuditEventHandler(session_factory)
    event = _event()

    await handler(event)
    await handler(event)  # redelivery of the SAME event_id

    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count(AuditLogModel.id)).where(
                    AuditLogModel.event_id == event.event_id
                )
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_user_action_maps_to_user_verb(session_factory):
    handler = AuditEventHandler(session_factory)
    event = new_event(
        EventType.USER_ACTION.value,
        payload={"action": "login", "ip_address": "192.168.1.1"},
        user_id="00000000-0000-0000-0000-00000000cafe",
        tenant_id="00000000-0000-0000-0000-00000000feed",
    )

    await handler(event)

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.event_id == event.event_id)
            )
        ).scalar_one()
        assert row.action == "user.login"
        assert row.ip_address == "192.168.1.1"


@pytest.mark.asyncio
async def test_event_without_tenant_is_permanent_error(session_factory):
    handler = AuditEventHandler(session_factory)
    event = new_event(EventType.REPORT_VIEWED.value, payload={"report": "x"})

    with pytest.raises(PermanentProcessingError):
        await handler(event)
