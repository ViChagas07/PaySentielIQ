# ============================================================
# PaySentinelIQ — RabbitMQ Event Publisher
# ============================================================
# Implements EventPublisher with aio-pika robust connections and
# PUBLISHER CONFIRMS: ``publish`` only returns True after the broker
# has acknowledged the message. A confirmation failure returns False
# and logs a structured error — never a silent loss.
# ============================================================

from __future__ import annotations

import logging
import time
from typing import Any

from app.messaging.domain.envelope import EventEnvelope
from app.messaging.infrastructure.rabbitmq_connection import RabbitMQConnectionManager
from app.shared.settings import get_settings

logger = logging.getLogger(__name__)


class RabbitMQEventPublisher:
    """EventPublisher backed by RabbitMQ (topic exchange + confirms)."""

    def __init__(
        self,
        connection_manager: RabbitMQConnectionManager | None = None,
        *,
        exchange_name: str | None = None,
        declare_topology: bool = True,
    ) -> None:
        settings = get_settings()
        self._exchange_name = exchange_name or settings.RABBITMQ_EXCHANGE
        self._dlx_name = settings.RABBITMQ_DLX
        self._connection_manager = connection_manager or RabbitMQConnectionManager()
        self._declare_topology = declare_topology
        self._channel: Any | None = None
        self._exchange: Any | None = None

    async def _ensure_channel(self) -> Any:
        if self._channel is not None and not self._channel.is_closed:
            return self._channel

        connection = await self._connection_manager.connect()
        # publisher_confirms=True → publish() waits for broker ack/nack.
        self._channel = await connection.channel(publisher_confirms=True)

        if self._declare_topology:
            from app.messaging.infrastructure.rabbitmq_topology import declare_topology

            await declare_topology(
                self._channel,
                exchange_name=self._exchange_name,
                dlx_name=self._dlx_name,
            )

        self._exchange = await self._channel.get_exchange(self._exchange_name)
        return self._channel

    async def publish(self, event: EventEnvelope, *, routing_key: str | None = None) -> bool:
        """Publish an event and wait for the broker confirmation."""
        import aio_pika

        key = routing_key or event.event_type
        t0 = time.monotonic()
        try:
            await self._ensure_channel()
            message = aio_pika.Message(
                body=_serialize(event),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=event.event_id,
                type=event.event_type,
                headers={
                    "event_type": event.event_type,
                    "source": event.source,
                    "version": event.version,
                    **({"correlation_id": event.correlation_id} if event.correlation_id else {}),
                },
            )
            await self._exchange.publish(message, routing_key=key, timeout=10.0)
            logger.info(
                "event_published",
                extra={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "routing_key": key,
                    "correlation_id": event.correlation_id,
                    "user_id": event.user_id,
                    "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                    "success": True,
                },
            )
            return True
        except Exception as exc:
            # Channel state is unknown after a failure — drop it so the
            # next publish reconnects cleanly.
            self._channel = None
            self._exchange = None
            logger.error(
                "event_publish_failed",
                extra={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "routing_key": key,
                    "correlation_id": event.correlation_id,
                    "user_id": event.user_id,
                    "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                    "success": False,
                    "error": str(exc),
                },
            )
            _capture_exception(exc)
            return False

    async def close(self) -> None:
        """Close channel and connection (graceful shutdown)."""
        if self._channel is not None and not self._channel.is_closed:
            try:
                await self._channel.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error closing RabbitMQ channel: %s", exc)
        self._channel = None
        self._exchange = None
        await self._connection_manager.close()


def _serialize(event: EventEnvelope) -> bytes:
    import orjson

    return orjson.dumps(event.to_dict())


def _capture_exception(exc: Exception) -> None:
    """Forward publish failures to Sentry when configured (non-fatal)."""
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # pragma: no cover
        pass
