from typing import Annotated

from nuke_di import Option, job

from sqlite_queue.clients import TaskQueue


@job
async def enqueue(
    queue: TaskQueue,
    count: Annotated[int, Option(help="How many tasks to add", short="n")] = 3,
    kind: Annotated[str, Option(help="The kind of the tasks")] = "email",
) -> None:
    """Add tasks to the queue."""
    for number in range(1, count + 1):
        task_id = await queue.put(kind, f"{kind}-{number}")
        print(f"enqueue: added task {task_id} ({kind}-{number})")
    pending = await queue.pending()
    print(f"enqueue: {pending} tasks pending")
