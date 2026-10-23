"""
What the routers of an app add to the routes they include, tracked for the app's startup to resolve: FastAPI
builds websocket routes and applies the `dependencies=` of routers without the route class.
"""

from collections.abc import Callable
from typing import Any, TypeAlias

from fastapi import APIRouter

from nuke_di.fastapi.bindings import _bind_dependencies, _bind_route, _ContainerRoute
from nuke_di.integration import Binding, unique

# An included router with the clients of the dependencies given to include_router()
_Include: TypeAlias = tuple[APIRouter, list[Binding]]


class _Tracked:
    """
    What a router adds to the routes it includes, kept on the router; FastAPI 0.14x applies it lazily, out
    of reach of the route class, and only nuke-di knows which routers an app includes.
    """

    def __init__(self, dependencies: list[Binding]) -> None:
        # The clients of the router's own dependencies, applied to the routes of the routers it includes
        self.dependencies = dependencies
        # Each included router with the clients of the dependencies given to include_router()
        self.includes: list[_Include] = []


class _WebSocketAdder:
    """
    `add_api_websocket_route()` of a router that records the clients of the route it adds.
    """

    def __init__(self, router: APIRouter, add: Callable[..., None], route_cls: type[_ContainerRoute]) -> None:
        self.router = router
        self.add = add
        self.route_cls = route_cls

    def __call__(self, path: str, endpoint: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        # Before FastAPI reads the signatures, which happens in APIWebSocketRoute.__init__; the dependencies of
        # the router itself are tracked already
        bindings = _bind_route(endpoint, kwargs, self.route_cls)
        self.add(path, endpoint, *args, **kwargs)
        # The route FastAPI has just appended
        self.router.routes[-1].nuke_di_bindings = bindings  # type: ignore[attr-defined]


def _track(router: APIRouter, route_cls: type[_ContainerRoute]) -> None:
    bindings = _bind_dependencies(router.dependencies, route_cls)
    router.nuke_di_tracked = _Tracked(bindings)  # type: ignore[attr-defined]


def _include(
    owner: APIRouter,
    router: APIRouter,
    kwargs: dict[str, Any],
    route_cls: type[_ContainerRoute],
    name: str,
) -> None:
    other = router.route_class
    if issubclass(other, _ContainerRoute) and other.container is not route_cls.container:
        # Its clients would never be resolved: the startup of this container does not know them
        raise TypeError(f"the router fills clients from another container than {name}")
    bindings = _bind_dependencies(kwargs.get("dependencies") or (), route_cls)
    owner.nuke_di_tracked.includes.append((router, bindings))  # type: ignore[attr-defined]


def _include_router(
    owner: APIRouter,
    include_router: Callable[..., None],
    route_cls: type[_ContainerRoute],
    router: APIRouter,
    **kwargs: Any,
) -> None:
    """
    `include_router()` of the router of an app set up by setup(), with `include_router` the original.
    """
    _include(owner, router, kwargs, route_cls, "this app")
    include_router(router, **kwargs)


def _router_bindings(router: APIRouter) -> list[Binding]:
    """
    The clients of every route an app serves through `router`, each once.
    """
    found: list[Binding] = []
    _collect(router, found)
    return unique(found)


def _collect(router: APIRouter, found: list[Binding]) -> None:
    for route in router.routes:
        found.extend(getattr(route, "nuke_di_bindings", ()))
    tracked: _Tracked | None = getattr(router, "nuke_di_tracked", None)
    if tracked is None:
        return
    found.extend(tracked.dependencies)
    for included, bindings in tracked.includes:
        found.extend(bindings)
        _collect(included, found)
