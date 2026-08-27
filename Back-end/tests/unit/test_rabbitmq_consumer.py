# ============================================================
# PaySentinelIQ — RabbitMQ Consumer Tests (ack / retry / DLQ)
# ============================================================
# The consumer is tested against FAKE aio-pika messages — no broker
# required. Verifies manual-ack semantics, backoff retries and
# dead-letter steering for the exact contract the workers rely on.

from contextlib import asynccontextmanager

import pytest

from app.messaging.application.retry import RetryPolicy
from app.messaging.domain.envelope import new_event
from app.messaging.domain.event_types import EventType
from app.messaging.domain.ports import (
    PermanentProcessingError,
    TransientProcessingError,
)
from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer
from app.messaging.infrastructure.rabbitmq_topology import RETRY_HEADER


# ── Fakes ────────────────────────────────────────────────────


class FakeExchange:
    def __init__(self) -> None:
        self.published: list[tuple[object, str]] = []

    async def publish(self, message, routing_key="", timeout=None):
        self.published.append((message, routing_key))


class FakeChannel:
    def __init__(self) -> None:
        self.default_exchange = FakeExchange()


class FakeMessage:
    def __init__(self, body: bytes, headers: dict | None = None, message_id: str = "m-1"):
        self.body = body
        self.headers = headers or {}
        self.message_id = message_id
        self.content_type = "application/json"
        self.type = None
        self.acked = False
        self.rejected = False
        self.reject_requeue: bool | None = None

    async def ack(self):
        self.acked = True

    async def reject(self, requeue: bool = True):
        self.rejected = True
        self.reject_requeue = requeue

    @asynccontextmanager
    async def process(self, **kwargs):
        try:
            yield
        finally:
            pass


def _consumer(handler, *, max_attempts: int = 3) -> RabbitMQEventConsumer:
    consumer = RabbitMQEventConsumer(
        "sentinel.email",
        handler,
        retry_policy=RetryPolicy(max_attempts=max_attempts, backoff_seconds=(1, 10)),
    )
    consumer._channel = FakeChannel()
    return consumer


def _message_for_event(headers: dict | None = None) -> FakeMessage:
    event = new_event(
        EventType.BILL_DUE_SOON.value,
        payload={"bill_id": "b-1"},
        user_id="00000000-0000-0000-0000-00000000cafe",
        tenant_id="00000000-0000-0000-0000-00000000feed",
    )
    import orjson

    return FakeMessage(orjson.dumps(event.to_dict()), headers=headers)


# ── Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_acks_message():
    handled: list[str] = []

    async def handler(event):
        handled.append(event.event_id)

    consumer = _consumer(handler)
    message = _message_for_event()

    await consumer._on_message(message)

    assert message.acked is True
    assert message.rejected is False
    assert len(handled) == 1


@pytest.mark.asyncio
async def test_transient_error_schedules_retry_and_acks_original():
    calls = 0

    async def handler(event):
        nonlocal calls
        calls += 1
        raise TransientProcessingError("db down")

    consumer = _consumer(handler)
    message = _message_for_event()
    channel = consumer._channel

    await consumer._on_message(message)

    assert message.acked is True
    assert message.rejected is False
    assert len(channel.default_exchange.published) == 1
    retry_message, routing_key = channel.default_exchange.published[0]
    assert routing_key == "sentinel.email.retry"
    assert retry_message.headers[RETRY_HEADER] == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_count_increments_on_second_failure():
    async def handler(event):
        raise TransientProcessingError("still down")

    consumer = _consumer(handler)
    message = _message_for_event(headers={RETRY_HEADER: 1})
    channel = consumer._channel

    await consumer._on_message(message)

    retry_message, _ = channel.default_exchange.published[0]
    assert retry_message.headers[RETRY_HEADER] == 2


@pytest.mark.asyncio
async def test_exhausted_retries_go_to_dead_letter():
    async def handler(event):
        raise TransientProcessingError("forever down")

    consumer = _consumer(handler)
    # retry count 2 + this failure = attempt 3 == max → DLQ.
    message = _message_for_event(headers={RETRY_HEADER: 2})

    await consumer._on_message(message)

    assert message.rejected is True
    assert message.reject_requeue is False
    assert consumer._channel.default_exchange.published == []


@pytest.mark.asyncio
async def test_permanent_error_goes_straight_to_dlq():
    async def handler(event):
        raise PermanentProcessingError("invalid business data")

    consumer = _consumer(handler)
    message = _message_for_event()

    await consumer._on_message(message)

    assert message.rejected is True
    assert message.reject_requeue is False
    assert consumer._channel.default_exchange.published == []


@pytest.mark.asyncio
async def test_malformed_body_goes_to_dlq():
    async def handler(event):  # pragma: no cover - never called
        raise AssertionError("handler must not run")

    consumer = _consumer(handler)
    message = FakeMessage(b"not-json-at-all")

    await consumer._on_message(message)

    assert message.rejected is True
    assert message.reject_requeue is False


@pytest.mark.asyncio
async def test_handler_exception_without_envelope_goes_to_dlq():
    async def handler(event):
        raise TransientProcessingError("x")

    consumer = _consumer(handler)
    message = FakeMessage(b"[]")  # valid JSON, invalid envelope

    await consumer._on_message(message)

    assert message.rejected is True
    assert message.reject_requeue is False
