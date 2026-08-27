# ============================================================
# PaySentinelIQ — Messaging Ports (Hexagonal Interfaces)
# ============================================================
# Application/domain code depends ONLY on these protocols.
# The RabbitMQ implementation lives in app.messaging.infrastructure.
# ============================================================

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

from app.messaging.domain.envelope import EventEnvelope

# ── Handler errors ───────────────────────────────────────────


class TransientProcessingError(Exception):
    """A recoverable failure — the message is eligible for retry."""


class PermanentProcessingError(Exception):
    """An unrecoverable failure — the message must go straight to the DLQ."""


# Handler signature: receives the decoded envelope; raises
# TransientProcessingError / PermanentProcessingError to steer retry.
EventHandler = Callable[[EventEnvelope], Coroutine[Any, Any, None]]


@runtime_checkable
class EventPublisher(Protocol):
    """Publishes domain events to the message broker."""

    async def publish(self, event: EventEnvelope, *, routing_key: str | None = None) -> bool:
        """Publish one event. Returns True only after broker confirmation."""
        ...

    async def close(self) -> None:
        """Release underlying connections/resources."""
        ...


@runtime_checkable
class EventConsumer(Protocol):
    """Consumes events from a queue with manual acknowledgements."""

    queue_name: str

    async def start(self) -> None:
        """Connect, declare topology and start consuming."""
        ...

    async def stop(self) -> None:
        """Stop consuming and release resources (graceful shutdown)."""
        ...

    async def health_check(self) -> bool:
        """True when the underlying broker connection is usable."""
        ...
