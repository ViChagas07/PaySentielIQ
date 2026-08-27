# ============================================================
# PaySentinelIQ — Notification Worker
# ============================================================
# Consumes `sentinel.notifications` and creates in-app
# notifications (Notification Center) + WebSocket push.
# Run:  python -m app.workers.notification_worker
# ============================================================

from __future__ import annotations

import logging

from app.workers.runner import run_worker_sync

logger = logging.getLogger(__name__)


def main() -> None:
    from app.messaging.application.handlers import NotificationEventHandler
    from app.messaging.infrastructure.rabbitmq_consumer import RabbitMQEventConsumer
    from app.messaging.infrastructure.rabbitmq_topology import NOTIFICATIONS_QUEUE
    from app.shared.settings import get_settings

    settings = get_settings()
    if not settings.RABBITMQ_ENABLED:
        raise SystemExit("RABBITMQ_ENABLED=false — notification worker refused to start")

    handler = NotificationEventHandler()
    consumer = RabbitMQEventConsumer(
        NOTIFICATIONS_QUEUE,
        handler,  # type: ignore[arg-type]
        consumer_name="notification-worker",
    )

    async def start() -> object:
        await consumer.start()
        return consumer

    async def stop() -> None:
        await consumer.stop()

    run_worker_sync(f"notification_worker[{NOTIFICATIONS_QUEUE}]", start, stop=stop)


if __name__ == "__main__":
    main()
