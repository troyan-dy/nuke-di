"""
aiogram integration: handlers take clients by type hint, and the dispatcher runs the container: the clients
connect before its startup handlers and disconnect after its shutdown handlers.

See docs/specs/aiogram.md.
"""

import inspect
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any, get_type_hints

from aiogram import Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject

from nuke_di.core import DI, Dependencies
from nuke_di.integration import Binding, Framework, client_of, running
from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname

__all__ = ("setup",)

# aiogram has no `Depends`: it passes a handler the items of its middleware data by name, see `_Clients`
_AIOGRAM = Framework(
    name="aiogram",
    not_started="{client} was not started with the dispatcher: register its handler before the dispatcher starts",
    not_connected=(
        "{client} is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test"
    ),
)

# What aiogram itself puts into the data of an update or the arguments of a startup handler: a client under
# one of these names would hide it from the handler and from the middlewares after nuke-di's
_RESERVED = frozenset(
    {
        "bot",
        "bots",
        "dispatcher",
        "router",
        "event_router",
        "event_update",
        "handler",
        "event_context",
        "event_from_user",
        "event_chat",
        "event_thread_id",
        "event_business_connection_id",
        "fsm_storage",
        "state",
        "raw_state",
    }
)

# The client arguments of one function, with the binding that fills each
Arguments = tuple[tuple[str, Binding], ...]


def setup(dp: Dispatcher, container: Dependencies = DI) -> None:
    """
    Fill the client arguments of the handlers of `dp` and of every router included into it, and run `container`
    with `dp`: connect it before the startup handlers, disconnect it after the shutdown handlers.
    """
    if not isinstance(dp, Dispatcher):
        # A router has no startup of its own, and every router is included into one dispatcher only
        raise TypeError(f"setup() takes the Dispatcher, not {dp!r}: the routers included into it are covered")
    if getattr(dp.emit_startup, "__nuke_di__", False):
        raise TypeError("setup() was already called for this dispatcher")

    clients = _Clients(dp, container)
    # An inner middleware of the dispatcher runs for the handlers of every router below it, included before
    # setup() or after; it is the first place where the handler that matched the update is known
    for observer in dp.observers.values():
        observer.middleware.register(clients)

    emit_startup, emit_shutdown = dp.emit_startup, dp.emit_shutdown
    # Entered on startup, closed after the shutdown handlers
    stack = AsyncExitStack()

    # The whole startup and shutdown, not a handler of `dp.startup`: aiogram calls the startup handlers of the
    # routers after those of the dispatcher, and their shutdown handlers after the dispatcher's too, and leaves
    # the shutdown out when a startup handler fails
    async def startup(*args: Any, **kwargs: Any) -> None:
        found = clients.find(kwargs)
        await stack.enter_async_context(running(container, list(found.bindings.values())))
        # Only once connected: a dispatcher started twice keeps the clients of the first startup
        clients.found = found
        try:
            await emit_startup(*args, **kwargs, **await clients.lifecycle())
        except BaseException:
            await stack.aclose()
            raise

    async def shutdown(*args: Any, **kwargs: Any) -> None:
        try:
            await emit_shutdown(*args, **kwargs, **await clients.lifecycle())
        finally:
            await stack.aclose()

    startup.__nuke_di__ = True  # type: ignore[attr-defined]
    dp.emit_startup = startup  # type: ignore[method-assign]
    dp.emit_shutdown = shutdown  # type: ignore[method-assign]


class _Clients(BaseMiddleware):
    """
    Fills the client arguments of the handler that matched an update into the data of the update, by name, as
    aiogram passes everything else.
    """

    def __init__(self, dp: Dispatcher, container: Dependencies) -> None:
        self.dp = dp
        self.container = container
        # What the last startup found; before the first one, every handler is found as it is first called
        self.found = _Found(container, _RESERVED)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        found = self.found
        callback = data["handler"].callback
        arguments = found.arguments.get(callback)
        if arguments is None:
            # A handler registered after startup, or an update fed before it: it raises "not started" or
            # "not connected" below
            arguments = found.arguments[callback] = found.of(callback)
        for name, binding in arguments:
            instance = binding.instance
            data[name] = instance if instance is not None else await binding.get()
        return await handler(event, data)

    def find(self, kwargs: dict[str, Any]) -> "_Found":
        """
        The client arguments of every handler of the dispatcher and its routers, as they are on startup;
        `kwargs` are the arguments of the startup, which aiogram passes to the handlers too.
        """
        found = _Found(self.container, _RESERVED | self.dp.workflow_data.keys() | kwargs.keys())
        for router in self.dp.chain_tail:
            for observer in router.observers.values():
                for handler in observer.handlers:
                    found.arguments[handler.callback] = found.of(handler.callback)
                    for item in handler.filters or ():
                        _refuse_filter(item.callback)
            for handler in [*router.startup.handlers, *router.shutdown.handlers]:
                for name, binding in found.of(handler.callback):
                    known = found.lifecycle.setdefault(name, binding)
                    if known is not binding:
                        raise TypeError(
                            f'Argument "{name}" of the startup and shutdown handlers is {sname(known.cls)} and '
                            f"{sname(binding.cls)}: aiogram passes them the same arguments by name, so give "
                            f"different clients different names"
                        )
        return found

    async def lifecycle(self) -> dict[str, NotSingletonClient]:
        """
        The client arguments of the startup and shutdown handlers.
        """
        return {name: await binding.get() for name, binding in self.found.lifecycle.items()}


class _Found:
    """
    The client arguments of a dispatcher's handlers, as one startup found them.
    """

    def __init__(self, container: Dependencies, reserved: frozenset[str]) -> None:
        self.container = container
        # The names aiogram passes itself
        self.reserved = reserved
        # One binding per client class
        self.bindings: dict[type[NotSingletonClient], Binding] = {}
        # The client arguments of every handler, by its callback: the cost of an update is a lookup here
        self.arguments: dict[Any, Arguments] = {}
        # The client arguments of the startup and shutdown handlers, by name: aiogram passes them all the same
        # arguments
        self.lifecycle: dict[str, Binding] = {}

    def of(self, call: Any) -> Arguments:
        """
        The client arguments of `call`, each with the binding of its client.
        """
        found = []
        for name, client in _client_arguments(call):
            if name in self.reserved:
                raise TypeError(
                    f'Argument "{name}" of {sname(call)} is {sname(client)}, but aiogram passes "{name}" to '
                    f"handlers itself: rename the argument"
                )
            binding = self.bindings.get(client)
            if binding is None:
                binding = self.bindings[client] = Binding(client, self.container, _AIOGRAM)
            found.append((name, binding))
        return tuple(found)


def _client_arguments(call: Any) -> list[tuple[str, type[NotSingletonClient]]]:
    """
    The arguments of `call` whose type hint is a client, as aiogram reads them: through `functools.wraps`.
    """
    target = inspect.unwrap(call)
    function: Any
    if inspect.isfunction(target):
        function = target
    elif inspect.ismethod(target):
        function = target.__func__
    elif not inspect.isclass(target) and callable(target):
        # A `Filter` object
        function = type(target).__call__
    else:
        # aiogram's class-based handlers take what they need from `self.data`
        return []
    try:
        hints = get_type_hints(function, include_extras=True)
        parameters = inspect.signature(target).parameters
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: aiogram does not read type hints, it passes by name
        return []
    return [(name, client) for name in parameters if (client := client_of(hints.get(name))) is not None]


def _refuse_filter(call: Any) -> None:
    found = _client_arguments(call)
    if found:
        name, client = found[0]
        raise TypeError(
            f'Argument "{name}" of the filter {sname(call)} is {sname(client)}: aiogram calls filters before '
            f"the middlewares that fill clients, so a filter takes no clients; check it in the handler instead"
        )
