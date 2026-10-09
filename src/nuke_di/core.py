import asyncio
import contextlib
import inspect
import logging
import time
import types
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, TypeVar, Union, cast, get_args, get_origin, get_type_hints, overload

from nuke_di.errors import (
    CircularDependencyError,
    ConnectError,
    ConnectTimeoutError,
    InitializeDependencyError,
    InvalidSignatureError,
)
from nuke_di.graph import Graph, Node
from nuke_di.logs import fields
from nuke_di.options import DependenciesSettings
from nuke_di.timings import ClientTiming, Outcome
from nuke_di.types import Client, NotSingletonClient
from nuke_di.utils import isa, sname

logger = logging.getLogger(__name__)
isclient = isa(Client)
isnotsingleton = isa(NotSingletonClient)

CT = TypeVar("CT", bound=NotSingletonClient)
R = TypeVar("R")

Limiter = asyncio.Semaphore | contextlib.nullcontext[None]

# `*args` and `**kwargs`: never filled by the container
_VARIADIC = frozenset({inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD})


class _ClientConnectError(Exception):
    """
    Carries a `ConnectError` out of a connect task.

    `ConnectError` is a `SystemExit`, and a `SystemExit` raised inside a task escapes the event loop.
    """

    def __init__(self, error: ConnectError) -> None:
        super().__init__(str(error))
        self.error = error


@dataclass(repr=False)
class Dependencies:
    clients: OrderedDict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=OrderedDict)
    connect_clients: list[NotSingletonClient] = field(default_factory=list)
    settings: DependenciesSettings = field(default_factory=DependenciesSettings)
    connected: bool = field(default=False, init=False)
    # One entry per client of the last connect(), in connect order; kept after disconnect()
    timings: list[ClientTiming] = field(default_factory=list, init=False)
    # The entries of `timings` by `id()` of their client, until the container is flushed
    _timings: dict[int, ClientTiming] = field(default_factory=dict, init=False)
    # Layer of every client in `connect_clients`, keyed by `id()`: dataclass clients may be unhashable
    _layers: dict[int, int] = field(default_factory=dict, init=False)
    # For `graph()`: the class asked for and the client passed, per `__init__` argument of every client in
    # `connect_clients`, keyed by `id()`. The class tells a Replacement apart from the clients it stands in for.
    _dependencies: dict[int, dict[str, tuple[type[NotSingletonClient], NotSingletonClient]]] = field(
        default_factory=dict, init=False
    )
    # Replacements registered by `mock()` and `override()`, a subset of `clients`
    _replacements: dict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=dict, init=False)
    # Replacements of the open `override()` blocks, outermost first; `flush()` keeps them
    _overrides: list[tuple[type[NotSingletonClient], NotSingletonClient]] = field(default_factory=list, init=False)
    # Clients being resolved right now, outermost first: finds cycles and names the path in errors
    _resolving: list[type[NotSingletonClient]] = field(default_factory=list, init=False)
    # The same classes, for the cycle check: the list would be scanned once per client, as long as the depth
    _resolving_set: set[type[NotSingletonClient]] = field(default_factory=set, init=False)
    # The function `inject()` is resolving for, named first in the path but never part of a cycle
    _injecting: Callable[..., Any] | None = field(default=None, init=False)

    async def __aenter__(self) -> None:
        await self.connect()

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    def flush(self) -> None:
        if self.connected is True:
            raise ConnectError("already connected")

        self.clients = OrderedDict()
        self.connect_clients = []
        self._layers = {}
        self._dependencies = {}
        self._timings = {}
        self._replacements = {}
        for cls, replacement in self._overrides:
            self._register_replacement(cls, replacement)

    def _abandon(self) -> None:
        """
        Forget every client without disconnecting it, for a caller that has no event loop left to do it.
        """
        self.connected = False
        self.flush()

    async def connect(self) -> None:
        if self.connected is True:
            raise ConnectError("already connected")

        self.connected = True
        limiter = self._limiter()
        layers = self._group_by_layer(self.connect_clients)
        self._timings = {
            id(client): ClientTiming(sname(client), self._layer(client)) for layer in layers for client in layer
        }
        self.timings = list(self._timings.values())
        # Clients whose connect() finished, so a failure knows what to roll back
        connected: list[NotSingletonClient] = []
        started = time.perf_counter()

        try:
            for number, layer in enumerate(layers):
                logger.debug(
                    "Connecting layer %d: %s", number, ", ".join(map(sname, layer)), extra=fields(layer=number)
                )
                # The first failure cancels the rest of the layer
                async with asyncio.TaskGroup() as group:
                    for client in layer:
                        group.create_task(self._connect_client(client, limiter, connected))

        except ExceptionGroup as eg:
            await self._rollback(connected)
            # Every failure is logged already, the first one stops the application
            error = cast(_ClientConnectError, eg.exceptions[0]).error
            raise error from error.__cause__

        except asyncio.CancelledError:
            await self._rollback(connected)
            raise

        self._log_startup(len(layers), time.perf_counter() - started)

    def _log_startup(self, layers: int, duration: float) -> None:
        if not self.timings:
            return

        slowest = sorted(self.timings, key=lambda timing: timing.connect or 0, reverse=True)[:3]
        logger.info(
            "Connected %s in %s in %.2fs (slowest: %s)",
            _count(len(self.timings), "client"),
            _count(layers, "layer"),
            duration,
            ", ".join(f"{timing.name} {timing.connect or 0:.2f}s" for timing in slowest),
            extra=fields(duration=duration),
        )

        timeout = self.settings.connect_timeout
        for timing in self.timings:
            if timing.connect is not None and timing.connect > timeout / 2:
                logger.warning(
                    "Client %s took %.2fs to connect, more than half of CONNECT_TIMEOUT_SECONDS (%gs)",
                    timing.name,
                    timing.connect,
                    timeout,
                    extra=_fields(timing, timing.connect),
                )

    async def _connect_client(
        self, client: NotSingletonClient, limiter: Limiter, connected: list[NotSingletonClient]
    ) -> None:
        timing = self._timings[id(client)]
        name = timing.name
        # The record's fields are built per client: skipped when nobody listens
        debug = logger.isEnabledFor(logging.DEBUG)
        try:
            async with limiter:
                if debug:
                    logger.debug("Connecting client %s", name, extra=_fields(timing))
                with _measure(timing, "connect"):
                    await _within(self.settings.connect_timeout, client.connect())
            connected.append(client)
            if debug:
                logger.debug(
                    "Connected client %s in %.3fs", name, timing.connect, extra=_fields(timing, timing.connect)
                )

        except TimeoutError as exc:
            logger.exception("Timeout occurred connecting client %s", name, extra=_fields(timing, timing.connect))
            error: ConnectError = ConnectTimeoutError(f"Timeout occurred connecting client {name}")
            error.__cause__ = exc
            raise _ClientConnectError(error) from exc

        except Exception as exc:
            logger.exception("Error occurred connecting client %s", name, extra=_fields(timing, timing.connect))
            error = ConnectError(f"Error occurred connecting client {name}")
            error.__cause__ = exc
            raise _ClientConnectError(error) from exc

    async def disconnect(self) -> None:
        if self.connected is False:
            raise ConnectError("already disconnected")

        await self._disconnect_layers(self.connect_clients)

    async def _rollback(self, connected: list[NotSingletonClient]) -> None:
        logger.debug("Connect failed, disconnecting %d connected clients", len(connected), extra=fields())
        await self._disconnect_layers(connected)

    async def _disconnect_layers(self, clients: list[NotSingletonClient]) -> None:
        self.connected = False
        limiter = self._limiter()

        try:
            for layer in reversed(self._group_by_layer(clients)):
                # A client whose disconnect() ends in a CancelledError of its own made gather() raise it out of here,
                # skipping the rest; a TaskGroup ignores a cancelled child and finishes the layer. A client that fails
                # with an Exception is logged by _disconnect_client, so only a cancellation ends the group early
                async with asyncio.TaskGroup() as group:
                    for client in layer:
                        group.create_task(self._disconnect_client(client, limiter))
        finally:
            # Cancelled or not, the container keeps no half-disconnected client for the next connect() to reuse
            self.flush()

    async def _disconnect_client(self, client: NotSingletonClient, limiter: Limiter) -> None:
        timing = self._timings[id(client)]
        name = timing.name
        debug = logger.isEnabledFor(logging.DEBUG)
        try:
            async with limiter:
                if debug:
                    logger.debug("Disconnecting client %s", name, extra=_fields(timing))
                with _measure(timing, "disconnect"):
                    await _within(self.settings.disconnect_timeout, client.disconnect())
            if debug:
                logger.debug(
                    "Disconnected client %s in %.3fs",
                    name,
                    timing.disconnect,
                    extra=_fields(timing, timing.disconnect),
                )
        except TimeoutError:
            logger.exception("Timeout occurred disconnecting client %s", name, extra=_fields(timing, timing.disconnect))
        except Exception:
            # The client failed, but the rest still have to be stopped
            logger.exception("Failed to disconnect client %s", name, extra=_fields(timing, timing.disconnect))

    def _limiter(self) -> Limiter:
        if self.settings.connect_concurrency == 0:
            return contextlib.nullcontext()
        return asyncio.Semaphore(self.settings.connect_concurrency)

    def _layer(self, client: NotSingletonClient) -> int:
        return self._layers.get(id(client), 0)

    def _group_by_layer(self, clients: list[NotSingletonClient]) -> list[list[NotSingletonClient]]:
        layers: defaultdict[int, list[NotSingletonClient]] = defaultdict(list)
        for client in clients:
            layers[self._layer(client)].append(client)
        return [layers[number] for number in sorted(layers)]

    def resolve(self, cls: type[CT]) -> CT:
        """
        Idempotent operation.
        """
        if self.connected is True:
            raise ConnectError("already connected")

        inst = self.clients.get(cls)
        if inst is not None:
            return cast(CT, inst)

        if cls in self._resolving_set:
            raise CircularDependencyError(f"Circular dependency: {self._path(cls)}")

        name = sname(cls)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug('Resolving dependency "%s"', name, extra=fields(client=name))

        self._resolving.append(cls)
        self._resolving_set.add(cls)
        try:
            arguments = self._client_arguments(cls)
            init = {key: self.resolve(dep) for key, dep in arguments.items()}
        finally:
            self._resolving.pop()
            self._resolving_set.remove(cls)

        try:
            inst = cls(**init)
        except Exception as e:
            logger.exception("Error occurred during initialize client %s", name, extra=fields(client=name))
            raise InitializeDependencyError(f"Error occurred during initialize client {name}") from e

        if isclient(cls):
            self.clients[cls] = inst

        # One layer above the highest dependency; mocks are not connected, so they do not count
        self._layers[id(inst)] = 1 + max((self._layers.get(id(d), -1) for d in init.values()), default=-1)
        self._dependencies[id(inst)] = {key: (arguments[key], dep) for key, dep in init.items()}
        self.connect_clients.append(inst)
        return inst

    def graph(self) -> Graph:
        """
        A snapshot of the resolved clients with their dependencies and Layers.

        `flush()` empties it, apart from the Replacements of the open `override()` blocks, which it keeps.
        """
        # A Replacement is one object for every class it stands in for, and may be a resolved client too,
        # so its nodes are keyed by class and the resolved clients by `id()`
        replaced = {
            cls: Node(cls=cls, singleton=isclient(cls), layer=None, replacement=replacement)
            for cls, replacement in self._replacements.items()
        }
        resolved: dict[int, Node] = {}
        # Resolution order: a client's dependencies have a node before the client does
        for client in self.connect_clients:
            dependencies = {
                key: replaced[cls] if self._replacements.get(cls) is dep else resolved[id(dep)]
                for key, (cls, dep) in self._dependencies.get(id(client), {}).items()
            }
            resolved[id(client)] = Node(
                cls=type(client),
                singleton=isclient(type(client)),
                layer=self._layer(client),
                replacement=None,
                dependencies=MappingProxyType(dependencies),
            )
        return Graph((*replaced.values(), *resolved.values()))

    def _client_arguments(self, cls: type[NotSingletonClient]) -> dict[str, type[NotSingletonClient]]:
        """
        The arguments of `cls.__init__` to fill with clients; fail on any other required argument.

        Read once per class in the process: the result depends on `__init__` alone, so it is kept on the class
        beside the `__init__` it was read from, and read again when the class gets another `__init__`.
        """
        init = cls.__init__
        if init is object.__init__:
            # `inspect.signature` parses the text signature of a slot wrapper on every call
            return {}

        # A subclass that inherits `__init__` finds its base class's entry and shares it; one that redefines
        # `__init__` fails the check below and stores its own
        entry: tuple[Callable[..., Any], dict[str, type[NotSingletonClient]]] | None = getattr(
            cls, "__nuke_di_arguments__", None
        )
        if entry is not None and entry[0] is init:
            return entry[1]

        # Only a success is kept: a type hint that fails to evaluate must fail on the next resolve too
        arguments = self._read_arguments(cls, init)
        try:
            cls.__nuke_di_arguments__ = (init, arguments)  # type: ignore[attr-defined]
        except AttributeError:
            # A metaclass that forbids setting attributes: the class is read again on the next resolve
            pass
        return arguments

    def _read_arguments(
        self, cls: type[NotSingletonClient], init: Callable[..., Any]
    ) -> dict[str, type[NotSingletonClient]]:
        hints = self._type_hints(init, f"{sname(cls)}.__init__")

        clients: dict[str, type[NotSingletonClient]] = {}
        for argument in _init_arguments(init):
            name = argument.name
            hint: Any = hints.get(name)
            if isnotsingleton(hint):
                if argument.positional_only:
                    raise self._signature_error(cls, name, "is positional-only, a client is passed by keyword")
                clients[name] = hint
            elif argument.has_default:
                # Not ours to fill: the default stays
                continue
            elif hint is None:
                raise self._signature_error(cls, name, "has no type hint")
            elif (client := _optional_client(hint)) is not None:
                raise self._signature_error(cls, name, f"is {sname(client)} | None, a client cannot be optional")
            else:
                raise self._signature_error(cls, name, f"is {_type_name(hint)}, which is not a client")
        return clients

    def _type_hints(self, func: Callable[..., Any], name: str) -> dict[str, Any]:
        try:
            return get_type_hints(func)
        except NameError as exc:
            raise InvalidSignatureError(
                f'Cannot evaluate the type hints of "{name}": {exc}; a type hint must name something the module '
                f"defines or imports at runtime, not a class local to a function or imported under TYPE_CHECKING"
                f"{self._path_suffix()}"
            ) from exc

    def _signature_error(self, cls: type[NotSingletonClient], name: str, reason: str) -> InvalidSignatureError:
        return InvalidSignatureError(f'Argument "{name}" of "{sname(cls)}.__init__" {reason}{self._path_suffix()}')

    def _path(self, *more: Callable[..., Any]) -> str:
        root = [] if self._injecting is None else [self._injecting]
        return " -> ".join(map(sname, [*root, *self._resolving, *more]))

    def _path_suffix(self) -> str:
        return f" (resolving {self._path()})" if self._injecting is not None or self._resolving else ""

    def inject(self, func: Callable[..., R]) -> Callable[..., R]:
        """
        Bind the dependencies from the signature of `func`.

        note: `func` may be a function or a class. The result keeps the return type of `func`; its remaining
        arguments are not typed, a type checker cannot subtract the client arguments from a signature.
        """
        if self.connected is True:
            raise ConnectError("already connected")

        signature = self._inspect(func)
        return partial(func, **signature)

    # Typed like `unittest.mock.create_autospec`: an autospec mock is `Any`, so a test reaches its
    # `return_value` and `assert_awaited_once_with` under a strict type checker; a Replacement of your own
    # keeps its type
    @overload
    def mock(self, cls: type[CT], new: None = None) -> Any: ...

    @overload
    def mock(self, cls: type[CT], new: CT) -> CT: ...

    def mock(self, cls: type[CT], new: CT | None = None) -> Any:
        """
        Register a Replacement for `cls`, an autospec mock by default; `flush()` drops it.
        """
        if self.connected is True:
            raise ConnectError("already connected")

        name = sname(cls)
        current = self._replacements.get(cls)
        if current is not None:
            if new is not None and new is not current:
                raise ConnectError(f"{name} already has a replacement")
            return current
        # Consumers resolved before would keep the real client while the caller holds the Replacement
        if self._is_resolved(cls):
            raise ConnectError(f"{name} is already resolved, call mock() before resolve() or inject()")

        if new is None:
            # unittest costs every process a few milliseconds at import, and only tests mock
            from unittest.mock import create_autospec

            # A Replacement stands in for an instance: calling it is a TypeError, not another mock
            replacement = create_autospec(cls, instance=True)
        else:
            replacement = new
        self._register_replacement(cls, replacement)
        return self._replacements[cls]

    @overload
    def override(self, cls: type[CT], new: None = None) -> contextlib.AbstractContextManager[Any]: ...

    @overload
    def override(self, cls: type[CT], new: CT) -> contextlib.AbstractContextManager[CT]: ...

    @contextlib.contextmanager
    def override(self, cls: type[CT], new: CT | None = None) -> Iterator[Any]:
        """
        Register a Replacement for `cls` that lasts until the end of the block, `flush()` included.

        The container must have no resolved clients on entry and is flushed on exit,
        so nothing resolved with the Replacement outlives the block.
        """
        if self.connected is True:
            raise ConnectError("already connected")

        name = sname(cls)
        if self.connect_clients:
            # Flushing them on exit would silently drop what the caller resolved before the block
            resolved = ", ".join(sorted(map(sname, self.connect_clients)))
            raise ConnectError(f"override({name}) needs a container without resolved clients, found: {resolved}")
        if cls in self._replacements:
            raise ConnectError(f"{name} already has a replacement")

        replacement = self.mock(cls, new)
        entry: tuple[type[NotSingletonClient], NotSingletonClient] = (cls, replacement)
        self._overrides.append(entry)
        try:
            yield replacement
        except BaseException:
            # The exception of the block matters more than the state of the container
            self._close_override(entry)
            raise

        self._close_override(entry)
        if self.connected is True:
            raise ConnectError(f"override({name}) exited while the container is connected")

    def _close_override(self, entry: tuple[type[NotSingletonClient], NotSingletonClient]) -> None:
        self._overrides = [other for other in self._overrides if other is not entry]
        # A connected container is flushed by its disconnect()
        if self.connected is False:
            self.flush()

    def _is_resolved(self, cls: type[NotSingletonClient]) -> bool:
        # A NotSingletonClient is never cached in `clients`, only its instances are in `connect_clients`
        return cls in self.clients or any(type(client) is cls for client in self.connect_clients)

    def _register_replacement(self, cls: type[NotSingletonClient], replacement: NotSingletonClient) -> None:
        self.clients[cls] = replacement
        self._replacements[cls] = replacement

    def _inspect(self, func: Callable) -> dict[str, NotSingletonClient]:
        logger.debug('Parsing signature of func "%s"', sname(func), extra=fields())
        signature: dict[str, NotSingletonClient] = {}

        # get_type_hints silently skips unannotated arguments, so they are looked up in the signature
        for param in inspect.signature(func).parameters.values():
            if param.kind in _VARIADIC:
                continue
            if param.annotation is inspect.Parameter.empty:
                raise InvalidSignatureError(f'Argument "{param.name}" of "{sname(func)}" has no type hint')

        sig: dict[str, Any] = self._type_hints(func, sname(func))
        sig.pop("return", None)

        # A client may inject() on its own while this one resolves, so the outer root is restored after it
        outer, self._injecting = self._injecting, func
        try:
            for key, value in sig.items():
                if isnotsingleton(value):
                    signature[key] = self.resolve(value)
        finally:
            self._injecting = outer

        return signature


class _Argument(NamedTuple):
    """
    One argument of an `__init__`, as much of its signature as the container needs.
    """

    name: str
    positional_only: bool
    has_default: bool


def _init_arguments(init: Callable[..., Any]) -> list[_Argument]:
    """
    The arguments of `init` after `self`, without `*args` and `**kwargs`.
    """
    # A plain function is read from its code object, which is what `inspect.signature` does at thirty times the cost;
    # a decorated one (`__wrapped__`), a declared signature or a C function go through `inspect.signature`
    if inspect.isfunction(init) and "__wrapped__" not in init.__dict__ and "__signature__" not in init.__dict__:
        code = init.__code__
        positional = code.co_argcount
        names = code.co_varnames[: positional + code.co_kwonlyargcount]
        positional_only = code.co_posonlyargcount
        first_default = positional - len(init.__defaults__ or ())
        kwdefaults = init.__kwdefaults__ or {}
        # `self` is the first argument, unless the method takes it through `*args`
        start = 1 if positional or not code.co_flags & inspect.CO_VARARGS else 0
        return [
            _Argument(
                name, index < positional_only, index >= first_default if index < positional else name in kwdefaults
            )
            for index, name in enumerate(names[start:], start)
        ]

    parameters = list(inspect.signature(init).parameters.values())[1:]
    return [
        _Argument(param.name, param.kind is param.POSITIONAL_ONLY, param.default is not param.empty)
        for param in parameters
        if param.kind not in _VARIADIC
    ]


def _fields(timing: ClientTiming, duration: float | None = None) -> dict[str, Any]:
    """
    The structured fields of a log record about one client.
    """
    return fields(client=timing.name, layer=timing.layer, duration=duration)


@contextlib.contextmanager
def _measure(timing: ClientTiming, phase: Literal["connect", "disconnect"]) -> Iterator[None]:
    """
    Record how long the block took and how it ended as the `phase` of `timing`.
    """
    started = time.perf_counter()
    outcome: Outcome = "failed"
    try:
        yield
        outcome = "ok"
    except TimeoutError:
        outcome = "timed_out"
        raise
    except asyncio.CancelledError:
        # Another client of the layer failed, or the whole connect or disconnect was cancelled
        outcome = "cancelled"
        raise
    finally:
        setattr(timing, phase, time.perf_counter() - started)
        setattr(timing, f"{phase}_outcome", outcome)


async def _within(seconds: float, coro: Coroutine[Any, Any, None]) -> None:
    """
    Await a client's coroutine with a timeout, in the task of the caller.

    `asyncio.wait_for()` runs the coroutine in a task of its own on 3.11, about 40 µs per call, and in the caller's
    task only from 3.12. A timeout of zero or less expires before the coroutine starts, as `wait_for()` does;
    `asyncio.timeout(0)` alone would let it run up to its first suspension.
    """
    if seconds <= 0:
        coro.close()
        raise TimeoutError
    async with asyncio.timeout(seconds):
        await coro


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _optional_client(hint: Any) -> type[NotSingletonClient] | None:
    if get_origin(hint) not in {Union, types.UnionType}:
        return None
    args = get_args(hint)
    clients = [arg for arg in args if isnotsingleton(arg)]
    return clients[0] if type(None) in args and clients else None


def _type_name(hint: Any) -> str:
    """
    A type hint as written in code: `int`, `list[int]`, `Database | int`, without module prefixes.
    """
    if hint is type(None):
        return "None"
    if hint is Ellipsis:
        return "..."
    if isinstance(hint, list):
        # The parameters of a Callable
        return f"[{', '.join(map(_type_name, hint))}]"
    origin = get_origin(hint)
    if origin in {Union, types.UnionType}:
        return " | ".join(map(_type_name, get_args(hint)))
    if origin is not None:
        return f"{_type_name(origin)}[{', '.join(map(_type_name, get_args(hint))) or '()'}]"
    if inspect.isclass(hint):
        return hint.__name__
    return repr(hint).replace("typing.", "")


DI = Dependencies()
