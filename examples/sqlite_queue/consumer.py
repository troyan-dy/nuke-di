import asyncio
import contextlib
from typing import Annotated

from nuke_di import Option, Shutdown, worker

from sqlite_queue.clients import Task, TaskQueue


async def process(task: Task) -> None:
    print(f"consumer: processing task {task.id} ({task.payload})")
    await asyncio.sleep(0.5)  # the actual work


@worker
async def consume(
    queue: TaskQueue,
    shutdown: Shutdown,
    poll_interval: Annotated[float, Option(help="Seconds to wait when the queue is empty")] = 2.0,
) -> None:
    """Process the tasks of the queue until SIGTERM or SIGINT."""
    while not shutdown.is_set():
        task = await queue.claim()
        if task is None:
            print("consumer: queue is empty, waiting")
            # Sleep until the next poll, but wake up at once on Shutdown
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), timeout=poll_interval)
            continue
        await process(task)
        await queue.done(task.id)
        print(f"consumer: done task {task.id}")
    print("consumer: stopped")
