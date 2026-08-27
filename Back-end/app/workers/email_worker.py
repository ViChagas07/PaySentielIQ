# ============================================================
# PaySentinelIQ — Email Worker
# ============================================================
# Consumes `sentinel.email` and delivers transactional emails.
# Run:  python -m app.workers.email_worker
# ============================================================

from __future__ import annotations

import logging

from app.workers.runner import run_worker_sync

logger = logging.getLogger(__name__)


def main() -> None:
    from app.messaging.application.handlers import EmailEventHandler
    from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer
    from app.messaging.infrastructure.rabbitmq_topology import EMAIL_QUEUE
    from app.shared.settings import get_settings

    settings = get_settings()
    if not settings.RABBITMQ_ENABLED:
        raise SystemExit("RABBITMQ_ENABLED=false — email worker refused to start")

    handler = EmailEventHandler()
    consumer = RabbitMQEventConsumer(
        EMAIL_QUEUE,
        handler,  # type: ignore[arg-type]
        consumer_name="email-worker",
    )

    async def start() -> object:
        await consumer.start()
        return consumer

    async def stop() -> None:
        await consumer.stop()

    run_worker_sync(f"email_worker[{EMAIL_QUEUE}]", start, stop=stop)


if __name__ == "__main__":
    main()
