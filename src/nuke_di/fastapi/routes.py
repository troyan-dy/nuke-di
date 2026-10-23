"""
The route class and the router that fill client arguments, one route class per container.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from nuke_di.core import DI, Dependencies
from nuke_di.fastapi.bindings import _ContainerRoute
from nuke_di.fastapi.tracking import _include, _track, _WebSocketAdder
from nuke_di.naming import sname

# Where a container keeps its route class: the container is an unhashable dataclass, and keeping it here
# lets both be garbage collected together
_ROUTE_CLASS = "_nuke_di_route_class"


class ClientRoute(_ContainerRoute):
    """
    Route class that fills the arguments typed as clients from the global `DI`.
    """

    container = DI


DI.__dict__[_ROUTE_CLASS] = ClientRoute


def _route_class(container: Dependencies) -> type[_ContainerRoute]:
    route_cls: type[_ContainerRoute] | None = container.__dict__.get(_ROUTE_CLASS)
    # A copy of a container carries the route class of the original in its attributes
    if route_cls is None or route_cls.container is not container:
        route_cls = type("ContainerRoute", (_ContainerRoute,), {"container": container})
        container.__dict__[_ROUTE_CLASS] = route_cls
    return route_cls


class ClientRouter(APIRouter):
    """
    `APIRouter` whose routes and dependencies take clients by type hint, from `container`.

    It also fills the clients of its own `dependencies=` and of the `dependencies=` given to its
    `include_router()`, which FastAPI applies to included routes without their route class.
    """

    def __init__(self, *, container: Dependencies = DI, **kwargs: Any) -> None:
        route_cls = _route_class(container)
        route_class = kwargs.setdefault("route_class", route_cls)
        if not issubclass(route_class, route_cls):
            raise TypeError(
                f"route_class={sname(route_class)} does not fill clients from the container of this router: "
                f"leave it out, or derive it from ClientRoute for DI"
            )
        super().__init__(**kwargs)
        self._route_cls = route_cls
        _track(self, route_cls)

    def include_router(self, router: APIRouter, **kwargs: Any) -> None:
        _include(self, router, kwargs, self._route_cls, "this router")
        super().include_router(router, **kwargs)

    def add_api_websocket_route(self, path: str, endpoint: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        add = _WebSocketAdder(self, super().add_api_websocket_route, self._route_cls)
        add(path, endpoint, *args, **kwargs)
