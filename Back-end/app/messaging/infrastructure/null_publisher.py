# ============================================================
# PaySentinelIQ — Null / In-Memory Publishers
# ============================================================
# - NullEventPublisher: no-op used when RABBITMQ_ENABLED=false or
#   by code paths that must never hard-fail on broker absence.
# - InMemoryEventPublisher: collects published events in-process.
#   Used by unit tests (no RabbitMQ required) and useful for local
#   debugging of event flows.
# ============================================================

from __future__ import annotations

import logging

from app.messaging.domain.envelope import EventEnvelope

logger = logging.getLogger(__name__)


class NullEventPublisher:
    """EventPublisher that silently drops events (dev fallback)."""

    async def publish(self, event: EventEnvelope, *, routing_key: str | None = None) -> bool:
        logger.debug(
            "event dropped by NullEventPublisher: %s (%s)",
            event.event_type,
            event.event_id,
        )
        return True

    async def close(self) -> None:
        return None


class InMemoryEventPublisher:
    """EventPublisher that records events in-process (tests/dev)."""

    def __init__(self) -> None:
        self._events: list[tuple[EventEnvelope, str | None]] = []

    async def publish(self, event: EventEnvelope, *, routing_key: str | None = None) -> bool:
        self._events.append((event, routing_key or event.event_type))
        return True

    async def close(self) -> None:
        return None

    @property
    def published_events(self) -> list[EventEnvelope]:
        return [event for event, _ in self._events]

    def events_of_type(self, event_type: str) -> list[EventEnvelope]:
        return [e for e in self.published_events if e.event_type == event_type]

    async def __aenter__(self) -> "InMemoryEventPublisher":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
