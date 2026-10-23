"""
How the clients of one connect() or disconnect() take their turns: in dependency order, a few at a time, each step
bounded by a timeout and measured.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine, Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from nuke_di.options import DependenciesSettings
from nuke_di.timings import ClientTiming, Outcome
from nuke_di.types import NotSingletonClient

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

Limiter: TypeAlias = asyncio.Semaphore | contextlib.nullcontext[None]
# The step of one client, run once the clients it waits for are done
_Step: TypeAlias = Callable[[NotSingletonClient], Coroutine[Any, Any, None]]
# The class asked for and the client passed, for one `__init__` argument of a client
_Dependency: TypeAlias = tuple[type[NotSingletonClient], NotSingletonClient]
# The arguments of a client that takes no client
_NO_ARGUMENTS: Final[Mapping[str, _Dependency]] = MappingProxyType({})


async def in_dependency_order(
    clients: list[NotSingletonClient],
    waits_for: dict[int, list[int]],
    step: _Step,
) -> None:
    """
    Run `step` for every client of `clients` concurrently, each once `step` has returned for the clients it
    `waits_for`, by `id()`.

    A client that `step` raised for releases no one: in a connect, the consumers of a failed client must not start
    before the group cancels them. The first exception cancels every step still running or waiting.
    """
    done = {id(client): asyncio.Event() for client in clients}

    async with asyncio.TaskGroup() as group:
        for client in clients:
            group.create_task(_step_when_ready(client, step, waits_for, done))


async def _step_when_ready(
    client: NotSingletonClient,
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


def dependencies_among(container: "Dependencies", clients: list[NotSingletonClient]) -> dict[int, list[int]]:
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


def make_limiter(settings: DependenciesSettings) -> Limiter:
    if settings.connect_concurrency == 0:
        return contextlib.nullcontext()
    return asyncio.Semaphore(settings.connect_concurrency)


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


def cancelled_from_outside() -> bool:
    """
    Whether the task is being cancelled, as opposed to a client raising a CancelledError of its own, e.g. re-raised
    from a task it awaited.
    """
    task = asyncio.current_task()
    return task is None or task.cancelling() > 0


@contextlib.contextmanager
def measure(
    timing: ClientTiming,
    phase: Literal["connect", "disconnect"],
    *,
    done: list[Any] | None = None,
    client: object = None,
) -> Iterator[None]:
    """
    Record how long the block took and how it ended as the `phase` of `timing`, then append `client` to `done`.
    """
    started = time.perf_counter()
    outcome: Outcome = "failed"
    try:
        yield
    except TimeoutError:
        outcome = "timed_out"
        raise
    except asyncio.CancelledError:
        # Another client failed, or the whole connect or disconnect was cancelled
        outcome = "cancelled"
        raise
    else:
        outcome = "ok"
    finally:
        # Both fields in one statement: a third one in this finally would take a helper, a call per client
        timing.__dict__.update(
            {
                phase: time.perf_counter() - started,
                f"{phase}_outcome": outcome,
            }
        )
        # However the block ended, and once its timing is recorded
        if done is not None:
            done.append(client)
