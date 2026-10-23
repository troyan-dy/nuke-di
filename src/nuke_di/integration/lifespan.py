"""
The container connected for the time an app runs, with the clients its handlers take.
"""

import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from nuke_di.clients import BackgroundTasks, Shutdown
from nuke_di.core import Dependencies
from nuke_di.integration.binding import Binding
from nuke_di.naming import sname


@asynccontextmanager
async def running(container: Dependencies, bindings: list[Binding]) -> AsyncIterator[None]:
    """
    Resolve the clients of `bindings` and connect `container` for the time an app runs.
    """
    _refuse_busy(container, bindings)
    await _start(container, bindings)
    try:
        yield
    finally:
        await _stop(container, bindings)


def wrap_lifespan(
    original: Callable[..., Any],
    container: Dependencies,
    bindings: Callable[[], list[Binding]],
) -> Callable[..., Any]:
    """
    A lifespan that runs `original` inside `container`, connected with the clients `bindings()` finds on
    startup; refuses an app set up already.
    """
    if getattr(original, "__nuke_di__", False):
        raise TypeError("setup() was already called for this app")

    @asynccontextmanager
    async def wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        # The app's own lifespan runs inside, so its startup and shutdown code can use the clients
        async with running(container, bindings()), original(*args, **kwargs) as state:
            yield state

    wrapper.__nuke_di__ = True  # type: ignore[attr-defined]
    return wrapper


def _refuse_busy(container: Dependencies, bindings: list[Binding]) -> None:
    if container.connected:
        # E.g. by another app on the same container; its clients are left as they are
        raise RuntimeError("nuke-di clients failed to start: the container is already connected")
    busy = next((binding for binding in bindings if binding.instance is not None), None)
    if busy is not None:
        # A function bound once, shared with an app that runs: filling it would hand that app our clients
        raise RuntimeError(
            f"nuke-di clients failed to start: {sname(busy.client_class)} is filled for another app that is running; "
            f"apps that share a handler function run one at a time"
        )


async def _start(container: Dependencies, bindings: list[Binding]) -> None:
    # A failed resolution would leave the timings of an earlier connect
    container.timings = []
    try:
        await _connect(container, bindings)
    except BaseException as exc:
        _forget(bindings)
        # A failed connect() has already flushed the container, a failed resolution has not
        container.flush()
        if isinstance(exc, SystemExit):
            # ConnectError and InitializeDependencyError are SystemExit, which escapes the event loop of the
            # server; a plain error lets it report a failed startup and exit
            raise RuntimeError(f"nuke-di clients failed to start: {exc}") from exc
        raise


async def _connect(container: Dependencies, bindings: list[Binding]) -> None:
    for binding in bindings:
        binding.instance = container.resolve(binding.client_class)
        binding.container = weakref.ref(container)
    await container.connect()


async def _stop(container: Dependencies, bindings: list[Binding]) -> None:
    # The same order as a Run: stop the loops, then the tasks that use the clients, then the clients
    shutdown = container.clients.get(Shutdown)
    if isinstance(shutdown, Shutdown):
        shutdown.set()
    tasks = container.clients.get(BackgroundTasks)
    if isinstance(tasks, BackgroundTasks):
        await tasks.stop()
    try:
        await container.disconnect()
    except BaseException:
        _forget(bindings)
        raise
    _forget(bindings)


def _forget(bindings: list[Binding]) -> None:
    for binding in bindings:
        binding.instance = None
