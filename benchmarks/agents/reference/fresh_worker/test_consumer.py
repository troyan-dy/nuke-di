from consumer import Queue, consume

from nuke_di import Dependencies, Shutdown


async def test_consume(di: Dependencies) -> None:
    shutdown = di.resolve(Shutdown)
    queue = di.mock(Queue)

    async def last() -> str:
        shutdown.set()
        return "m1"

    queue.get.side_effect = last
    injected = di.inject(consume)
    async with di:
        await injected()
    queue.get.assert_awaited_once()
