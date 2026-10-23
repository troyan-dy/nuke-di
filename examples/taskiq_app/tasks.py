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
async def send_report(user_id: int, reports: Reports = TaskiqDepends()) -> str:  # noqa: WPS404 - see above
    return await reports.send_weekly(user_id)


async def notify_admin(context: Annotated[Context, TaskiqDepends()], mailer: Mailer) -> AsyncGenerator[None, None]:
    # A dependency takes taskiq's objects and clients side by side; the code after `yield` runs after the task
    yield
    task_name = context.message.task_name
    await mailer.send("admin", f"{task_name} finished")


@broker.task
async def send_reports(
    user_ids: list[int],
    reports: Reports = TaskiqDepends(),  # noqa: WPS404 - for type checkers, as send_report
    _: None = TaskiqDepends(notify_admin),  # noqa: WPS404 - a dependency of taskiq's own, which taskiq reads from defaults
) -> int:
    for user_id in user_ids:
        await reports.send_weekly(user_id)
    return len(user_ids)
