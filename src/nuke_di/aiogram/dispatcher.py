"""
setup() of a dispatcher: its handlers take clients, and the container runs with its startup and shutdown.
"""

from contextlib import AsyncExitStack
from typing import Any

from aiogram import Dispatcher

from nuke_di.aiogram.middleware import _Clients
from nuke_di.aiogram.scan import find
from nuke_di.core import DI, Dependencies
from nuke_di.integration import running


def setup(dp: Dispatcher, container: Dependencies = DI) -> None:
    """
    Fill the client arguments of the handlers of `dp` and of every router included into it, and run `container`
    with `dp`: connect it before the startup handlers, disconnect it after the shutdown handlers.
    """
    if not isinstance(dp, Dispatcher):
        # A router has no startup of its own, and every router is included into one dispatcher only
        raise TypeError(f"setup() takes the Dispatcher, not {dp!r}: the routers included into it are covered")
    if isinstance(getattr(dp.emit_startup, "__self__", None), _Lifecycle):
        raise TypeError("setup() was already called for this dispatcher")

    clients = _Clients(dp, container)
    # An inner middleware of the dispatcher runs for the handlers of every router below it, included before
    # setup() or after; it is the first place where the handler that matched the update is known
    for observer in dp.observers.values():
        observer.middleware.register(clients)

    lifecycle = _Lifecycle(dp, container, clients)
    dp.emit_startup = lifecycle.startup  # type: ignore[method-assign]
    dp.emit_shutdown = lifecycle.shutdown  # type: ignore[method-assign]


class _Lifecycle:
    """
    The whole startup and shutdown of a dispatcher, not a handler of `dp.startup`: aiogram calls the startup
    handlers of the routers after those of the dispatcher, and their shutdown handlers after the dispatcher's too,
    and leaves the shutdown out when a startup handler fails.
    """

    def __init__(self, dp: Dispatcher, container: Dependencies, clients: _Clients) -> None:
        self.dp = dp
        self.container = container
        self.clients = clients
        self.emit_startup = dp.emit_startup
        self.emit_shutdown = dp.emit_shutdown
        # Entered on startup, closed after the shutdown handlers
        self.stack = AsyncExitStack()

    async def startup(self, *args: Any, **kwargs: Any) -> None:
        found = find(self.dp, self.container, kwargs)
        bindings = list(found.bindings.values())
        await self.stack.enter_async_context(running(self.container, bindings))
        # Only once connected: a dispatcher started twice keeps the clients of the first startup
        self.clients.found = found
        self.clients.started = True
        try:
            await self.emit_startup(*args, **kwargs, **self.clients.lifecycle())
        except BaseException:
            await self.stop()
            raise

    async def shutdown(self, *args: Any, **kwargs: Any) -> None:
        if not self.clients.started:
            # After a failed startup, or a second shutdown: the shutdown handlers run with what aiogram passes
            await self.emit_shutdown(*args, **kwargs)
            return
        try:
            await self.emit_shutdown(*args, **self.clients.shutdown_arguments(kwargs))
        except BaseException:
            await self.stop()
            raise
        await self.stop()

    async def stop(self) -> None:
        self.clients.started = False
        await self.stack.aclose()
