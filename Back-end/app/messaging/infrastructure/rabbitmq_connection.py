# ============================================================
# PaySentinelIQ — RabbitMQ Connection Manager
# ============================================================
# Single place that owns TCP/AMQP connections. Robust connections
# (aio-pika) auto-reconnect on transient network failures.
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.shared.settings import get_settings

logger = logging.getLogger(__name__)


class RabbitMQConnectionManager:
    """Lazily creates and caches a robust AMQP connection.

    One instance per process (API worker, consumer worker, scheduler).
    Connections are bound to the event loop that created them — callers
    running in a different loop (e.g. Celery tasks spinning ephemeral
    loops) must create their own short-lived manager.
    """

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self._url = url or settings.RABBITMQ_URL.get_secret_value()
        self._connect_timeout = settings.RABBITMQ_CONNECT_TIMEOUT
        self._heartbeat = settings.RABBITMQ_HEARTBEAT
        self._connection: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    # ── Connection lifecycle ─────────────────────────────────

    async def connect(self) -> Any:
        """Return a live robust connection, creating it if needed."""
        import aio_pika

        current_loop = asyncio.get_running_loop()

        # aio-pika connections are loop-bound: if the cached connection
        # belongs to another (closed) loop, drop it.
        if (
            self._connection is not None
            and (self._loop is not current_loop or self._connection.is_closed)
        ):
            self._connection = None

        if self._connection is not None:
            return self._connection

        if self._lock is None or getattr(self._lock, "_loop", current_loop) is not current_loop:
            self._lock = asyncio.Lock()

        async with self._lock:
            if self._connection is not None and not self._connection.is_closed:
                return self._connection

            logger.info("Connecting to RabbitMQ (timeout=%ss)...", self._connect_timeout)
            self._connection = await asyncio.wait_for(
                aio_pika.connect_robust(
                    self._url,
                    heartbeat=self._heartbeat,
                    timeout=self._connect_timeout,
                ),
                timeout=self._connect_timeout + 5,
            )
            self._loop = current_loop
            logger.info("RabbitMQ connection established")
            return self._connection

    async def close(self) -> None:
        """Close the connection gracefully."""
        if self._connection is not None and not self._connection.is_closed:
            try:
                await self._connection.close()
                logger.info("RabbitMQ connection closed")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error closing RabbitMQ connection: %s", exc)
        self._connection = None

    async def health_check(self) -> bool:
        """Open and close a channel — the cheapest real liveness probe."""
        try:
            connection = await self.connect()
            channel = await connection.channel()
            await channel.close()
            return True
        except Exception as exc:
            logger.warning("RabbitMQ health check failed: %s", exc)
            return False
