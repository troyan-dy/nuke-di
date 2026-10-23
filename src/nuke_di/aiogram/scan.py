"""
The startup's scan of a dispatcher: the client arguments of the handlers of every router, and the refusal of
the filters and scenes that would take clients.
"""

from collections.abc import Iterable, Iterator
from typing import Any

from aiogram import Dispatcher, Router
from aiogram.fsm.scene import SceneHandlerWrapper

from nuke_di.aiogram.framework import _RESERVED
from nuke_di.aiogram.middleware import _Found
from nuke_di.aiogram.signatures import _client_arguments
from nuke_di.core import Dependencies
from nuke_di.naming import sname


def find(dp: Dispatcher, container: Dependencies, kwargs: dict[str, Any]) -> _Found:
    """
    The client arguments of every handler of the dispatcher and its routers, as they are on startup;
    `kwargs` are the arguments of the startup, which aiogram passes to the handlers too.
    """
    reserved = _RESERVED | dp.workflow_data.keys() | kwargs.keys()
    found = _Found(container, reserved)
    for router in dp.chain_tail:
        _scan_observers(router, found)
        _scan_lifecycle(router, found)
    return found


def _scan_observers(router: Router, found: _Found) -> None:
    for observer in router.observers.values():
        # The filters of the observer itself, `router.message.filter(...)`, which run first
        _refuse_filters(observer._handler.filters)
        for handler in observer.handlers:
            callback = handler.callback
            _refuse_scene(callback)
            found.arguments[id(callback)] = (callback, found.of(callback))
            _refuse_filters(handler.filters)


def _scan_lifecycle(router: Router, found: _Found) -> None:
    for handler in (*router.startup.handlers, *router.shutdown.handlers):
        for name, binding in found.of(handler.callback):
            found.add_lifecycle(name, binding)


def _refuse_filters(filters: Iterable[Any] | None) -> None:
    for each in filters or ():
        clients = _client_arguments(each.callback)
        if clients:
            name, client = clients[0]
            raise TypeError(
                f'Argument "{name}" of the filter {sname(each.callback)} is {sname(client)}: aiogram calls filters '
                f"before the middlewares that fill clients, so a filter takes no clients; check it in the handler "
                f"instead"
            )


def _refuse_scene(call: Any) -> None:
    """
    Refuse a scene whose handlers take clients: aiogram calls the handlers of a scene's actions, such as
    `on.message.enter()`, from its own machinery, which no middleware of nuke-di reaches.
    """
    if not isinstance(call, SceneHandlerWrapper):
        return
    for handler in _scene_handlers(call.scene.__scene_config__):
        clients = _client_arguments(handler)
        if clients:
            name, client = clients[0]
            where = handler.__qualname__.rsplit("<locals>.", 1)[-1]
            raise TypeError(
                f'Argument "{name}" of the scene handler {where} is {sname(client)}: aiogram calls '
                f"the handlers of a scene from its own machinery, so a scene takes no clients; pass what it needs "
                f"from a handler outside it, e.g. `await scenes.enter({sname(call.scene)}, ...)`"
            )


def _scene_handlers(config: Any) -> Iterator[Any]:
    """
    The handlers of a scene, then the callbacks of its actions.
    """
    yield from (handler.handler for handler in config.handlers)
    for actions in config.actions.values():
        yield from (action.callback for action in actions.values())
