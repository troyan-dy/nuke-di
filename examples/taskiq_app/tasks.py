from collections.abc import AsyncGenerator
from typing import Annotated

from nuke_di.taskiq import setup
from taskiq import Context, InMemoryBroker, TaskiqDepends

from taskiq_app.clients import Mailer, Reports

# Runs the tasks in this process; a deployment swaps it for the broker of its queue, see README.md
broker = InMemoryBroker()
# Clients connect when the worker starts and disconnect when it stops; a process that only kicks connects nothing
setup(broker)


# The default is for type checkers, which read `.kiq()` against this signature; the client still comes by its type
@broker.task
async def send_report(user_id: int, reports: Reports = TaskiqDepends()) -> str:
    return await reports.send_weekly(user_id)


async def notify_admin(context: Annotated[Context, TaskiqDepends()], mailer: Mailer) -> AsyncGenerator[None, None]:
    # A dependency takes taskiq's objects and clients side by side; the code after `yield` runs after the task
    yield
    await mailer.send("admin", f"{context.message.task_name} finished")


@broker.task
async def send_reports(
    user_ids: list[int], reports: Reports = TaskiqDepends(), _: None = TaskiqDepends(notify_admin)
) -> int:
    for user_id in user_ids:
        await reports.send_weekly(user_id)
    return len(user_ids)
