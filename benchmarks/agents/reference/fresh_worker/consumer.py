import asyncio
import itertools

from nuke_di import Client, Shutdown, worker


class Queue(Client):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def get(self) -> str:
        await asyncio.sleep(0.2)
        return f"message-{next(self._ids)}"


@worker
async def consume(queue: Queue, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        print(f"processed {await queue.get()}", flush=True)
