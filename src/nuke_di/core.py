import asyncio
import inspect
import logging
from collections import OrderedDict
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


@dataclass(repr=False)
class Dependencies:
    clients: OrderedDict[type[NotSingletonClient], NotSingletonClient] = field(default_factory=OrderedDict)
    connect_clients: list[NotSingletonClient] = field(default_factory=list)
    settings: DependenciesSettings = field(default_factory=DependenciesSettings)
    connected: bool = field(default=False, init=False)

    async def __aenter__(self) -> None:
        await self.connect()

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    def flush(self) -> None:
        if self.connected is True:
            raise ConnectError("already connected")

        self.clients = OrderedDict()
        self.connect_clients = []

    async def connect(self) -> None:
        if self.connected is True:
            raise ConnectError("already connected")

        self.connected = True

        for client in self.connect_clients:
            name = sname(client)
            logger.debug("Connecting client %s", name)
            try:
                await asyncio.wait_for(client.connect(), timeout=self.settings.connect_timeout)

            except TimeoutError as exc:
                logger.exception("Timeout occurred connecting client %s", name)
                raise ConnectTimeoutError(f"Timeout occurred connecting client {name}") from exc

            except Exception as e:
                logger.exception("Error occurred connecting client %s", name)
                raise ConnectError(f"Error occurred connecting client {name}") from e

    async def disconnect(self) -> None:
        if self.connected is False:
            raise ConnectError("already disconnected")

        self.connected = False

        for client in reversed(self.connect_clients):
            name = sname(client)
            logger.debug("Disconnecting client %s", name)
            try:
                await client.disconnect()
            except Exception:
                # The client failed, but the rest still have to be stopped
                logger.exception("Failed to disconnect client %s", name)

        self.flush()

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

        sig: dict[str, Any] = get_type_hints(func)
        sig.pop("return", None)

        for key, value in sig.items():
            if value is inspect.Parameter.empty:
                raise InvalidSignatureError("Arguments without type hints are restricted")

            if isnotsingleton(value):
                signature[key] = self.resolve(value)

        return signature


DI = Dependencies()
