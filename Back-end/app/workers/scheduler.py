# ============================================================
# PaySentinelIQ — Bill Due-Soon Scheduler Worker
# ============================================================
# Periodic scanner: identifies bills near their due date and
# publishes `bill.due_soon` / `bill.overdue` events.
# It NEVER sends emails or creates notifications directly.
# Run:  python -m app.workers.scheduler
# ============================================================

from __future__ import annotations

import asyncio
import logging

from app.workers.runner import run_worker_sync

logger = logging.getLogger(__name__)


def main() -> None:
    from app.messaging.application.bill_scheduler import BillDueSoonScheduler
    from app.shared.settings import get_settings

    settings = get_settings()
    if not settings.BILL_SCHEDULER_ENABLED:
        raise SystemExit("BILL_SCHEDULER_ENABLED=false — scheduler refused to start")

    scheduler = BillDueSoonScheduler()
    stop_event = asyncio.Event()
    state: dict[str, asyncio.Task[None] | None] = {"loop_task": None}

    async def _loop() -> None:
        while not stop_event.is_set():
            try:
                await scheduler.run_once()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("scheduler_scan_failed", extra={"error": str(exc)})
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.BILL_SCHEDULER_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue

    async def start() -> object:
        from app.messaging.infrastructure.factory import get_event_publisher

        # Touch the publisher once so a misconfigured broker fails fast.
        _ = get_event_publisher()
        state["loop_task"] = asyncio.create_task(_loop())
        return None

    async def stop() -> None:
        stop_event.set()
        loop_task = state.get("loop_task")
        if loop_task is not None:
            try:
                await asyncio.wait_for(loop_task, timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                loop_task.cancel()
        from app.messaging.infrastructure.factory import close_event_publisher

        await close_event_publisher()

    run_worker_sync("bill_scheduler", start, stop=stop)


if __name__ == "__main__":
    main()
