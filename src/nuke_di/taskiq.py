"""
taskiq integration: tasks and their dependencies take clients by type hint, and the worker runs the container.

See docs/specs/taskiq.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

import inspect
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from taskiq import AsyncBroker, TaskiqDepends, TaskiqEvents, TaskiqState

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, client_of, running, unique

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
    # A worker reads the signatures of its tasks once, when it builds its receiver before it starts; an
    # InMemoryBroker reads a task's on its first run, which may come while another broker runs the same function:
    # the signature must not change from one container to the next
    per_container=False,
)


def setup(broker: AsyncBroker, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the tasks of `broker` and of their dependencies, and run `container` with the worker:
    connect it on the worker's startup, before the other startup handlers, and disconnect it once the broker has
    shut down. A process that only kicks tasks connects nothing.
    """
    if getattr(broker, "__nuke_di__", False):
        raise TypeError("setup() was already called for this broker")
    tasks = broker.get_all_tasks()
    # The tasks declared so far, shared tasks included; a worker reads their signatures before it starts
    for task in tasks.values():
        _rewrite(task.original_func, container)
    receiver = getattr(broker, "receiver", None)
    if receiver is not None:
        # An InMemoryBroker builds its receiver when it is made, which reads the shared tasks there were then;
        # forgotten, they are read again, rewritten, on their first run
        receiver.known_tasks.difference_update(tasks)
    broker.task = _Task(broker.task, container)  # type: ignore[method-assign]
    worker = _Worker(broker, container)
    # First, so the startup handlers registered before setup() see the clients too
    broker.event_handlers[TaskiqEvents.WORKER_STARTUP].insert(0, worker.startup)
    broker.shutdown = worker  # type: ignore[method-assign]
    broker.__nuke_di__ = True  # type: ignore[attr-defined]


def _rewrite(func: Callable[..., Any], container: Dependencies) -> list[Binding]:
    _refuse_client_classes(func, set())
    return bind(func, container, _TASKIQ)


def _refuse_client_classes(call: Any, seen: set[int]) -> None:
    """
    Raise for a dependency class whose `__init__` takes clients, at any depth: taskiq builds it from that
    `__init__`, which `bind()` does not rewrite, so the task would fail on every run.
    """
    if id(call) in seen:
        return
    seen.add(id(call))
    init = call.__init__ if inspect.isclass(call) else call
    try:
        hints = get_type_hints(init, include_extras=True)
        parameters = inspect.signature(init).parameters.values()
    except (NameError, TypeError, ValueError):
        # Left to bind() and taskiq, which report what they cannot read
        return
    for param in parameters:
        hint = hints.get(param.name, param.annotation)
        if client_of(hint, _TASKIQ.depends) is not None:
            if inspect.isclass(call):
                raise TypeError(
                    f"{call.__qualname__} takes clients in __init__ and is a taskiq dependency: taskiq builds it from "
                    f"its own __init__, which nuke-di does not rewrite; take it by type hint without TaskiqDepends() "
                    f"if it is a client, or take its clients in a dependency function"
                )
            continue
        marker = _marker(param, hint)
        if marker is not None:
            dependency = marker.dependency or (get_args(hint)[0] if get_origin(hint) is Annotated else hint)
            if inspect.isclass(dependency) or inspect.isfunction(dependency):
                _refuse_client_classes(dependency, seen)


def _marker(param: inspect.Parameter, hint: Any) -> Any:
    if isinstance(param.default, _TASKIQ.depends):
        return param.default
    if get_origin(hint) is Annotated:
        return next((item for item in reversed(get_args(hint)[1:]) if isinstance(item, _TASKIQ.depends)), None)
    return None


class _Task:
    """
    `broker.task`, which rewrites the function of every task declared after setup() before taskiq registers it:
    `register_task()` goes through it too.
    """

    def __init__(self, task: Callable[..., Any], container: Dependencies) -> None:
        self.original = task
        self.container = container

    def __call__(self, task_name: Any = None, **labels: Any) -> Any:
        if callable(task_name):
            # `@broker.task` without parentheses
            _rewrite(task_name, self.container)
            return self.original(task_name, **labels)
        register = self.original(task_name, **labels)

        def decorator(func: Callable[..., Any]) -> Any:
            _rewrite(func, self.container)
            return register(func)

        return decorator


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
            wait_all = getattr(self.broker, "wait_all", None)
            if self.stack is not None and wait_all is not None:
                # An InMemoryBroker runs a kicked task in the background, and its shutdown does not wait for it
                await wait_all()
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
            bindings += _rewrite(task.original_func, self.container)
        return unique(bindings)
