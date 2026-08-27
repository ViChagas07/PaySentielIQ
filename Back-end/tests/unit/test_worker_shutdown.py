# ============================================================
# PaySentinelIQ — Worker Runner Tests (graceful shutdown)
# ============================================================

import asyncio

import pytest

from app.workers.runner import run_worker


@pytest.mark.asyncio
async def test_runner_calls_stop_after_stop_event():
    started = asyncio.Event()
    stopped = asyncio.Event()
    stop_signal = asyncio.Event()

    async def start() -> object:
        started.set()
        return None

    async def stop() -> None:
        stopped.set()

    task = asyncio.create_task(
        run_worker("test-worker", start, stop=stop, stop_event=stop_signal)
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    stop_signal.set()
    exit_code = await asyncio.wait_for(task, timeout=5)

    assert exit_code == 0
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_runner_stop_errors_are_non_fatal():
    stop_signal = asyncio.Event()

    async def start() -> object:
        return None

    async def stop() -> None:
        raise RuntimeError("close failed")  # must not crash the runner

    task = asyncio.create_task(
        run_worker("test-worker", start, stop=stop, stop_event=stop_signal)
    )
    await asyncio.sleep(0.05)
    stop_signal.set()
    exit_code = await asyncio.wait_for(task, timeout=5)

    assert exit_code == 0


@pytest.mark.asyncio
async def test_runner_without_stop_callback():
    stop_signal = asyncio.Event()

    async def start() -> object:
        return None

    task = asyncio.create_task(
        run_worker("test-worker", start, stop_event=stop_signal)
    )
    await asyncio.sleep(0.05)
    stop_signal.set()
    exit_code = await asyncio.wait_for(task, timeout=5)
    assert exit_code == 0
