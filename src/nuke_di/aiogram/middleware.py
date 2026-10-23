"""
The inner middleware that fills the client arguments of the handler that matched an update, and what the
startup found for it.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject

from nuke_di.aiogram.framework import _AIOGRAM, _RESERVED, Arguments
from nuke_di.aiogram.signatures import _client_arguments
from nuke_di.core import Dependencies
from nuke_di.integration import Binding
from nuke_di.naming import sname
from nuke_di.types import NotSingletonClient


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
        # Between a startup that connected the container and its shutdown
        self.started = False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],  # noqa: WPS110
    ) -> Any:
        callback = data["handler"].callback
        arguments = self._arguments_of(callback)
        if not arguments:
            return await handler(event, data)
        return await handler(event, self._fill(data, arguments, callback))

    def late(self, binding: Binding) -> NotSingletonClient:
        """
        The client of a handler the startup did not see: connected if another client depends on it.
        """
        if self.started:
            instance = self.container.clients.get(binding.client_class)
            if instance is not None:
                return instance
        return binding.require()

    def shutdown_arguments(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        The arguments of a shutdown, which leave the names of the clients to nuke-di, with the clients of the
        startup and shutdown handlers.
        """
        taken = sorted(kwargs.keys() & self.found.lifecycle.keys())
        if taken:
            name = taken[0]
            client = self.found.lifecycle[name].client_class
            raise TypeError(
                f'"{name}" is passed to emit_shutdown(), but it is the client '
                f"{sname(client)} of a startup or shutdown handler: rename the "
                f"argument or the key"
            )
        return {**kwargs, **self.lifecycle()}

    def lifecycle(self) -> dict[str, NotSingletonClient]:
        """
        The client arguments of the startup and shutdown handlers.
        """
        return {name: binding.require() for name, binding in self.found.lifecycle.items()}

    def _arguments_of(self, callback: Any) -> Arguments:
        found = self.found
        # By identity: a callable object may not be hashable; the entry keeps the callback, so its id stays its own
        entry = found.arguments.get(id(callback))
        if entry is None or entry[0] is not callback:
            # A handler registered after startup, or an update fed before it
            entry = (callback, found.of(callback))
            found.arguments[id(callback)] = entry
        return entry[1]

    def _fill(
        self,
        update_data: dict[str, Any],
        arguments: Arguments,
        callback: Any,
    ) -> dict[str, Any]:
        # A copy: aiogram passes the same data to the next handler when this one raises SkipHandler
        filled = dict(update_data)
        for name, binding in arguments:
            if name in filled:
                raise TypeError(
                    f'"{name}" is in the data of the update already, and {sname(callback)} takes the client '
                    f"{sname(binding.client_class)} under that name: a filter, a middleware or feed_update() passed "
                    f"it; rename the argument or the key"
                )
            instance = binding.instance
            filled[name] = self.late(binding) if instance is None else instance
        return filled


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
        # The client arguments of every handler, by the id of its callback, which the entry keeps: the cost of an
        # update is a lookup here
        self.arguments: dict[int, tuple[Any, Arguments]] = {}
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
                binding = Binding(client, self.container, _AIOGRAM)
                self.bindings[client] = binding
            found.append((name, binding))
        return tuple(found)

    def add_lifecycle(self, name: str, binding: Binding) -> None:
        """
        Record a client argument of a startup or shutdown handler: aiogram passes them the same arguments by name.
        """
        known = self.lifecycle.setdefault(name, binding)
        if known is not binding:
            raise TypeError(
                f'Argument "{name}" of the startup and shutdown handlers is '
                f"{sname(known.client_class)} and {sname(binding.client_class)}: aiogram passes them "
                f"the same arguments by name, so give different clients different names"
            )
