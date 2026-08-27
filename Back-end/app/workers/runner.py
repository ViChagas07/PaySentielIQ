# ============================================================
# PaySentinelIQ — Worker Runner (graceful shutdown)
# ============================================================
# Standard lifecycle for all standalone workers:
#   1. structured logging setup
#   2. SIGTERM/SIGINT → graceful stop (drain + close broker resources)
#   3. connection cleanup on exit
# ============================================================

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from app.shared.settings import get_settings

logger = logging.getLogger(__name__)


async def run_worker(
    name: str,
    start: Callable[[], Awaitable[object]],
    *,
    stop: Callable[[], Awaitable[None]] | None = None,
    stop_event: asyncio.Event | None = None,
) -> int:
    """Run a worker until SIGINT/SIGTERM (or ``stop_event``), then shut
    down gracefully.

    ``start`` returns a handle the worker needs while running (or None);
    ``stop`` releases resources (consumer stop, connection close, ...).

    Returns the process exit code (0 = graceful shutdown).
    """
    settings = get_settings()
    if settings.ENABLE_STRUCTURED_LOGGING:
        from app.observability.logging import StructuredLogger

        StructuredLogger.setup(settings.LOG_LEVEL)

    logger.info("worker_starting", extra={"worker": name})

    stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover (Windows)
            pass

    try:
        await start()
        logger.info("worker_ready", extra={"worker": name})
        await stop_event.wait()
        logger.info("worker_shutdown_requested", extra={"worker": name})
    except asyncio.CancelledError:  # pragma: no cover - defensive
        logger.info("worker_cancelled", extra={"worker": name})
    finally:
        if stop is not None:
            try:
                await stop()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "worker_stop_error", extra={"worker": name, "error": str(exc)}
                )

    logger.info("worker_stopped", extra={"worker": name})
    return 0


def run_worker_sync(
    name: str,
    start: Callable[[], Awaitable[object]],
    *,
    stop: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Synchronous wrapper (module entrypoints) — exits the process."""
    code = asyncio.run(run_worker(name, start, stop=stop))
    raise SystemExit(code)
