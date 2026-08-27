# ============================================================
# PaySentinelIQ — Email Handler Tests
# ============================================================
# The sentinel.email consumer must:
#   - deliver bill.due_soon emails through the EmailService port
#   - never send the same event twice (processed_events dedupe)
#   - respect the user's email_alerts preference
#   - retry (raise TransientProcessingError) when delivery fails

import uuid

import pytest
from sqlalchemy import select

from app.messaging.application.handlers.email import EmailEventHandler
from app.messaging.domain.envelope import EventEnvelope, new_event
from app.messaging.domain.event_types import EventType
from app.messaging.domain.ports import PermanentProcessingError, TransientProcessingError
from app.notifications.domain.ports import EmailMessage
from app.shared.orm_models import ProcessedEventModel, UserModel, UserSettingsModel


class FakeEmailService:
    """Records sent messages; optionally fails."""

    def __init__(self, fail_times: int = 0):
        self.sent: list[EmailMessage] = []
        self._failures_left = fail_times

    async def send(self, message: EmailMessage) -> None:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise ConnectionError("smtp down (transient)")
        self.sent.append(message)


async def _seed_user(session_factory, *, email_alerts: bool | None = None) -> str:
    """Create a user (+ optional settings) and return its id."""
    uid = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            UserModel(
                id=uid,
                tenant_id=uuid.uuid4(),
                email=f"user-{uid.hex[:8]}@example.com",
                full_name="Test User",
                hashed_password="x",
                role="viewer",
            )
        )
        if email_alerts is not None:
            session.add(
                UserSettingsModel(user_id=uid, email_alerts=email_alerts)
            )
        await session.commit()
    return str(uid)


def _due_soon_event(user_id: str) -> EventEnvelope:
    return new_event(
        EventType.BILL_DUE_SOON.value,
        payload={
            "bill_id": str(uuid.uuid4()),
            "due_date": "2026-08-29T00:00:00+00:00",
            "amount": 129.9,
            "beneficiary": "Internet Fibra",
            "days_until_due": 2,
        },
        user_id=user_id,
        tenant_id="00000000-0000-0000-0000-00000000feed",
    )


@pytest.mark.asyncio
async def test_sends_email_once(session_factory):
    uid = await _seed_user(session_factory)
    fake = FakeEmailService()
    handler = EmailEventHandler(session_factory, fake)
    event = _due_soon_event(uid)

    await handler(event)

    assert len(fake.sent) == 1
    assert "129,90" in fake.sent[0].body_text
    assert "Internet Fibra" in fake.sent[0].body_text
    assert fake.sent[0].body_html is not None

    async with session_factory() as session:
        marker = (
            await session.execute(
                select(ProcessedEventModel).where(
                    ProcessedEventModel.event_id == event.event_id
                )
            )
        ).scalar_one()
        assert marker.consumer == "sentinel.email"


@pytest.mark.asyncio
async def test_duplicate_delivery_never_sends_twice(session_factory):
    uid = await _seed_user(session_factory)
    fake = FakeEmailService()
    handler = EmailEventHandler(session_factory, fake)
    event = _due_soon_event(uid)

    await handler(event)
    await handler(event)  # broker redelivery

    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_transient_failure_propagates_and_is_retryable(session_factory):
    uid = await _seed_user(session_factory)
    fake = FakeEmailService(fail_times=1)
    handler = EmailEventHandler(session_factory, fake)
    event = _due_soon_event(uid)

    with pytest.raises(TransientProcessingError):
        await handler(event)
    assert len(fake.sent) == 0  # nothing delivered

    # Retry succeeds (marker was NOT committed during the failure).
    await handler(event)
    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_email_alerts_disabled_skips_without_error(session_factory):
    uid = await _seed_user(session_factory, email_alerts=False)
    fake = FakeEmailService()
    handler = EmailEventHandler(session_factory, fake)

    await handler(_due_soon_event(uid))

    assert len(fake.sent) == 0


@pytest.mark.asyncio
async def test_unknown_user_is_permanent_error(session_factory):
    fake = FakeEmailService()
    handler = EmailEventHandler(session_factory, fake)
    event = _due_soon_event(str(uuid.uuid4()))

    with pytest.raises(PermanentProcessingError):
        await handler(event)
