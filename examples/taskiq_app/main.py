import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from taskiq import AsyncBroker
from taskiq.decor import AsyncTaskiqDecoratedTask

from taskiq_app.tasks import broker, send_report, send_reports


@asynccontextmanager
async def running(app_broker: AsyncBroker) -> AsyncIterator[None]:
    # An InMemoryBroker is its own worker: its startup connects the clients, its shutdown disconnects them
    await app_broker.startup()
    try:
        yield
    finally:
        await app_broker.shutdown()


async def result_of(task: AsyncTaskiqDecoratedTask[..., Any], *args: object) -> Any:
    """
    What `task` returns for `args`: kicked, then its result awaited.
    """
    kicked = await task.kiq(*args)
    task_result = await kicked.wait_result()
    return task_result.return_value


async def main() -> None:
    async with running(broker):
        print("send_report(1): sent to", await result_of(send_report, 1))
        print("send_reports([2, 3]):", await result_of(send_reports, [2, 3]), "reports")


if __name__ == "__main__":
    asyncio.run(main())
