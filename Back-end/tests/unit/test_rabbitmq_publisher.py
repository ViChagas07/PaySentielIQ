# ============================================================
# PaySentinelIQ — RabbitMQ Publisher Tests (publisher confirms)
# ============================================================
# Verifies the publisher serializes envelopes into AMQP messages,
# routes by event_type by default, waits for confirms, and resets
# the channel on failure. Uses fakes — no broker required.

import pytest

from app.messaging.domain.envelope import new_event
from app.messaging.domain.event_types import EventType
from app.messaging.infrastructure.rabbitmq_publisher import RabbitMQEventPublisher


# ── Fakes ────────────────────────────────────────────────────


class FakeExchange:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.published: list[tuple[object, str]] = []

    async def publish(self, message, routing_key="", timeout=None):
        if self.fail:
            raise ConnectionError("broker nack")
        self.published.append((message, routing_key))


class FakeChannel:
    def __init__(self, exchange: FakeExchange):
        self.exchange = exchange
        self.is_closed = False
        self.publisher_confirms = True

    async def get_exchange(self, name: str):
        return self.exchange


class FakeConnection:
    def __init__(self, exchange: FakeExchange):
        self.exchange = exchange
        self.is_closed = False

    async def channel(self, publisher_confirms: bool = False):
        return FakeChannel(self.exchange)


class FakeConnectionManager:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    async def connect(self):
        return self.connection

    async def close(self):
        return None


def _publisher(exchange: FakeExchange) -> RabbitMQEventPublisher:
    manager = FakeConnectionManager(FakeConnection(exchange))
    return RabbitMQEventPublisher(
        connection_manager=manager, declare_topology=False
    )


@pytest.mark.asyncio
async def test_publish_returns_true_after_confirm():
    exchange = FakeExchange()
    publisher = _publisher(exchange)
    event = new_event(
        EventType.BILL_SCHEDULED.value,
        payload={"bill_id": "b-1"},
        user_id="u-1",
        tenant_id="t-1",
    )

    ok = await publisher.publish(event)

    assert ok is True
    assert len(exchange.published) == 1
    message, routing_key = exchange.published[0]
    assert routing_key == EventType.BILL_SCHEDULED.value
    assert message.message_id == event.event_id
    assert message.delivery_mode.name == "PERSISTENT"


@pytest.mark.asyncio
async def test_custom_routing_key_is_used():
    exchange = FakeExchange()
    publisher = _publisher(exchange)
    event = new_event(EventType.BILL_DUE_SOON.value, payload={})

    await publisher.publish(event, routing_key="custom.key")

    _, routing_key = exchange.published[0]
    assert routing_key == "custom.key"


@pytest.mark.asyncio
async def test_publish_failure_returns_false_and_resets_channel():
    exchange = FakeExchange(fail=True)
    publisher = _publisher(exchange)
    event = new_event(EventType.BILL_DUE_SOON.value, payload={})

    ok = await publisher.publish(event)

    assert ok is False
    assert publisher._channel is None  # channel reset → clean reconnect next time

    # Recovery: next publish succeeds with a healthy exchange.
    exchange.fail = False
    ok = await publisher.publish(event)
    assert ok is True


@pytest.mark.asyncio
async def test_close_releases_resources():
    exchange = FakeExchange()
    publisher = _publisher(exchange)
    await publisher.publish(new_event(EventType.BILL_PAID.value, payload={}))

    await publisher.close()

    assert publisher._channel is None
