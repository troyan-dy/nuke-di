"""
FastAPI integration: path operations and their dependencies take clients by type hint.

See docs/specs/fastapi.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

import inspect
import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, ClassVar, cast, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Depends, FastAPI, params
from fastapi.routing import APIRoute

from nuke_di.clients import BackgroundTasks, Shutdown
from nuke_di.core import DI, Dependencies, isnotsingleton
from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname

__all__ = ("ClientRoute", "ClientRouter", "setup")

# Where a container keeps its route class: the container is an unhashable dataclass, and keeping it here
# lets both be garbage collected together
_ROUTE_CLASS = "_nuke_di_route_class"


class _Binding:
    """
    A client argument of one function, filled with the instance resolved on startup.
    """

    def __init__(self, cls: type[NotSingletonClient], container: Dependencies) -> None:
        self.cls = cls
        self.container = weakref.ref(container)
        self.instance: NotSingletonClient | None = None

    async def get(self) -> NotSingletonClient:
        # No arguments and `async`, so FastAPI calls it inline rather than in a threadpool
        if self.instance is not None:
            return self.instance

        container = self.container()
        if container is not None and container.connected:
            # The app started, but its startup never saw this route
            raise RuntimeError(
                f"{sname(self.cls)} was not started with the app: include the router of its route into the app "
                f"or into a ClientRouter, not into a plain APIRouter"
            )
        raise RuntimeError(
            f"{sname(self.cls)} is not connected: start the app with its lifespan, e.g. `with TestClient(app)`"
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

    original = app.router.lifespan_context
    if getattr(original, "__nuke_di__", False):
        raise TypeError("setup() was already called for this app")

    _track(app.router, route_cls)
    # On the router of the app rather than the app: app.include_router() calls it, and so may the user
    include_router = app.router.include_router

    def include(router: APIRouter, **kwargs: Any) -> None:
        _include(app.router, router, kwargs, route_cls, "this app")
        include_router(router, **kwargs)

    app.router.include_router = include  # type: ignore[method-assign]

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[Any]:
        # The app's own lifespan runs inside, so its startup and shutdown code can use the clients
        async with _connected(route_cls.container, _router_bindings(app.router)), original(app) as state:
            yield state

    lifespan.__nuke_di__ = True  # type: ignore[attr-defined]
    app.router.lifespan_context = lifespan


class _Tracked:
    """
    What a router adds to the routes it includes, kept on the router; FastAPI 0.14x applies it lazily, out
    of reach of the route class, and only nuke-di knows which routers an app includes.
    """

    def __init__(self, dependencies: list[_Binding]) -> None:
        # The clients of the router's own dependencies, applied to the routes of the routers it includes
        self.dependencies = dependencies
        # Each included router with the clients of the dependencies given to include_router()
        self.includes: list[tuple[APIRouter, list[_Binding]]] = []


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


def _router_bindings(router: APIRouter) -> list[_Binding]:
    """
    The clients of every route an app serves through `router`, each once.
    """
    found: dict[int, _Binding] = {}

    def visit(router: APIRouter) -> None:
        for route in router.routes:
            found.update((id(binding), binding) for binding in getattr(route, "nuke_di_bindings", ()))
        tracked: _Tracked | None = getattr(router, "nuke_di_tracked", None)
        if tracked is None:
            return
        found.update((id(binding), binding) for binding in tracked.dependencies)
        for included, bindings in tracked.includes:
            found.update((id(binding), binding) for binding in bindings)
            visit(included)

    visit(router)
    return list(found.values())


@asynccontextmanager
async def _connected(container: Dependencies, bindings: list[_Binding]) -> AsyncIterator[None]:
    if container.connected:
        # E.g. by another app on the same container; its clients are left as they are
        raise RuntimeError("nuke-di clients failed to start: the container is already connected")

    try:
        for binding in bindings:
            binding.instance = container.resolve(binding.cls)
        await container.connect()
    except BaseException as exc:
        _forget(bindings)
        # A failed connect() has already flushed the container, a failed resolution has not
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
            _forget(bindings)


def _forget(bindings: list[_Binding]) -> None:
    for binding in bindings:
        binding.instance = None


class _ClassSignature:
    """
    The rewritten signature of one class, hidden from its subclasses, which inherit class attributes.
    """

    def __init__(self, owner: type, signature: inspect.Signature) -> None:
        self.owner = owner
        self.signature = signature

    def __get__(self, instance: object, owner: type) -> inspect.Signature | None:
        # `None` makes inspect.signature() compute the signature as usual
        return self.signature if instance is None and owner is self.owner else None


def _bind(call: Callable[..., Any] | None, route_cls: type[_ContainerRoute]) -> list[_Binding]:
    """
    Rewrite the signature of `call` and of the dependencies it uses, so FastAPI fills its clients;
    return the clients of all of them.
    """
    if inspect.isclass(call):
        init: Callable[..., Any] = call.__init__
    elif inspect.isfunction(call):
        init = call
    else:
        # Bound methods, callable objects and the `None` of `Depends()`: no signature to replace in place
        return []

    # `vars()`, not getattr(): a subclass must not look bound because its base class is
    marks = vars(call)
    container = marks.get("__nuke_di_container__")
    if container is not None and container() is route_cls.container:
        # Bound already, e.g. a route of an included router copied by an older FastAPI. A function bound to
        # another container is bound again: the routes declared before keep the dependencies they captured
        return cast(list[_Binding], marks["__nuke_di_bindings__"])

    try:
        hints = get_type_hints(init, include_extras=True)
        # The signature as written, not the one a binding to another container replaced it with
        signature: inspect.Signature = marks.get("__nuke_di_signature__") or inspect.signature(call)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: FastAPI copes with it, or reports it itself
        return []

    parameters = []
    own: list[_Binding] = []
    reachable: list[_Binding] = []
    for param in signature.parameters.values():
        hint = hints.get(param.name, param.annotation)
        client = _client(hint)
        if client is None:
            depends = _depends(param, hint)
            if depends is not None:
                # `Annotated[Auth, Depends()]` depends on the annotated class
                reachable += _bind(depends.dependency or _hint_class(hint), route_cls)
            parameters.append(param.replace(annotation=hint))
            continue

        binding = _Binding(client, route_cls.container)
        own.append(binding)
        parameters.append(param.replace(annotation=Annotated[client, Depends(binding.get)]))

    reachable += own
    if not own:
        # Nothing to rewrite here; the clients of its dependencies are found again on the next declaration
        return reachable

    # Annotations are evaluated already: FastAPI would resolve strings against the wrong module otherwise
    # A class returns an instance of itself, not what its __init__ is annotated with
    return_annotation = signature.empty if inspect.isclass(call) else hints.get("return", signature.return_annotation)
    rewritten = signature.replace(parameters=parameters, return_annotation=return_annotation)
    if inspect.isclass(call):
        call.__signature__ = _ClassSignature(call, rewritten)  # type: ignore[attr-defined]
    else:
        call.__signature__ = rewritten  # type: ignore[attr-defined]
    call.__nuke_di_container__ = weakref.ref(route_cls.container)  # type: ignore[union-attr]
    call.__nuke_di_signature__ = signature  # type: ignore[union-attr]
    call.__nuke_di_bindings__ = reachable  # type: ignore[union-attr]
    return reachable


def _hint_class(hint: Any) -> Any:
    """
    The class an `Annotated[Class, ...]` hint names.
    """
    return get_args(hint)[0] if get_origin(hint) is Annotated else hint


def _client(hint: Any) -> type[NotSingletonClient] | None:
    """
    The client a type hint asks for: `Client`, or `Annotated[Client, ...]` without a `Depends`.
    """
    if isnotsingleton(hint):
        return cast(type[NotSingletonClient], hint)
    if get_origin(hint) is Annotated:
        inner, *metadata = get_args(hint)
        if isnotsingleton(inner) and not any(isinstance(item, params.Depends) for item in metadata):
            return cast(type[NotSingletonClient], inner)
    return None


def _depends(param: inspect.Parameter, hint: Any) -> params.Depends | None:
    if isinstance(param.default, params.Depends):
        return param.default
    if get_origin(hint) is Annotated:
        return next((item for item in get_args(hint)[1:] if isinstance(item, params.Depends)), None)
    return None
