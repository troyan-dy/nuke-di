import asyncio
import itertools
import logging
from typing import Annotated

from nuke_di import BackgroundTasks, Option, Shutdown, worker

from background_tasks.clients import Cache, Inbox

# Above the decorator, so the records of nuke_di, a failed background task included, are shown
logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")


async def heartbeat(inbox: Inbox, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        print(f"heartbeat: alive, {inbox.received} messages received")


async def refresh_cache(cache: Cache, interval: float, fail_after: int | None = None) -> None:
    for attempt in itertools.count(1):
        await asyncio.sleep(interval)
        if fail_after is not None and attempt > fail_after:
            raise ConnectionError("the settings service is unreachable")
        await cache.refresh()
        print(f"refresh: cache v{cache.version}")


@worker
async def process(
    inbox: Inbox,
    cache: Cache,
    tasks: BackgroundTasks,
    shutdown: Shutdown,
    fail_after: Annotated[int | None, Option(help="Make the cache refresh fail after N refreshes")] = None,
) -> None:
    """Process the inbox while a heartbeat and a cache refresh run in the background."""
    tasks.spawn(heartbeat(inbox, interval=1.0), name="heartbeat")
    tasks.spawn(refresh_cache(cache, interval=1.25, fail_after=fail_after), name="refresh-cache")
    while not shutdown.is_set():
        message = await inbox.get()
        print(f"worker: {message} with cache v{cache.version}")
        await asyncio.sleep(0.7)  # the actual work
    print("worker: stopped")
