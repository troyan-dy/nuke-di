import asyncio
import itertools

from nuke_di import Client, Shutdown, worker

# A new message every this many seconds
INTERVAL_SECONDS = 0.2


class Queue(Client):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def get(self) -> str:
        await asyncio.sleep(INTERVAL_SECONDS)
        return f"message-{next(self._ids)}"


@worker
async def consume(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        message = await queue.get()
        print(f"processed {message}", flush=True)
