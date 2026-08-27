# ============================================================
# PaySentinelIQ — Bill Due-Soon Scheduler Tests
# ============================================================
# Covers: identifying bills near the due date, publishing
# `bill.due_soon` / `bill.overdue` with DETERMINISTIC event ids,
# honoring reminder preferences, and the notified-today dedupe.

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.messaging.application.bill_scheduler import BillDueSoonScheduler
from app.messaging.domain.event_types import EventType
from app.messaging.infrastructure.null_publisher import InMemoryEventPublisher
from app.shared.orm_models import PaymentScheduleModel, UserSettingsModel


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(session_factory):
    """Isolate scheduler tests — the session-scoped SQLite is shared."""
    async with session_factory() as session:
        await session.execute(delete(UserSettingsModel))
        await session.execute(delete(PaymentScheduleModel))
        await session.commit()
    yield


async def _seed_schedule(
    session_factory,
    *,
    user_id: str,
    due_in_days: int,
    amount: float = 129.9,
    beneficiary: str = "Internet",
    status: str = "pending",
    notified_at: datetime | None = None,
    reminder_preferences: dict | None = None,
) -> uuid.UUID:
    schedule_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            PaymentScheduleModel(
                id=schedule_id,
                user_id=uuid.UUID(user_id),
                tenant_id=uuid.uuid4(),
                due_date=datetime.now(UTC) + timedelta(days=due_in_days),
                amount=amount,
                beneficiary=beneficiary,
                status=status,
                notified_at=notified_at,
            )
        )
        if reminder_preferences is not None:
            session.add(
                UserSettingsModel(
                    user_id=uuid.UUID(user_id), reminder_preferences=reminder_preferences
                )
            )
        await session.commit()
    return schedule_id


@pytest.mark.asyncio
async def test_publishes_due_soon_for_bill_within_horizon(session_factory):
    publisher = InMemoryEventPublisher()
    scheduler = BillDueSoonScheduler(session_factory, publisher, horizon_days=2)
    bill_id = await _seed_schedule(
        session_factory,
        user_id="00000000-0000-0000-0000-00000000cafe",
        due_in_days=1,
    )

    stats = await scheduler.run_once()

    events = publisher.events_of_type(EventType.BILL_DUE_SOON.value)
    assert stats["due_soon_events"] == 1
    assert len(events) == 1
    event = events[0]
    assert event.payload["bill_id"] == str(bill_id)
    assert event.payload["amount"] == 129.9
    assert event.payload["days_until_due"] == 1
    assert event.user_id == "00000000-0000-0000-0000-00000000cafe"


@pytest.mark.asyncio
async def test_deterministic_event_id_per_bill_and_day(session_factory):
    publisher = InMemoryEventPublisher()
    scheduler = BillDueSoonScheduler(session_factory, publisher, horizon_days=2)
    bill_id = await _seed_schedule(
        session_factory,
        user_id="00000000-0000-0000-0000-00000000cafe",
        due_in_days=1,
    )

    await scheduler.run_once()
    first = publisher.events_of_type(EventType.BILL_DUE_SOON.value)[0]

    # Re-run: notified_at set → no new event.
    await scheduler.run_once()
    assert len(publisher.events_of_type(EventType.BILL_DUE_SOON.value)) == 1

    # Force a re-publish of the same logical occurrence → same event id.
    async with session_factory() as session:
        row = (
            await session.execute(
                select(PaymentScheduleModel).where(PaymentScheduleModel.id == bill_id)
            )
        ).scalar_one()
        row.notified_at = None
        await session.commit()

    await scheduler.run_once()
    second = publisher.events_of_type(EventType.BILL_DUE_SOON.value)[1]
    assert first.event_id == second.event_id


@pytest.mark.asyncio
async def test_marks_and_publishes_overdue(session_factory):
    publisher = InMemoryEventPublisher()
    scheduler = BillDueSoonScheduler(session_factory, publisher, horizon_days=2)
    bill_id = await _seed_schedule(
        session_factory,
        user_id="00000000-0000-0000-0000-00000000cafe",
        due_in_days=-3,
    )

    stats = await scheduler.run_once()

    assert stats["overdue_events"] == 1
    events = publisher.events_of_type(EventType.BILL_OVERDUE.value)
    assert len(events) == 1
    assert events[0].payload["bill_id"] == str(bill_id)
    assert events[0].payload["days_until_due"] < 0

    async with session_factory() as session:
        row = (
            await session.execute(
                select(PaymentScheduleModel).where(PaymentScheduleModel.id == bill_id)
            )
        ).scalar_one()
        assert row.status == "overdue"


@pytest.mark.asyncio
async def test_bills_beyond_horizon_are_not_published(session_factory):
    publisher = InMemoryEventPublisher()
    scheduler = BillDueSoonScheduler(session_factory, publisher, horizon_days=2)
    await _seed_schedule(
        session_factory,
        user_id="00000000-0000-0000-0000-00000000cafe",
        due_in_days=10,
    )

    stats = await scheduler.run_once()

    assert stats["due_soon_events"] == 0
    assert publisher.published_events == []


@pytest.mark.asyncio
async def test_on_due_date_emits_with_vencimento_hoje(session_factory):
    publisher = InMemoryEventPublisher()
    scheduler = BillDueSoonScheduler(session_factory, publisher, horizon_days=2)
    await _seed_schedule(
        session_factory,
        user_id="00000000-0000-0000-0000-00000000cafe",
        due_in_days=0,
    )

    stats = await scheduler.run_once()

    assert stats["due_soon_events"] == 1
    event = publisher.events_of_type(EventType.BILL_DUE_SOON.value)[0]
    assert event.payload["days_until_due"] == 0
    assert event.payload["reason"] == "Vence hoje"


@pytest.mark.asyncio
async def test_notified_today_is_skipped(session_factory):
    publisher = InMemoryEventPublisher()
    scheduler = BillDueSoonScheduler(session_factory, publisher, horizon_days=2)
    await _seed_schedule(
        session_factory,
        user_id="00000000-0000-0000-0000-00000000cafe",
        due_in_days=1,
        notified_at=datetime.now(UTC),
    )

    stats = await scheduler.run_once()

    assert stats["skipped_already_notified"] == 1
    assert publisher.published_events == []
