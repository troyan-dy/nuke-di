import asyncio

from nuke_di import entrypoint

from sqlite_queue.clients import Task, TaskQueue


async def process(task: Task) -> None:
    print(f"consumer: processing task {task.id} ({task.payload})")
    await asyncio.sleep(0.5)  # stand-in for the work


@entrypoint
async def consume(queue: TaskQueue) -> None:
    """Process pending tasks and exit when the queue is empty."""
    while True:
        task = await queue.claim()
        if task is None:
            print("consumer: queue is empty")
            return
        await process(task)
        await queue.done(task.id)
        print(f"consumer: done task {task.id}")
