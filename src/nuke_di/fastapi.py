"""
FastAPI integration: path operations and their dependency functions take clients by type hint.

See docs/specs/fastapi.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

import inspect
import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, ClassVar, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Depends, FastAPI, params
from fastapi.routing import APIRoute

from nuke_di.clients import BackgroundTasks, Shutdown
from nuke_di.core import DI, Dependencies, isnotsingleton
from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname

__all__ = ("ClientRoute", "ClientRouter", "setup")


class _Binding:
    """
    A client argument of one function, filled with the instance resolved on startup.
    """

    def __init__(self, cls: type[NotSingletonClient]) -> None:
        self.cls = cls
        self.instance: NotSingletonClient | None = None

    async def get(self) -> NotSingletonClient:
        # No arguments and `async`, so FastAPI calls it inline rather than in a threadpool
        if self.instance is None:
            raise RuntimeError(
                f"{sname(self.cls)} is not connected: start the app with its lifespan, e.g. `with TestClient(app)`"
            )
        return self.instance


class ClientRoute(APIRoute):
    """
    Route class that fills the arguments typed as clients, from the global `DI`.

    `ClientRouter(container=...)` uses the route class of another container.
    """

    container: ClassVar[Dependencies] = DI
    bindings: ClassVar[list[_Binding]] = []

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        # Before FastAPI reads the signatures, which happens in APIRoute.__init__
        _bind(endpoint, type(self))
        for depends in kwargs.get("dependencies") or ():
            _bind_depends(depends, type(self))
        super().__init__(path, endpoint, **kwargs)


# A container is an unhashable dataclass, so its route class is kept by id(), with a weak reference to tell
# a live container from a new one that got the same id
_route_classes: dict[int, tuple[weakref.ref[Dependencies], type[ClientRoute]]] = {
    id(DI): (weakref.ref(DI), ClientRoute),
}


def _route_class(container: Dependencies) -> type[ClientRoute]:
    """
    The route class of `container`, one per container.
    """
    known = _route_classes.get(id(container))
    if known is not None and known[0]() is container:
        return known[1]

    route = type("ClientRoute", (ClientRoute,), {"container": container, "bindings": []})
    _route_classes[id(container)] = (weakref.ref(container), route)
    return route


class ClientRouter(APIRouter):
    """
    `APIRouter` whose routes and dependencies take clients by type hint, from `container`.

    It also fills the clients of its own `dependencies=` and of the `dependencies=` given to its
    `include_router()`, which FastAPI applies to included routes without their route class.
    """

    def __init__(self, *, container: Dependencies = DI, **kwargs: Any) -> None:
        route = _route_class(container)
        route_class = kwargs.setdefault("route_class", route)
        if not issubclass(route_class, route):
            raise TypeError(
                f"route_class={sname(route_class)} does not fill clients from the container of this router: "
                f"derive it from the route class of the container, e.g. ClientRoute for DI"
            )
        super().__init__(**kwargs)
        self._container_route = route
        for depends in self.dependencies:
            _bind_depends(depends, route)

    def include_router(self, router: APIRouter, **kwargs: Any) -> None:
        for depends in kwargs.get("dependencies") or ():
            _bind_depends(depends, self._container_route)
        super().include_router(router, **kwargs)


def setup(app: FastAPI, container: Dependencies = DI) -> None:
    """
    Fill client arguments of the routes declared on `app` from now on, and run `container` with the app:
    connect it on startup, disconnect it on shutdown.
    """
    route = _route_class(container)
    current = app.router.route_class
    if not issubclass(current, route):
        if current is not APIRoute:
            raise TypeError(
                f"app.router.route_class is {sname(current)}, which does not fill clients from this container, "
                f"and setup() would replace it: derive it from the route class of the container, e.g. ClientRoute "
                f"for DI"
            )
        app.router.route_class = route

    original = app.router.lifespan_context
    if getattr(original, "__nuke_di__", False):
        raise TypeError("setup() was already called for this app")

    # FastAPI(dependencies=...) also applies to included routers, which FastAPI builds without the route class
    for depends in app.router.dependencies:
        _bind_depends(depends, route)

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[Any]:
        # The app's own lifespan runs inside, so its startup and shutdown code can use the clients
        async with _connected(route), original(app) as state:
            yield state

    lifespan.__nuke_di__ = True  # type: ignore[attr-defined]
    app.router.lifespan_context = lifespan


@asynccontextmanager
async def _connected(route: type[ClientRoute]) -> AsyncIterator[None]:
    container = route.container
    try:
        for binding in route.bindings:
            binding.instance = container.resolve(binding.cls)
        await container.connect()
    except BaseException as exc:
        _forget(route)
        # A failed connect() has already flushed the container, a failed resolution has not; a container that
        # was connected before startup is not ours to flush
        if not container.connected:
            container.flush()
        if isinstance(exc, SystemExit):
            # ConnectError and InitializeDependencyError are SystemExit, which escapes the event loop of the
            # server; a plain error lets it report a failed startup and exit
            raise RuntimeError(f"nuke-di clients failed to start: {exc}") from exc
        raise

    try:
        yield
    finally:
        # The same order as a Run: stop the loops, then the tasks that use the clients, then the clients
        shutdown = container.clients.get(Shutdown)
        if isinstance(shutdown, Shutdown):
            shutdown.set()
        tasks = container.clients.get(BackgroundTasks)
        if isinstance(tasks, BackgroundTasks):
            await tasks.stop()
        try:
            await container.disconnect()
        finally:
            _forget(route)


def _forget(route: type[ClientRoute]) -> None:
    for binding in route.bindings:
        binding.instance = None


def _bind(call: Callable[..., Any] | None, route: type[ClientRoute]) -> None:
    """
    Rewrite the signature of `call` and of the dependency functions it uses, so FastAPI fills its clients.
    """
    # Classes, callable objects and the `None` of `Depends()` are left to FastAPI: no signature to replace in place
    if not inspect.isfunction(call):
        return

    # Bound to this container already, e.g. a route of an included router copied by an older FastAPI. A function
    # bound to another container is bound again: the routes declared before keep the dependencies they captured
    if getattr(call, "__nuke_di_container__", None) is route.container:
        return

    try:
        hints = get_type_hints(call, include_extras=True)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: FastAPI copes with it, or reports it itself
        return

    # The signature as written, not the one a binding to another container replaced it with
    signature: inspect.Signature = getattr(call, "__nuke_di_signature__", None) or inspect.signature(call)
    parameters = []
    bindings = []
    for param in signature.parameters.values():
        hint = hints.get(param.name, param.annotation)
        client = _client(hint)
        if client is None:
            depends = _depends(param, hint)
            if depends is not None:
                _bind(depends.dependency, route)
            parameters.append(param.replace(annotation=hint))
            continue

        binding = _Binding(client)
        bindings.append(binding)
        parameters.append(param.replace(annotation=Annotated[client, Depends(binding.get)]))

    if bindings:
        # Annotations are evaluated already: FastAPI would resolve strings against the wrong module otherwise
        return_annotation = hints.get("return", signature.return_annotation)
        call.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=parameters, return_annotation=return_annotation
        )
        call.__nuke_di_container__ = route.container  # type: ignore[attr-defined]
        call.__nuke_di_signature__ = signature  # type: ignore[attr-defined]
        route.bindings.extend(bindings)


def _bind_depends(depends: params.Depends, route: type[ClientRoute]) -> None:
    _bind(depends.dependency, route)


def _client(hint: Any) -> type[NotSingletonClient] | None:
    """
    The client a type hint asks for: `Client`, or `Annotated[Client, ...]` without a `Depends`.
    """
    if isnotsingleton(hint):
        return hint  # type: ignore[no-any-return]
    if get_origin(hint) is Annotated:
        inner, *metadata = get_args(hint)
        if isnotsingleton(inner) and not any(isinstance(item, params.Depends) for item in metadata):
            return inner  # type: ignore[no-any-return]
    return None


def _depends(param: inspect.Parameter, hint: Any) -> params.Depends | None:
    if isinstance(param.default, params.Depends):
        return param.default
    if get_origin(hint) is Annotated:
        return next((item for item in get_args(hint)[1:] if isinstance(item, params.Depends)), None)
    return None
