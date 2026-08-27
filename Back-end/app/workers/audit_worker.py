# ============================================================
# PaySentinelIQ — Audit Worker
# ============================================================
# Consumes `sentinel.audit` and persists the activity history.
# Run:  python -m app.workers.audit_worker
# ============================================================

from __future__ import annotations

import logging

from app.workers.runner import run_worker_sync

logger = logging.getLogger(__name__)


def _build() -> tuple[str, object, object]:
    from app.messaging.application.handlers import AuditEventHandler
    from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer
    from app.messaging.infrastructure.rabbitmq_topology import AUDIT_QUEUE
    from app.shared.settings import get_settings

    settings = get_settings()
    if not settings.RABBITMQ_ENABLED:
        raise SystemExit("RABBITMQ_ENABLED=false — audit worker refused to start")

    handler = AuditEventHandler()
    consumer = RabbitMQEventConsumer(
        AUDIT_QUEUE,
        handler,  # type: ignore[arg-type]
        consumer_name="audit-worker",
    )
    return AUDIT_QUEUE, consumer, handler


def main() -> None:
    queue, consumer, _handler = _build()

    async def start() -> object:
        await consumer.start()  # type: ignore[attr-defined]
        return consumer

    async def stop() -> None:
        await consumer.stop()  # type: ignore[attr-defined]

    run_worker_sync(f"audit_worker[{queue}]", start, stop=stop)


if __name__ == "__main__":
    main()
