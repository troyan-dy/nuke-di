import asyncio

import pytest

from nuke_di import BackgroundTasks, Dependencies, Shutdown


async def test_shutdown_is_not_set_initially() -> None:
    shutdown = Shutdown()

    assert not shutdown.is_set()


async def test_shutdown_wait_returns_once_set() -> None:
    shutdown = Shutdown()
    waiter = asyncio.create_task(shutdown.wait())
    await asyncio.sleep(0)
    assert not waiter.done()

    shutdown.set()
    await asyncio.wait_for(waiter, timeout=1)

    assert shutdown.is_set()


async def test_shutdown_is_a_singleton_client() -> None:
    dep = Dependencies()

    assert dep.resolve(Shutdown) is dep.resolve(Shutdown)


async def test_spawned_task_runs() -> None:
    tasks = BackgroundTasks()
    done = asyncio.Event()

    async def work() -> None:
        done.set()

    task = tasks.spawn(work(), name="work")
    await asyncio.wait_for(done.wait(), timeout=1)

    assert task.get_name() == "work"


async def test_disconnect_cancels_and_awaits_tasks() -> None:
    tasks = BackgroundTasks()
    cleaned_up = False

    async def loop() -> None:
        nonlocal cleaned_up
        try:
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0)
            cleaned_up = True

    task = tasks.spawn(loop())
    await asyncio.sleep(0)
    await tasks.disconnect()

    assert task.cancelled()
    assert cleaned_up


async def test_spawn_after_stop_raises_and_closes_coroutine() -> None:
    tasks = BackgroundTasks()
    await tasks.stop()

    async def work() -> None:
        pass

    coro = work()
    with pytest.raises(RuntimeError, match="stopping"):
        tasks.spawn(coro)

    # a closed coroutine cannot be started, and no "never awaited" warning is emitted
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        coro.send(None)


async def test_failed_task_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    tasks = BackgroundTasks()

    async def broken() -> None:
        raise RuntimeError("boom")

    task = tasks.spawn(broken(), name="broken")
    await asyncio.wait({task})
    await asyncio.sleep(0)

    assert "Background task broken failed" in caplog.text
    assert "RuntimeError: boom" in caplog.text


async def test_failure_is_reported_to_watcher() -> None:
    tasks = BackgroundTasks()
    failures: list[BaseException] = []
    tasks.watch(failures.append)

    async def broken() -> None:
        raise RuntimeError("boom")

    async def fine() -> None:
        pass

    await asyncio.wait({tasks.spawn(fine()), tasks.spawn(broken())})
    await asyncio.sleep(0)

    assert [str(exc) for exc in failures] == ["boom"]


async def test_cancelled_task_is_not_a_failure() -> None:
    tasks = BackgroundTasks()
    failures: list[BaseException] = []
    tasks.watch(failures.append)

    task = tasks.spawn(asyncio.sleep(10))
    await asyncio.sleep(0)
    await tasks.stop()

    assert task.cancelled()
    assert failures == []
