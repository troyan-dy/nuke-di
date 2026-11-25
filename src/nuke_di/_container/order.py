"""
How the clients of one connect() or disconnect() take their turns: in dependency order, each step bounded by a timeout.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, TypeAlias

from nuke_di._container.registry import ClientClass
from nuke_di.types import Client

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

# The step of one client, run once the clients it waits for are done
_Step: TypeAlias = Callable[[ClientClass, Client], Coroutine[Any, Any, None]]


class LifecycleCancellation:
    """Distinguish a client's cancellation from cancellation of the lifecycle task group."""

    def __init__(self) -> None:
        self.task = asyncio.current_task()
        self.prior_requests = 0 if self.task is None else self.task.cancelling()

    def group_is_cancelling(self) -> bool:
        # TaskGroup cancels the owner on a sibling failure too. Requests already handled by the owner do not count.
        return self.task is None or self.task.cancelling() > self.prior_requests


async def in_dependency_order(
    clients: dict[ClientClass, Client],
    waits_for: dict[ClientClass, list[ClientClass]],
    step: _Step,
    prior_cancels: int,
) -> None:
    """
    Run each client after the declared classes it waits for.

    A client that `step` raised for releases no one: in a connect, the consumers of a failed client must not start
    before the group cancels them. The first exception cancels every step still running or waiting.
    """
    # Deliver a pending cancellation before starting clients. An already handled request only leaves a count.
    if prior_cancels:
        await asyncio.sleep(0)
    done = {key: asyncio.Event() for key in clients}

    async with asyncio.TaskGroup() as group:
        for key, client in clients.items():
            group.create_task(_step_when_ready(key, client, step, waits_for, done))


async def _step_when_ready(
    key: ClientClass,
    client: Client,
    step: _Step,
    waits_for: dict[ClientClass, list[ClientClass]],
    done: dict[ClientClass, asyncio.Event],
) -> None:
    for other in waits_for[key]:
        # A barrier on the events of the dependencies: gather would add a Task per edge, and change the order
        await done[other].wait()  # noqa: WPS476
    await step(key, client)
    done[key].set()


def dependencies_among(
    container: "Dependencies",
    clients: dict[ClientClass, Client],
) -> dict[ClientClass, list[ClientClass]]:
    """Keep declared edges between the selected clients, excluding replacements and unstarted clients."""
    registrations = container._clients
    selected = {}
    for key in clients:
        declared = registrations[key].dependencies or ()
        selected[key] = [other for other in declared if other in clients]
    return selected


async def connect_client(
    key: ClientClass,
    client: Client,
    seconds: float,
    connected: dict[ClientClass, Client],
) -> None:
    await within(seconds, client.connect())
    connected[key] = client
    # A hook can request cancellation and return before it next suspends. Roll it back too, rather than
    # releasing its consumers while TaskGroup is still deciding whether the child succeeded.
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        await asyncio.sleep(0)


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
