# ============================================================
# PaySentinelIQ — Event Publisher Factory
# ============================================================
# Single process-wide access point for the EventPublisher port.
# The API, workers and the scheduler all obtain the publisher from
# here — never by instantiating aio-pika directly.
# ============================================================

from __future__ import annotations

from app.messaging.domain.ports import EventPublisher
from app.messaging.infrastructure.null_publisher import NullEventPublisher
from app.shared.settings import get_settings

_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Return the process-wide publisher (RabbitMQ or no-op fallback).

    Lazily creates the real RabbitMQ publisher on first use. When
    RABBITMQ_ENABLED=false, a NullEventPublisher is returned so business
    code never has to branch on broker availability.
    """
    global _publisher

    if _publisher is None:
        settings = get_settings()
        if settings.RABBITMQ_ENABLED:
            from app.messaging.infrastructure.rabbitmq_publisher import (
                RabbitMQEventPublisher,
            )

            _publisher = RabbitMQEventPublisher()
        else:
            _publisher = NullEventPublisher()
    return _publisher


async def close_event_publisher() -> None:
    """Close the process-wide publisher (graceful shutdown)."""
    global _publisher
    if _publisher is not None:
        await _publisher.close()
        _publisher = None


def set_event_publisher(publisher: EventPublisher) -> None:
    """Override the process-wide publisher (used by tests)."""
    global _publisher
    _publisher = publisher
