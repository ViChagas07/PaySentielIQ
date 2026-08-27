# ============================================================
# PaySentinelIQ — RabbitMQ Topology (declarative)
# ============================================================
# Exchanges, queues and bindings are declared idempotently at
# startup by publishers and consumers alike (declare = no-op when
# the entity already exists with the same arguments).
#
#   sentinel.events (topic)
#     ├── sentinel.audit             bind: "#"
#     ├── sentinel.notifications     bind: bill.due_soon, bill.overdue
#     └── sentinel.email             bind: bill.due_soon
#
#   sentinel.dlx (topic)
#     ├── sentinel.audit.dlq         bind: "sentinel.audit"
#     ├── sentinel.notifications.dlq bind: "sentinel.notifications"
#     └── sentinel.email.dlq         bind: "sentinel.email"
#
#   Each consumer queue also owns a `<queue>.retry` delay queue whose
#   dead-letter exchange is `sentinel.events`. When a message sitting
#   in a retry queue expires (per-message TTL), RabbitMQ republishes it
#   to `sentinel.events` with its ORIGINAL routing key — so it lands
#   back on the queues that originally matched it.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.messaging.domain.event_types import EventType

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aio_pika

# ── Queue names ──────────────────────────────────────────────

AUDIT_QUEUE = "sentinel.audit"
NOTIFICATIONS_QUEUE = "sentinel.notifications"
EMAIL_QUEUE = "sentinel.email"

RETRY_QUEUE_SUFFIX = ".retry"
DLQ_SUFFIX = ".dlq"

# ── Bindings ─────────────────────────────────────────────────

AUDIT_BINDINGS: tuple[str, ...] = ("#",)  # audit trail sees everything relevant
NOTIFICATIONS_BINDINGS: tuple[str, ...] = (
    EventType.BILL_DUE_SOON.value,
    EventType.BILL_OVERDUE.value,
)
EMAIL_BINDINGS: tuple[str, ...] = (EventType.BILL_DUE_SOON.value,)

RETRY_HEADER = "x-retry-count"


@dataclass(frozen=True)
class QueueSpec:
    """Declarative description of one consumer queue and its satellites."""

    name: str
    bindings: tuple[str, ...]
    durable: bool = True
    extra_bindings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def retry_queue(self) -> str:
        return f"{self.name}{RETRY_QUEUE_SUFFIX}"

    @property
    def dlq(self) -> str:
        return f"{self.name}{DLQ_SUFFIX}"


def consumer_queues() -> tuple[QueueSpec, ...]:
    """All consumer queues of the platform (single declaration point)."""
    return (
        QueueSpec(name=AUDIT_QUEUE, bindings=AUDIT_BINDINGS),
        QueueSpec(name=NOTIFICATIONS_QUEUE, bindings=NOTIFICATIONS_BINDINGS),
        QueueSpec(name=EMAIL_QUEUE, bindings=EMAIL_BINDINGS),
    )


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel,
    *,
    exchange_name: str,
    dlx_name: str,
) -> None:
    """Declare exchanges, queues and bindings (idempotent).

    Called by publishers and consumers on startup so any process can
    bootstrap the broker independently of boot order.
    """
    import aio_pika

    exchange = await channel.declare_exchange(
        exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dlx = await channel.declare_exchange(
        dlx_name, aio_pika.ExchangeType.TOPIC, durable=True
    )

    for spec in consumer_queues():
        await declare_consumer_queue(
            channel, spec, exchange=exchange, dlx=dlx
        )


async def declare_consumer_queue(
    channel: aio_pika.abc.AbstractChannel,
    spec: QueueSpec,
    *,
    exchange: aio_pika.abc.AbstractExchange,
    dlx: aio_pika.abc.AbstractExchange,
) -> None:
    """Declare one consumer queue + its retry delay queue + its DLQ.

    Extracted so tests (and future modules) can declare custom queues
    with the exact same satellite topology.
    """
    # ── Main consumer queue (dead-letters into the DLX) ──
    queue = await channel.declare_queue(
        spec.name,
        durable=spec.durable,
        arguments={
            "x-dead-letter-exchange": dlx.name,
            "x-dead-letter-routing-key": spec.name,
        },
    )
    for binding in (*spec.bindings, *spec.extra_bindings):
        await queue.bind(exchange, routing_key=binding)

    # ── Retry queue (TTL delay → back to the main exchange) ──
    await channel.declare_queue(
        spec.retry_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": exchange.name},
    )

    # ── Dead-letter queue (terminal parking) ──
    dlq = await channel.declare_queue(spec.dlq, durable=True)
    await dlq.bind(dlx, routing_key=spec.name)
