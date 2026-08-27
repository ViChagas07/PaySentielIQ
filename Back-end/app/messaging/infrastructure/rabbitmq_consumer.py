# ============================================================
# PaySentinelIQ — RabbitMQ Event Consumer
# ============================================================
# Implements EventConsumer with:
#   - MANUAL acknowledgements (a message is acked only after the
#     handler completed successfully)
#   - application-managed retry with backoff via a TTL retry queue
#     (header `x-retry-count` tracks attempts — no infinite loops)
#   - dead-lettering of permanently invalid / exhausted messages
#   - structured observability per message
#
# Horizontal scaling: run N instances of the same worker process —
# RabbitMQ fair-dispatches (prefetch) across them. Consumers hold no
# local state; idempotency lives in the database (processed_events).
# ============================================================

from __future__ import annotations

import logging
import time
from typing import Any

from app.messaging.application.retry import RetryPolicy, is_permanent_error
from app.messaging.domain.envelope import EventEnvelope
from app.messaging.domain.ports import EventHandler
from app.messaging.infrastructure.rabbitmq_connection import RabbitMQConnectionManager
from app.messaging.infrastructure.rabbitmq_topology import RETRY_HEADER
from app.shared.settings import get_settings

logger = logging.getLogger(__name__)


class RabbitMQEventConsumer:
    """Consumes one queue and dispatches envelopes to a handler."""

    def __init__(
        self,
        queue_name: str,
        handler: EventHandler,
        *,
        connection_manager: RabbitMQConnectionManager | None = None,
        retry_policy: RetryPolicy | None = None,
        prefetch_count: int | None = None,
        consumer_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self.queue_name = queue_name
        self.consumer_name = consumer_name or queue_name
        self._handler = handler
        self._connection_manager = connection_manager or RabbitMQConnectionManager()
        self._retry_policy = retry_policy or RetryPolicy(
            max_attempts=settings.EVENT_RETRY_MAX_ATTEMPTS,
            backoff_seconds=tuple(settings.retry_backoff_seconds),
        )
        self._prefetch_count = prefetch_count or settings.RABBITMQ_PREFETCH_COUNT
        self._exchange_name = settings.RABBITMQ_EXCHANGE
        self._dlx_name = settings.RABBITMQ_DLX

        self._channel: Any | None = None
        self._queue: Any | None = None
        self._consumer_tag: str | None = None
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Connect, declare topology, and start consuming (non-blocking)."""
        from app.messaging.infrastructure.rabbitmq_topology import declare_topology

        connection = await self._connection_manager.connect()
        self._channel = await connection.channel(publisher_confirms=True)
        await self._channel.set_qos(prefetch_count=self._prefetch_count)

        await declare_topology(
            self._channel,
            exchange_name=self._exchange_name,
            dlx_name=self._dlx_name,
        )

        self._queue = await self._channel.get_queue(self.queue_name)
        self._consumer_tag = await self._queue.consume(self._on_message)
        self._running = True
        logger.info(
            "consumer_started",
            extra={"queue": self.queue_name, "prefetch": self._prefetch_count},
        )

    async def stop(self) -> None:
        """Stop consuming and close channel/connection gracefully."""
        self._running = False
        try:
            if self._queue is not None and self._consumer_tag is not None:
                await self._queue.cancel(self._consumer_tag)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Error cancelling consumer %s: %s", self.queue_name, exc)
        self._consumer_tag = None

        try:
            if self._channel is not None and not self._channel.is_closed:
                await self._channel.close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Error closing channel for %s: %s", self.queue_name, exc)
        self._channel = None

        await self._connection_manager.close()
        logger.info("consumer_stopped", extra={"queue": self.queue_name})

    async def health_check(self) -> bool:
        return self._running and await self._connection_manager.health_check()

    # ── Message handling ─────────────────────────────────────

    async def _on_message(self, message: Any) -> None:
        """Process one incoming AMQP message with manual ack semantics."""
        t0 = time.monotonic()
        retry_count = _retry_count_of(message)
        event: EventEnvelope | None = None

        async with message.process(requeue=False, ignore_processed=True):
            try:
                event = _decode_message(message)
                _attach_correlation(event)

                await self._handler(event)

                await message.ack()
                self._log("event_consumed", event, t0, retry_count, success=True)

            except Exception as exc:  # noqa: BLE001 - steering below
                if event is None or is_permanent_error(exc):
                    # Malformed payload or unrecoverable error → DLQ.
                    await message.reject(requeue=False)
                    self._log(
                        "event_dead_lettered",
                        event,
                        t0,
                        retry_count,
                        success=False,
                        error=str(exc),
                    )
                    return

                decision = self._retry_policy.evaluate(exc, retry_count)
                if decision.should_retry:
                    await self._republish_for_retry(message, decision.next_attempt,
                                                    decision.delay_seconds)
                    await message.ack()  # original leaves; retry copy waits in the delay queue
                    self._log(
                        "event_retry_scheduled",
                        event,
                        t0,
                        decision.next_attempt,
                        success=False,
                        error=str(exc),
                        retry_in_seconds=decision.delay_seconds,
                    )
                else:
                    await message.reject(requeue=False)
                    self._log(
                        "event_dead_lettered",
                        event,
                        t0,
                        retry_count,
                        success=False,
                        error=f"retries exhausted: {exc}",
                    )

    async def _republish_for_retry(
        self, message: Any, next_attempt: int, delay_seconds: float
    ) -> None:
        """Publish a copy of the message into `<queue>.retry` with a TTL.

        When the TTL expires, RabbitMQ dead-letters the message back into
        the main exchange with its ORIGINAL routing key.
        """
        import aio_pika

        if self._channel is None:
            raise RuntimeError("Consumer channel is not open")

        headers = dict(message.headers or {})
        headers[RETRY_HEADER] = next_attempt

        retry_message = aio_pika.Message(
            body=bytes(message.body),
            content_type=message.content_type or "application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message.message_id,
            type=message.type,
            headers=headers,
            expiration=delay_seconds,  # aio-pika converts seconds → ms
        )
        await self._channel.default_exchange.publish(
            retry_message,
            routing_key=f"{self.queue_name}.retry",
            timeout=10.0,
        )

    # ── Logging ──────────────────────────────────────────────

    def _log(
        self,
        outcome: str,
        event: EventEnvelope | None,
        t0: float,
        retry_count: int,
        *,
        success: bool,
        error: str | None = None,
        retry_in_seconds: float | None = None,
    ) -> None:
        record = {
            "queue": self.queue_name,
            "consumer": self.consumer_name,
            "event_id": event.event_id if event else None,
            "event_type": event.event_type if event else None,
            "correlation_id": event.correlation_id if event else None,
            "user_id": event.user_id if event else None,
            "retry_count": retry_count,
            "duration_ms": round((time.monotonic() - t0) * 1000, 2),
            "success": success,
        }
        if error:
            record["error"] = error
        if retry_in_seconds is not None:
            record["retry_in_seconds"] = retry_in_seconds

        if success:
            logger.info(outcome, extra=record)
        else:
            logger.error(outcome, extra=record)


# ── Helpers ──────────────────────────────────────────────────


def _retry_count_of(message: Any) -> int:
    headers = getattr(message, "headers", None) or {}
    try:
        return int(headers.get(RETRY_HEADER, 0))
    except (TypeError, ValueError):
        return 0


def _decode_message(message: Any) -> EventEnvelope:
    """Decode the AMQP body into an EventEnvelope (ValueError → DLQ)."""
    import orjson

    try:
        data = orjson.loads(bytes(message.body))
    except Exception as exc:
        raise ValueError(f"Message body is not valid JSON: {exc}") from exc
    return EventEnvelope.from_dict(data)


def _attach_correlation(event: EventEnvelope) -> None:
    """Propagate the event correlation id into the logging context."""
    try:
        from app.observability.correlation import CorrelationContext, set_correlation

        ctx = CorrelationContext()
        if event.correlation_id:
            ctx.request_id = event.correlation_id
            ctx.trace_id = event.correlation_id
        if event.tenant_id:
            ctx.tenant_id = event.tenant_id
        if event.user_id:
            ctx.user_id = event.user_id
        set_correlation(ctx)
    except Exception:  # pragma: no cover - correlation must never break consume
        pass
