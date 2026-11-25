"""
How the clients of one connect() or disconnect() take their turns: in dependency order, each step bounded by a timeout.
"""

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from nuke_di.types import Client

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

# The step of one client, run once the clients it waits for are done
_Step: TypeAlias = Callable[[Client], Coroutine[Any, Any, None]]
# The class asked for and the client passed, for one `__init__` argument of a client
_Dependency: TypeAlias = tuple[type[Client], Client]
# The arguments of a client that takes no client
_NO_ARGUMENTS: Final[Mapping[str, _Dependency]] = MappingProxyType({})


class LifecycleCancellation:
    """Distinguish a client's cancellation from cancellation of the lifecycle task group."""

    def __init__(self) -> None:
        self.task = asyncio.current_task()
        self.prior_requests = 0 if self.task is None else self.task.cancelling()

    def group_is_cancelling(self) -> bool:
        # TaskGroup cancels the owner on a sibling failure too. Requests already handled by the owner do not count.
        return self.task is None or self.task.cancelling() > self.prior_requests


async def in_dependency_order(
    clients: list[Client],
    waits_for: dict[int, list[int]],
    step: _Step,
    prior_cancels: int,
) -> None:
    """
    Run `step` for every client of `clients` concurrently, each once `step` has returned for the clients it
    `waits_for`, by `id()`.

    A client that `step` raised for releases no one: in a connect, the consumers of a failed client must not start
    before the group cancels them. The first exception cancels every step still running or waiting.
    """
    # Deliver a pending cancellation before starting clients. An already handled request only leaves a count.
    if prior_cancels:
        await asyncio.sleep(0)
    done = {id(client): asyncio.Event() for client in clients}

    async with asyncio.TaskGroup() as group:
        for client in clients:
            group.create_task(_step_when_ready(client, step, waits_for, done))


async def _step_when_ready(
    client: Client,
    step: _Step,
    waits_for: dict[int, list[int]],
    done: dict[int, asyncio.Event],
) -> None:
    key = id(client)
    for other in waits_for[key]:
        # A barrier on the events of the dependencies: gather would add a Task per edge, and change the order
        await done[other].wait()  # noqa: WPS476
    await step(client)
    done[key].set()


def dependencies_among(container: "Dependencies", clients: list[Client]) -> dict[int, list[int]]:
    """
    The dependencies of every client of `clients` that are among `clients`, by `id()`: a Replacement is never
    connected, so nothing waits for it.
    """
    ids = set(map(id, clients))
    dependencies = container._dependencies
    return {
        id(client): [
            id(dependency)
            for _, dependency in dependencies.get(id(client), _NO_ARGUMENTS).values()
            if id(dependency) in ids
        ]
        for client in clients
    }


async def within(seconds: float, coro: Coroutine[Any, Any, None]) -> None:
    """
    Await a client's coroutine with a timeout, in the task of the caller.

    `asyncio.wait_for()` runs the coroutine in a task of its own on 3.11, about 40 µs per call, and in the caller's
    task only from 3.12. A timeout of zero or less expires before the coroutine starts, as `wait_for()` does;
    `asyncio.timeout(0)` alone would let it run up to its first suspension.
    """
    if seconds <= 0:
        coro.close()
        raise TimeoutError
    async with asyncio.timeout(seconds):
        await coro
