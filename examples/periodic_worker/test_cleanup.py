import asyncio

from nuke_di import Dependencies, Shutdown

from periodic_worker.cleanup import cleanup
from periodic_worker.clients import Sessions


async def test_delete_expired() -> None:
    sessions = Sessions()
    sessions.add("old", ttl=0)
    sessions.add("fresh", ttl=60)

    assert await sessions.delete_expired() == ["old"]
    assert len(sessions) == 1


async def test_stops_at_once_on_shutdown() -> None:
    sessions, shutdown = Sessions(), Shutdown()
    worker = asyncio.create_task(cleanup(sessions, shutdown, interval=3600))
    await asyncio.sleep(0)  # the first cleanup runs, then the hour-long wait begins

    shutdown.set()  # what SIGTERM would do

    # Done without waiting out the interval
    await asyncio.wait_for(worker, timeout=1)


async def test_runs_every_interval(di: Dependencies) -> None:
    sessions, shutdown = di.resolve(Sessions), di.resolve(Shutdown)
    calls = 0
    delete_expired = sessions.delete_expired

    async def count_and_stop() -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 3:
            shutdown.set()
        return await delete_expired()

    sessions.delete_expired = count_and_stop  # type: ignore[method-assign]
    run = di.inject(cleanup)
    async with di:
        await run(interval=0.01)

    assert calls == 3
