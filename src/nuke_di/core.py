import asyncio
import contextlib
import inspect
import logging
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar, cast, get_type_hints
from unittest.mock import create_autospec

from nuke_di.errors import ConnectError, ConnectTimeoutError, InitializeDependencyError, InvalidSignatureError
from nuke_di.options import DependenciesSettings
from nuke_di.types import Client, NotSingletonClient
from nuke_di.utils import isa, select_values, sname, walk_values

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

    async def connect(self) -> None:
        if self.connected is True:
            raise ConnectError("already connected")

        self.connected = True
        limiter = self._limiter()

        for number, layer in enumerate(self._group_by_layer()):
            logger.debug("Connecting layer %d: %s", number, ", ".join(map(sname, layer)))
            try:
                # The first failure cancels the rest of the layer
                async with asyncio.TaskGroup() as group:
                    for client in layer:
                        group.create_task(self._connect_client(client, limiter))

            except ExceptionGroup as eg:
                # Every failure is logged already, the first one stops the application
                error = cast(_ClientConnectError, eg.exceptions[0]).error
                raise error from error.__cause__

    async def _connect_client(self, client: NotSingletonClient, limiter: Limiter) -> None:
        name = sname(client)
        try:
            async with limiter:
                logger.debug("Connecting client %s", name)
                await asyncio.wait_for(client.connect(), timeout=self.settings.connect_timeout)

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

        self.connected = False
        limiter = self._limiter()

        for layer in reversed(self._group_by_layer()):
            await asyncio.gather(*(self._disconnect_client(client, limiter) for client in layer))

        self.flush()

    async def _disconnect_client(self, client: NotSingletonClient, limiter: Limiter) -> None:
        name = sname(client)
        try:
            async with limiter:
                logger.debug("Disconnecting client %s", name)
                await client.disconnect()
        except Exception:
            # The client failed, but the rest still have to be stopped
            logger.exception("Failed to disconnect client %s", name)

    def _limiter(self) -> Limiter:
        if self.settings.connect_concurrency == 0:
            return contextlib.nullcontext()
        return asyncio.Semaphore(self.settings.connect_concurrency)

    def _group_by_layer(self) -> list[list[NotSingletonClient]]:
        layers: defaultdict[int, list[NotSingletonClient]] = defaultdict(list)
        for client in self.connect_clients:
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

        name = sname(cls)
        logger.debug('Resolving dependency "%s"', name)

        # A client may appear in the class annotations but not in __init__,
        # which means it is not _our_ dependency
        type_hints = get_type_hints(cls.__init__)
        type_hints.pop("self", None)

        deps = select_values(isnotsingleton, type_hints)

        init = dict(walk_values(self.resolve, deps))
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
        if self.connected is True:
            raise ConnectError("already connected")
        return self.clients.setdefault(cls, new or create_autospec(cls))  # type: ignore[return-value]

    def _inspect(self, func: Callable) -> dict[str, NotSingletonClient]:
        logger.debug('Parsing signature of func "%s"', sname(func))
        signature: dict[str, NotSingletonClient] = {}

        # get_type_hints silently skips unannotated arguments, so they are looked up in the signature
        for param in inspect.signature(func).parameters.values():
            if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
                continue
            if param.annotation is inspect.Parameter.empty:
                raise InvalidSignatureError(f'Argument "{param.name}" of "{sname(func)}" has no type hint')

        sig: dict[str, Any] = get_type_hints(func)
        sig.pop("return", None)

        for key, value in sig.items():
            if isnotsingleton(value):
                signature[key] = self.resolve(value)

        return signature


DI = Dependencies()
