# ============================================================
# PaySentinelIQ — Notification Handler Tests
# ============================================================
# The sentinel.notifications consumer must create in-app
# notifications (reusing NotificationModel) exactly once per event.

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.messaging.application.handlers.notifications import NotificationEventHandler
from app.messaging.domain.envelope import EventEnvelope, new_event
from app.messaging.domain.event_types import EventType
from app.messaging.domain.ports import PermanentProcessingError
from app.messaging.infrastructure.null_publisher import InMemoryEventPublisher
from app.shared.orm_models import NotificationModel, ProcessedEventModel


@pytest_asyncio.fixture(autouse=True)
async def _isolate(session_factory, monkeypatch):
    """Isolate tests (shared SQLite) + avoid real Redis pushes."""
    async with session_factory() as session:
        await session.execute(delete(ProcessedEventModel))
        await session.execute(delete(NotificationModel))
        await session.commit()

    # The notification handler pushes to the WS Redis bridge — a no-op
    # in unit tests (no Redis in the test environment).
    async def _noop_push(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.websocket.router.publish_via_redis", _noop_push
    )
    yield


def _due_soon_event() -> EventEnvelope:
    return new_event(
        EventType.BILL_DUE_SOON.value,
        payload={
            "bill_id": str(uuid.uuid4()),
            "due_date": "2026-08-29T00:00:00+00:00",
            "amount": 250.0,
            "beneficiary": "Energia Elétrica",
            "days_until_due": 2,
            "reason": "Vence em 2 dia(s)",
        },
        user_id="00000000-0000-0000-0000-00000000cafe",
        tenant_id="00000000-0000-0000-0000-00000000feed",
    )


@pytest.mark.asyncio
async def test_creates_notification(session_factory):
    publisher = InMemoryEventPublisher()
    handler = NotificationEventHandler(session_factory, publisher)
    event = _due_soon_event()

    await handler(event)

    async with session_factory() as session:
        rows = (await session.execute(select(NotificationModel))).scalars().all()
        matching = [
            r for r in rows if (r.metadata_ or {}).get("event_id") == event.event_id
        ]
        assert len(matching) == 1
        row = matching[0]
        assert row.type == "payment"
        assert row.title.startswith("Pagamento pendente")
        assert row.severity == "warning"
        assert row.user_id == uuid.UUID(event.user_id)


@pytest.mark.asyncio
async def test_redelivery_does_not_duplicate(session_factory):
    publisher = InMemoryEventPublisher()
    handler = NotificationEventHandler(session_factory, publisher)
    event = _due_soon_event()

    await handler(event)
    await handler(event)

    async with session_factory() as session:
        count = (
            await session.execute(select(func.count(NotificationModel.id)))
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_publishes_notification_created_for_audit(session_factory):
    publisher = InMemoryEventPublisher()
    handler = NotificationEventHandler(session_factory, publisher)
    event = _due_soon_event()

    await handler(event)

    created = publisher.events_of_type(EventType.NOTIFICATION_CREATED.value)
    assert len(created) == 1
    assert created[0].payload["trigger_event_id"] == event.event_id


@pytest.mark.asyncio
async def test_overdue_uses_critical_severity(session_factory):
    publisher = InMemoryEventPublisher()
    handler = NotificationEventHandler(session_factory, publisher)
    event = new_event(
        EventType.BILL_OVERDUE.value,
        payload={
            "bill_id": str(uuid.uuid4()),
            "due_date": "2026-08-20T00:00:00+00:00",
            "amount": 100.0,
            "beneficiary": "Água",
            "days_until_due": -5,
        },
        user_id="00000000-0000-0000-0000-00000000cafe",
        tenant_id="00000000-0000-0000-0000-00000000feed",
    )

    await handler(event)

    async with session_factory() as session:
        rows = (await session.execute(select(NotificationModel))).scalars().all()
        overdue = [r for r in rows if r.severity == "critical"]
        assert len(overdue) == 1


@pytest.mark.asyncio
async def test_event_without_user_is_permanent_error(session_factory):
    handler = NotificationEventHandler(session_factory, InMemoryEventPublisher())
    event = new_event(EventType.BILL_DUE_SOON.value, payload={"bill_id": "x"})

    with pytest.raises(PermanentProcessingError):
        await handler(event)


@pytest.mark.asyncio
async def test_unrelated_event_is_ignored(session_factory):
    handler = NotificationEventHandler(session_factory, InMemoryEventPublisher())
    event = new_event(
        EventType.REPORT_VIEWED.value,
        payload={"report": "x"},
        user_id="00000000-0000-0000-0000-00000000cafe",
        tenant_id="00000000-0000-0000-0000-00000000feed",
    )

    await handler(event)  # must not raise

    async with session_factory() as session:
        count = (
            await session.execute(select(func.count(NotificationModel.id)))
        ).scalar_one()
        assert count == 0
