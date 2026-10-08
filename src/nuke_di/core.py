import asyncio
import contextlib
import inspect
import logging
import types
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar, Union, cast, get_args, get_origin, get_type_hints
from unittest.mock import create_autospec

from nuke_di.errors import (
    CircularDependencyError,
    ConnectError,
    ConnectTimeoutError,
    InitializeDependencyError,
    InvalidSignatureError,
)
from nuke_di.options import DependenciesSettings
from nuke_di.types import Client, NotSingletonClient
from nuke_di.utils import isa, sname

logger = logging.getLogger(__name__)
isclient = isa(Client)
isnotsingleton = isa(NotSingletonClient)

CT = TypeVar("CT", bound=NotSingletonClient)

Limiter = asyncio.Semaphore | contextlib.nullcontext[None]


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
    # Layer of every client in `connect_clients`, keyed by `id()`: dataclass clients may be unhashable
    _layers: dict[int, int] = field(default_factory=dict, init=False)
    # Replacements registered by `mock()` and `override()`, a subset of `clients`
    _replacements: dict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=dict, init=False)
    # Replacements of the open `override()` blocks, outermost first; `flush()` keeps them
    _overrides: list[tuple[type[NotSingletonClient], NotSingletonClient]] = field(default_factory=list, init=False)
    # Clients being resolved right now, outermost first: finds cycles and names the path in errors
    _resolving: list[type[NotSingletonClient]] = field(default_factory=list, init=False)
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
        # Clients whose connect() finished, so a failure knows what to roll back
        connected: list[NotSingletonClient] = []

        try:
            for number, layer in enumerate(self._group_by_layer(self.connect_clients)):
                logger.debug("Connecting layer %d: %s", number, ", ".join(map(sname, layer)))
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

    async def _connect_client(
        self, client: NotSingletonClient, limiter: Limiter, connected: list[NotSingletonClient]
    ) -> None:
        name = sname(client)
        try:
            async with limiter:
                logger.debug("Connecting client %s", name)
                await asyncio.wait_for(client.connect(), timeout=self.settings.connect_timeout)
            connected.append(client)

        except TimeoutError as exc:
            logger.exception("Timeout occurred connecting client %s", name)
            error: ConnectError = ConnectTimeoutError(f"Timeout occurred connecting client {name}")
            error.__cause__ = exc
            raise _ClientConnectError(error) from exc

        except Exception as exc:
            logger.exception("Error occurred connecting client %s", name)
            error = ConnectError(f"Error occurred connecting client {name}")
            error.__cause__ = exc
            raise _ClientConnectError(error) from exc

    async def disconnect(self) -> None:
        if self.connected is False:
            raise ConnectError("already disconnected")

        await self._disconnect_layers(self.connect_clients)

    async def _rollback(self, connected: list[NotSingletonClient]) -> None:
        logger.debug("Connect failed, disconnecting %d connected clients", len(connected))
        await self._disconnect_layers(connected)

    async def _disconnect_layers(self, clients: list[NotSingletonClient]) -> None:
        self.connected = False
        limiter = self._limiter()

        for layer in reversed(self._group_by_layer(clients)):
            await asyncio.gather(*(self._disconnect_client(client, limiter) for client in layer))

        self.flush()

    async def _disconnect_client(self, client: NotSingletonClient, limiter: Limiter) -> None:
        name = sname(client)
        try:
            async with limiter:
                logger.debug("Disconnecting client %s", name)
                await asyncio.wait_for(client.disconnect(), timeout=self.settings.disconnect_timeout)
        except TimeoutError:
            logger.exception("Timeout occurred disconnecting client %s", name)
        except Exception:
            # The client failed, but the rest still have to be stopped
            logger.exception("Failed to disconnect client %s", name)

    def _limiter(self) -> Limiter:
        if self.settings.connect_concurrency == 0:
            return contextlib.nullcontext()
        return asyncio.Semaphore(self.settings.connect_concurrency)

    def _group_by_layer(self, clients: list[NotSingletonClient]) -> list[list[NotSingletonClient]]:
        layers: defaultdict[int, list[NotSingletonClient]] = defaultdict(list)
        for client in clients:
            layers[self._layers.get(id(client), 0)].append(client)
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

        if cls in self._resolving:
            raise CircularDependencyError(f"Circular dependency: {self._path(cls)}")

        name = sname(cls)
        logger.debug('Resolving dependency "%s"', name)

        self._resolving.append(cls)
        try:
            init = {key: self.resolve(dep) for key, dep in self._client_arguments(cls).items()}
        finally:
            self._resolving.pop()

        try:
            inst = cls(**init)
        except Exception as e:
            logger.exception("Error occurred during initialize client %s", name)
            raise InitializeDependencyError(f"Error occurred during initialize client {name}") from e

        if isclient(cls):
            self.clients[cls] = inst

        # One layer above the highest dependency; mocks are not connected, so they do not count
        self._layers[id(inst)] = 1 + max((self._layers.get(id(d), -1) for d in init.values()), default=-1)
        self.connect_clients.append(inst)
        return inst

    def _client_arguments(self, cls: type[NotSingletonClient]) -> dict[str, type[NotSingletonClient]]:
        """
        The arguments of `cls.__init__` to fill with clients; fail on any other required argument.
        """
        hints = self._type_hints(cls.__init__, f"{sname(cls)}.__init__")

        clients: dict[str, type[NotSingletonClient]] = {}
        # The first parameter is `self`
        for param in list(inspect.signature(cls.__init__).parameters.values())[1:]:
            if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
                continue

            hint: Any = hints.get(param.name)
            if isnotsingleton(hint):
                if param.kind is param.POSITIONAL_ONLY:
                    raise self._signature_error(cls, param, "is positional-only, a client is passed by keyword")
                clients[param.name] = hint
            elif param.default is not param.empty:
                # Not ours to fill: the default stays
                continue
            elif hint is None:
                raise self._signature_error(cls, param, "has no type hint")
            elif (client := _optional_client(hint)) is not None:
                raise self._signature_error(cls, param, f"is {sname(client)} | None, a client cannot be optional")
            else:
                raise self._signature_error(cls, param, f"is {_type_name(hint)}, which is not a client")
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

    def _signature_error(
        self, cls: type[NotSingletonClient], param: inspect.Parameter, reason: str
    ) -> InvalidSignatureError:
        return InvalidSignatureError(
            f'Argument "{param.name}" of "{sname(cls)}.__init__" {reason}{self._path_suffix()}'
        )

    def _path(self, *more: Callable[..., Any]) -> str:
        root = [] if self._injecting is None else [self._injecting]
        return " -> ".join(map(sname, [*root, *self._resolving, *more]))

    def _path_suffix(self) -> str:
        return f" (resolving {self._path()})" if self._injecting is not None or self._resolving else ""

    def inject(self, func: Callable) -> Callable:
        """
        Bind the dependencies from the signature of `func`.

        note: `func` may be a function or a class.
        """
        if self.connected is True:
            raise ConnectError("already connected")

        signature = self._inspect(func)
        return partial(func, **signature)

    def mock(self, cls: type[CT], new: CT | None = None) -> CT:
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
            return cast(CT, current)
        # Consumers resolved before would keep the real client while the caller holds the Replacement
        if self._is_resolved(cls):
            raise ConnectError(f"{name} is already resolved, call mock() before resolve() or inject()")

        self._register_replacement(cls, create_autospec(cls) if new is None else new)
        return cast(CT, self._replacements[cls])

    @contextlib.contextmanager
    def override(self, cls: type[CT], new: CT | None = None) -> Iterator[CT]:
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
        logger.debug('Parsing signature of func "%s"', sname(func))
        signature: dict[str, NotSingletonClient] = {}

        # get_type_hints silently skips unannotated arguments, so they are looked up in the signature
        for param in inspect.signature(func).parameters.values():
            if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
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
