"""
The kit the framework integrations are built on: client arguments filled by the framework's own DI, and the
container connected for the time an app runs. `nuke_di.fastapi`, `nuke_di.faststream`, `nuke_di.litestar`,
`nuke_di.aiogram` and `nuke_di.taskiq` are built on it, as are `nuke_di.mcp` and `nuke_di.fastmcp`, and an
integration with another framework needs nothing private of nuke-di besides it.

See docs/guide/integrations.md and docs/adr/0003-fastapi-signature-rewrite.md.
"""

import inspect
import weakref
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, cast, get_args, get_origin, get_type_hints

from nuke_di.clients import BackgroundTasks, Shutdown
from nuke_di.core import Dependencies, isnotsingleton
from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname

__all__ = ("Binding", "DependsFramework", "Framework", "bind", "client_of", "running", "unique", "wrap_lifespan")


@dataclass(frozen=True, eq=False)
class Framework:
    """
    What the users of one framework are told when a client is missing; `{client}` is its class name.
    """

    name: str
    not_started: str
    not_connected: str


@dataclass(frozen=True, eq=False)
class DependsFramework(Framework):
    """
    A framework that injects through `Depends(...)` markers in signatures.
    """

    # The class of the markers
    depends: type
    # Builds the marker for a callable
    make_depends: Callable[[Callable[..., Any]], Any]
    # Whether a function is bound again for every container: FastAPI analyses a route once, when it is
    # declared, so each app keeps the bindings it captured. Otherwise a function is bound once, and every
    # app that starts resolves the same bindings
    per_container: bool = True


class Binding:
    """
    A client argument of one function, filled with the instance resolved on startup.
    """

    def __init__(self, cls: type[NotSingletonClient], container: Dependencies, framework: Framework) -> None:
        self.cls = cls
        # The container that resolves it, or would: it tells "not started" from "not connected"
        self.container = weakref.ref(container)
        self.framework = framework
        self.instance: NotSingletonClient | None = None

    async def get(self) -> NotSingletonClient:
        # No arguments and `async`, so the framework calls it inline rather than in a threadpool
        if self.instance is not None:
            return self.instance

        container = self.container()
        if container is not None and container.connected:
            # The app started, but its startup never saw this function
            raise RuntimeError(self.framework.not_started.format(client=sname(self.cls)))
        raise RuntimeError(self.framework.not_connected.format(client=sname(self.cls)))


def unique(bindings: Iterable[Binding]) -> list[Binding]:
    """
    `bindings` without repeats, in order: one function is often reachable from several handlers.
    """
    return list({id(binding): binding for binding in bindings}.values())


@asynccontextmanager
async def running(container: Dependencies, bindings: list[Binding]) -> AsyncIterator[None]:
    """
    Resolve the clients of `bindings` and connect `container` for the time an app runs.
    """
    if container.connected:
        # E.g. by another app on the same container; its clients are left as they are
        raise RuntimeError("nuke-di clients failed to start: the container is already connected")
    busy = next((binding for binding in bindings if binding.instance is not None), None)
    if busy is not None:
        # A function bound once, shared with an app that runs: filling it would hand that app our clients
        raise RuntimeError(
            f"nuke-di clients failed to start: {sname(busy.cls)} is filled for another app that is running; "
            f"apps that share a handler function run one at a time"
        )

    # A failed resolution would leave the timings of an earlier connect
    container.timings = []
    try:
        for binding in bindings:
            binding.instance = container.resolve(binding.cls)
            binding.container = weakref.ref(container)
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


def wrap_lifespan(
    original: Callable[..., Any], container: Dependencies, bindings: Callable[[], list[Binding]]
) -> Callable[..., Any]:
    """
    A lifespan that runs `original` inside `container`, connected with the clients `bindings()` finds on
    startup; refuses an app set up already.
    """
    if getattr(original, "__nuke_di__", False):
        raise TypeError("setup() was already called for this app")

    @asynccontextmanager
    async def lifespan(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        # The app's own lifespan runs inside, so its startup and shutdown code can use the clients
        async with running(container, bindings()), original(*args, **kwargs) as state:
            yield state

    lifespan.__nuke_di__ = True  # type: ignore[attr-defined]
    return lifespan


def _forget(bindings: list[Binding]) -> None:
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


def bind(call: Callable[..., Any] | None, container: Dependencies, framework: DependsFramework) -> list[Binding]:
    """
    Rewrite the signature of `call` and of the dependencies it uses, so the framework fills its clients;
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
    owner = marks.get("__nuke_di_owner__")
    if owner is not None and owner[1] is not framework:
        raise TypeError(
            f"{sname(call)} takes clients in both {owner[1].name} and {framework.name} handlers: nuke-di rewrites "
            f"its signature for one framework, so give each framework its own function"
        )
    if owner is not None and (owner[0]() is container or not framework.per_container):
        # Bound already, e.g. a route of an included router copied by an older FastAPI. A function bound to
        # another container is bound again for FastAPI: the routes declared before keep their dependencies
        return cast(list[Binding], marks["__nuke_di_bindings__"])

    try:
        hints = get_type_hints(init, include_extras=True)
        # The signature as written, not the one a binding to another container replaced it with
        signature: inspect.Signature = marks.get("__nuke_di_signature__") or inspect.signature(call)
    except NameError:
        # E.g. a name imported under TYPE_CHECKING: the framework copes with it, or reports it itself
        return []

    parameters = []
    own: list[Binding] = []
    reachable: list[Binding] = []
    for param in signature.parameters.values():
        hint = hints.get(param.name, param.annotation)
        client = client_of(hint, framework.depends)
        if client is None:
            depends = _depends(param, hint, framework.depends)
            if depends is not None:
                # `Annotated[Auth, Depends()]` depends on the annotated class
                reachable += bind(depends.dependency or _hint_class(hint), container, framework)
            parameters.append(param.replace(annotation=hint))
            continue

        binding = Binding(client, container, framework)
        own.append(binding)
        parameters.append(param.replace(annotation=Annotated[client, framework.make_depends(binding.get)]))

    reachable += own
    if not own:
        # Nothing to rewrite here; the clients of its dependencies are found again on the next declaration
        return reachable

    # Annotations are evaluated already: the framework would resolve strings against the wrong module otherwise
    # A class returns an instance of itself, not what its __init__ is annotated with
    return_annotation = signature.empty if inspect.isclass(call) else hints.get("return", signature.return_annotation)
    rewritten = signature.replace(parameters=parameters, return_annotation=return_annotation)
    if inspect.isclass(call):
        call.__signature__ = _ClassSignature(call, rewritten)  # type: ignore[attr-defined]
    else:
        call.__signature__ = rewritten  # type: ignore[attr-defined]
    call.__nuke_di_owner__ = (weakref.ref(container), framework)  # type: ignore[union-attr]
    call.__nuke_di_signature__ = signature  # type: ignore[union-attr]
    call.__nuke_di_bindings__ = reachable  # type: ignore[union-attr]
    return reachable


def _hint_class(hint: Any) -> Any:
    """
    The class an `Annotated[Class, ...]` hint names.
    """
    return get_args(hint)[0] if get_origin(hint) is Annotated else hint


def client_of(hint: Any, *markers: type) -> type[NotSingletonClient] | None:
    """
    The client a type hint asks for: `Client`, or `Annotated[Client, ...]` without any of `markers`.
    """
    if isnotsingleton(hint):
        return cast(type[NotSingletonClient], hint)
    if get_origin(hint) is Annotated:
        inner, *metadata = get_args(hint)
        if isnotsingleton(inner) and not any(isinstance(item, markers) for item in metadata):
            return cast(type[NotSingletonClient], inner)
    return None


def _depends(param: inspect.Parameter, hint: Any, marker: type) -> Any:
    if isinstance(param.default, marker):
        return param.default
    if get_origin(hint) is Annotated:
        return next((item for item in get_args(hint)[1:] if isinstance(item, marker)), None)
    return None
