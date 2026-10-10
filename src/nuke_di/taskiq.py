"""
taskiq integration: tasks and their dependencies take clients by type hint, and the worker runs the container.

See docs/specs/taskiq.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

from taskiq import AsyncBroker, TaskiqDepends, TaskiqEvents, TaskiqState

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, running, unique

__all__ = ("setup",)


_TASKIQ = DependsFramework(
    name="taskiq",
    # The marker of taskiq-dependencies, which taskiq re-exports as TaskiqDepends
    depends=type(TaskiqDepends()),
    make_depends=TaskiqDepends,
    not_started=(
        "{client} was not started with the worker: register its task on the broker, or import the module that "
        "declares it, before the worker starts"
    ),
    not_connected=(
        "{client} is not connected: the clients connect when the worker starts; run tasks with `taskiq worker`, "
        "or start an InMemoryBroker with `await broker.startup()` before kicking them"
    ),
    # A worker reads a task's signature when it builds its receiver, and an InMemoryBroker on the first run of the
    # task, possibly after another broker has started: the signature must not change from one container to the next
    per_container=False,
)


def setup(broker: AsyncBroker, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the tasks of `broker` and of their dependencies, and run `container` with the worker:
    connect it on the worker's startup, before the other startup handlers, and disconnect it once the broker has
    shut down. A process that only kicks tasks connects nothing.
    """
    if isinstance(vars(broker).get("shutdown"), _Worker):
        raise TypeError("setup() was already called for this broker")
    worker = _Worker(broker, container)
    # The tasks declared so far, shared tasks included; a worker reads their signatures before it starts
    for task in broker.get_all_tasks().values():
        bind(task.original_func, container, _TASKIQ)
    broker.task = _Task(broker.task, container)  # type: ignore[method-assign]
    # First, so the startup handlers registered before setup() see the clients too
    broker.event_handlers[TaskiqEvents.WORKER_STARTUP].insert(0, worker.startup)
    broker.shutdown = worker  # type: ignore[method-assign]


class _Task:
    """
    `broker.task`, which rewrites the function of every task declared after setup(): `register_task()` goes
    through it too.
    """

    def __init__(self, task: Callable[..., Any], container: Dependencies) -> None:
        self.original = task
        self.container = container

    def __call__(self, task_name: Any = None, **labels: Any) -> Any:
        made = self.original(task_name, **labels)
        if callable(task_name):
            # `@broker.task` without parentheses: taskiq has registered the task already
            return self._bind(made)
        return lambda func: self._bind(made(func))

    def _bind(self, task: Any) -> Any:
        bind(task.original_func, self.container, _TASKIQ)
        return task


class _Worker:
    """
    The container's run in a worker: entered on the WORKER_STARTUP event, which only a worker process and an
    InMemoryBroker fire, and left when `broker.shutdown()` returns, after the shutdown handlers, the middlewares and
    the result backend have stopped. It stands in for `broker.shutdown`.
    """

    def __init__(self, broker: AsyncBroker, container: Dependencies) -> None:
        self.broker = broker
        self.container = container
        self.shutdown = broker.shutdown
        self.stack: AsyncExitStack | None = None

    async def startup(self, state: TaskiqState) -> None:
        stack = AsyncExitStack()
        # A failed start raises out of broker.startup(), which fails the worker; so does a second start before a
        # shutdown, since the container is connected already
        await stack.enter_async_context(running(self.container, self._bindings()))
        self.stack = stack

    async def __call__(self) -> None:
        try:
            await self.shutdown()
        finally:
            stack, self.stack = self.stack, None
            if stack is not None:
                await stack.aclose()

    def _bindings(self) -> list[Binding]:
        """
        The clients of every task the broker serves and of the dependencies they use, each once.
        """
        bindings: list[Binding] = []
        for task in self.broker.get_all_tasks().values():
            bindings += bind(task.original_func, self.container, _TASKIQ)
        return unique(bindings)
