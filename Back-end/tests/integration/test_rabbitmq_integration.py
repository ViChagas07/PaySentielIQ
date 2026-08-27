# ============================================================
# PaySentinelIQ — RabbitMQ Integration Tests
# ============================================================
# End-to-end broker roundtrips: publish → consume → ack; retry with
# backoff; dead-lettering. These tests require a REAL RabbitMQ.
#
# Enable with:
#   RABBITMQ_TEST_URL=amqp://guest:guest@localhost:5672/ pytest tests/integration/test_rabbitmq_integration.py -v
#
# They are skipped by default so the unit suite never needs a broker.
# ============================================================

import asyncio
import os
import uuid

import pytest

from app.messaging.domain.envelope import new_event
from app.messaging.domain.ports import PermanentProcessingError, TransientProcessingError
from app.messaging.infrastructure.rabbitmq_connection import RabbitMQConnectionManager
from app.messaging.infrastructure.rabbitmq_publisher import RabbitMQEventPublisher
from app.messaging.infrastructure.rabbitmq_topology import (
    QueueSpec,
    declare_consumer_queue,
)

TEST_URL = os.environ.get("RABBITMQ_TEST_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="RABBITMQ_TEST_URL not set — set it to run broker tests"
)


@pytest.fixture
def unique_queue() -> str:
    return f"sentinel.test.{uuid.uuid4().hex[:12]}"


async def _declare_custom_queue(connection, queue_name: str, *, exchange_name: str, dlx_name: str) -> None:
    """Declare a custom consumer queue with retry + DLQ satellites."""
    import aio_pika

    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dlx = await channel.declare_exchange(dlx_name, aio_pika.ExchangeType.TOPIC, durable=True)
    spec = QueueSpec(name=queue_name, bindings=(queue_name,), durable=False)
    await declare_consumer_queue(channel, spec, exchange=exchange, dlx=dlx)
    await channel.close()


async def _purge(connection, *queue_names: str) -> None:
    channel = await connection.channel()
    for name in queue_names:
        queue = await channel.get_queue(name)
        await queue.purge()
    await channel.close()


@pytest.mark.asyncio
async def test_publish_consume_roundtrip(unique_queue):
    manager = RabbitMQConnectionManager(TEST_URL)
    connection = await manager.connect()
    try:
        await _declare_custom_queue(connection, unique_queue,
                                    exchange_name="sentinel.events",
                                    dlx_name="sentinel.dlx")

        received: list[str] = []

        async def handler(event):
            received.append(event.event_id)

        from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer

        consumer = RabbitMQEventConsumer(
            unique_queue, handler, connection_manager=manager, prefetch_count=1
        )
        await consumer.start()

        publisher = RabbitMQEventPublisher(
            connection_manager=manager, declare_topology=False
        )
        event = new_event(unique_queue, payload={"integration": True})
        ok = await publisher.publish(event, routing_key=unique_queue)
        assert ok is True

        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.1)

        assert received == [event.event_id]
        await consumer.stop()
        await publisher.close()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_retry_then_success(unique_queue):
    manager = RabbitMQConnectionManager(TEST_URL)
    connection = await manager.connect()
    try:
        await _declare_custom_queue(connection, unique_queue,
                                    exchange_name="sentinel.events",
                                    dlx_name="sentinel.dlx")
        await _purge(connection, unique_queue, f"{unique_queue}.retry",
                     f"{unique_queue}.dlq")

        attempts = 0

        async def handler(event):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TransientProcessingError("transient failure")

        from app.messaging.application.retry import RetryPolicy
        from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer

        consumer = RabbitMQEventConsumer(
            unique_queue,
            handler,
            connection_manager=manager,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=(1, 1, 1)),
            prefetch_count=1,
        )
        await consumer.start()

        publisher = RabbitMQEventPublisher(
            connection_manager=manager, declare_topology=False
        )
        event = new_event(unique_queue, payload={})
        await publisher.publish(event, routing_key=unique_queue)

        for _ in range(60):  # wait for retry TTL (1s) + second attempt
            if attempts >= 2:
                break
            await asyncio.sleep(0.2)

        assert attempts >= 2
        await consumer.stop()
        await publisher.close()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_permanent_error_lands_in_dlq(unique_queue):
    manager = RabbitMQConnectionManager(TEST_URL)
    connection = await manager.connect()
    try:
        await _declare_custom_queue(connection, unique_queue,
                                    exchange_name="sentinel.events",
                                    dlx_name="sentinel.dlx")
        await _purge(connection, unique_queue, f"{unique_queue}.retry",
                     f"{unique_queue}.dlq")

        async def handler(event):
            raise PermanentProcessingError("poison message")

        from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer

        consumer = RabbitMQEventConsumer(
            unique_queue, handler, connection_manager=manager, prefetch_count=1
        )
        await consumer.start()

        publisher = RabbitMQEventPublisher(
            connection_manager=manager, declare_topology=False
        )
        event = new_event(unique_queue, payload={})
        await publisher.publish(event, routing_key=unique_queue)

        await asyncio.sleep(1.0)

        channel = await connection.channel()
        dlq = await channel.get_queue(f"{unique_queue}.dlq")
        parked = await dlq.get(timeout=3)
        assert parked is not None
        assert parked.message_id == event.event_id
        await parked.ack()
        await channel.close()

        await consumer.stop()
        await publisher.close()
    finally:
        await manager.close()
