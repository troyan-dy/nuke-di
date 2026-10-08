"""
FastAPI integration: path operations and their dependencies take clients by type hint.

See docs/specs/fastapi.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

from collections.abc import Callable
from typing import Any, ClassVar

from fastapi import APIRouter, Depends, FastAPI, params
from fastapi.routing import APIRoute

from nuke_di._integration import Binding, DependsFramework, bind, unique, wrap_lifespan
from nuke_di.core import DI, Dependencies
from nuke_di.utils import sname

__all__ = ("ClientRoute", "ClientRouter", "setup")

# Where a container keeps its route class: the container is an unhashable dataclass, and keeping it here
# lets both be garbage collected together
_ROUTE_CLASS = "_nuke_di_route_class"


_FASTAPI = DependsFramework(
    depends=params.Depends,
    make_depends=Depends,
    not_started=(
        "{client} was not started with the app: include the router of its route into the app "
        "or into a ClientRouter, not into a plain APIRouter"
    ),
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `with TestClient(app)`",
)


class _ContainerRoute(APIRoute):
    """
    Route class that fills the arguments typed as clients from `container`; one subclass per container.
    """

    container: ClassVar[Dependencies]

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        # Before FastAPI reads the signatures, which happens in APIRoute.__init__
        route_cls = type(self)
        self.nuke_di_bindings = _bind(endpoint, route_cls)
        for depends in kwargs.get("dependencies") or ():
            self.nuke_di_bindings += _bind(depends.dependency, route_cls)
        super().__init__(path, endpoint, **kwargs)


class ClientRoute(_ContainerRoute):
    """
    Route class that fills the arguments typed as clients from the global `DI`.
    """

    container = DI


vars(DI)[_ROUTE_CLASS] = ClientRoute


def _route_class(container: Dependencies) -> type[_ContainerRoute]:
    route_cls: type[_ContainerRoute] | None = vars(container).get(_ROUTE_CLASS)
    # A copy of a container carries the route class of the original in its attributes
    if route_cls is None or route_cls.container is not container:
        route_cls = type("ContainerRoute", (_ContainerRoute,), {"container": container})
        vars(container)[_ROUTE_CLASS] = route_cls
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
        _add_websocket(self, super().add_api_websocket_route, self._route_cls, path, endpoint, args, kwargs)


def setup(app: FastAPI, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the routes declared on `app` from now on, and run `container` with the app:
    connect it on startup, disconnect it on shutdown.
    """
    route_cls = _route_class(container)
    current = app.router.route_class
    if not issubclass(current, route_cls):
        if current is not APIRoute:
            raise TypeError(
                f"app.router.route_class is {sname(current)}, which does not fill clients from this container, "
                f"and setup() would replace it: derive it from ClientRoute for DI"
            )
        app.router.route_class = route_cls

    # The app's own lifespan runs inside, so its startup and shutdown code can use the clients
    app.router.lifespan_context = wrap_lifespan(
        app.router.lifespan_context, route_cls.container, lambda: _router_bindings(app.router)
    )

    _track(app.router, route_cls)
    # On the router of the app rather than the app: app.include_router() calls it, and so may the user
    include_router = app.router.include_router

    def include(router: APIRouter, **kwargs: Any) -> None:
        _include(app.router, router, kwargs, route_cls, "this app")
        include_router(router, **kwargs)

    app.router.include_router = include  # type: ignore[method-assign]
    # FastAPI builds websocket routes without the route class
    add_websocket = app.router.add_api_websocket_route

    def add_api_websocket_route(path: str, endpoint: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        _add_websocket(app.router, add_websocket, route_cls, path, endpoint, args, kwargs)

    app.router.add_api_websocket_route = add_api_websocket_route  # type: ignore[method-assign]


class _Tracked:
    """
    What a router adds to the routes it includes, kept on the router; FastAPI 0.14x applies it lazily, out
    of reach of the route class, and only nuke-di knows which routers an app includes.
    """

    def __init__(self, dependencies: list[Binding]) -> None:
        # The clients of the router's own dependencies, applied to the routes of the routers it includes
        self.dependencies = dependencies
        # Each included router with the clients of the dependencies given to include_router()
        self.includes: list[tuple[APIRouter, list[Binding]]] = []


def _track(router: APIRouter, route_cls: type[_ContainerRoute]) -> None:
    bindings = [binding for depends in router.dependencies for binding in _bind(depends.dependency, route_cls)]
    router.nuke_di_tracked = _Tracked(bindings)  # type: ignore[attr-defined]


def _include(
    owner: APIRouter, router: APIRouter, kwargs: dict[str, Any], route_cls: type[_ContainerRoute], name: str
) -> None:
    other = router.route_class
    if issubclass(other, _ContainerRoute) and other.container is not route_cls.container:
        # Its clients would never be resolved: the startup of this container does not know them
        raise TypeError(f"the router fills clients from another container than {name}")
    bindings = [
        binding for depends in kwargs.get("dependencies") or () for binding in _bind(depends.dependency, route_cls)
    ]
    owner.nuke_di_tracked.includes.append((router, bindings))  # type: ignore[attr-defined]


def _add_websocket(
    router: APIRouter,
    add: Callable[..., None],
    route_cls: type[_ContainerRoute],
    path: str,
    endpoint: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    # Before FastAPI reads the signatures, which happens in APIWebSocketRoute.__init__; the dependencies of
    # the router itself are tracked already
    bindings = _bind(endpoint, route_cls)
    for depends in kwargs.get("dependencies") or ():
        bindings += _bind(depends.dependency, route_cls)
    add(path, endpoint, *args, **kwargs)
    # The route FastAPI has just appended
    router.routes[-1].nuke_di_bindings = bindings  # type: ignore[attr-defined]


def _router_bindings(router: APIRouter) -> list[Binding]:
    """
    The clients of every route an app serves through `router`, each once.
    """
    found: list[Binding] = []

    def visit(router: APIRouter) -> None:
        for route in router.routes:
            found.extend(getattr(route, "nuke_di_bindings", ()))
        tracked: _Tracked | None = getattr(router, "nuke_di_tracked", None)
        if tracked is None:
            return
        found.extend(tracked.dependencies)
        for included, bindings in tracked.includes:
            found.extend(bindings)
            visit(included)

    visit(router)
    return unique(found)


def _bind(call: Callable[..., Any] | None, route_cls: type[_ContainerRoute]) -> list[Binding]:
    return bind(call, route_cls.container, _FASTAPI)
