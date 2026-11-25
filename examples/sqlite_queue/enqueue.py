from nuke_di import entrypoint

from sqlite_queue.clients import TaskQueue


@entrypoint
async def enqueue(queue: TaskQueue, count: int = 3, kind: str = "email") -> None:
    """Add tasks to the queue."""
    for number in range(1, count + 1):
        task_id = await queue.put(kind, f"{kind}-{number}")
        print(f"enqueue: added task {task_id} ({kind}-{number})")
    pending = await queue.pending()
    print(f"enqueue: {pending} tasks pending")
