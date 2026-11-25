"""
One connect() of a container: every client after its dependencies, a failure rolling back the clients connected.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from nuke_di._container.disconnect import Disconnect
from nuke_di._container.messages import names
from nuke_di._container.order import (
    LifecycleCancellation,
    connect_client,
    dependencies_among,
    in_dependency_order,
)
from nuke_di.errors import ConnectError, ConnectTimeoutError, InitializeDependencyError
from nuke_di.logs import CONTAINER_LOGGER, client_fields, fields
from nuke_di.naming import count
from nuke_di.types import Client

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)


class _ClientStartupError(Exception):
    """Carry a client's existing startup error through TaskGroup so rollback can finish first."""

    def __init__(self, error: BaseException) -> None:
        super().__init__(str(error))
        self.error = error


class Connect:
    """
    The run of one connect: the clients connect in dependency order, and the first failure disconnects the ones
    that did.
    """

    def __init__(self, container: "Dependencies") -> None:
        self._container = container
        self._cancellation = LifecycleCancellation()
        self._timeout: float = 0
        # Clients whose connect() finished, so a failure knows what to roll back
        self._connected: dict[type[Client], Client] = {}
        self._total = 0
        self._names: dict[type, str] = {}

    async def run(self) -> None:
        clients = self._snapshot()
        self._total = len(clients)
        prior_cancels = self._cancellation.prior_requests

        try:
            duration = await self._connect(clients, prior_cancels)
        except BaseException as exc:
            await self._rollback(exc, self._cancellation.task, prior_cancels)
            error = _startup_error(exc)
            if error is None:
                raise
        else:
            with self._container._lock:
                self._container._state = "connected"
            if clients:
                _log_summary(self._total, duration)
            return

        # Preserve the client's own exception context after leaving TaskGroup's exception handler.
        raise error

    async def client(self, key: type[Client], client: Client) -> None:
        name = self._names[key]
        debug = logger.isEnabledFor(logging.DEBUG)
        if debug:
            logger.debug(
                "Connecting client %s (%d/%d connected)",
                name,
                len(self._connected),
                self._total,
                extra=client_fields(name),
            )

        started = time.perf_counter()
        try:
            await connect_client(key, client, self._timeout, self._connected)
        except TimeoutError as exc:
            duration = time.perf_counter() - started
            raise _timeout_failure(name, duration, self._timeout) from exc
        except BaseException as exc:
            failure = self._failure(name, time.perf_counter() - started, exc)
            if failure is None:
                raise
            raise failure from exc

        duration = time.perf_counter() - started
        if debug:
            logger.debug(
                "Connected client %s in %.3fs (%d/%d connected)",
                name,
                duration,
                len(self._connected),
                self._total,
                extra=client_fields(name, duration),
            )
        if duration > self._timeout / 2:
            logger.warning(
                "Client %s took %.2fs to connect, more than half of CONNECT_TIMEOUT_SECONDS (%gs)",
                name,
                duration,
                self._timeout,
                extra=client_fields(name, duration),
            )

    async def _connect(self, clients: dict[type[Client], Client], prior_cancels: int) -> float:
        started = time.perf_counter()
        # A client connects once its dependencies have. The first failure cancels clients still connecting or waiting.
        dependencies = dependencies_among(self._container, clients)
        await in_dependency_order(clients, dependencies, self.client, prior_cancels)
        return time.perf_counter() - started

    async def _rollback(
        self,
        error: BaseException,
        task: asyncio.Task[object] | None,
        prior_cancels: int,
    ) -> None:
        cancellation = None
        cancels = 0 if task is None else task.cancelling()
        if isinstance(error, BaseExceptionGroup) and task is not None and cancels:
            # TaskGroup can reissue cancellation when it raises a group. Take it before disconnecting so a
            # request from startup cannot stop the rollback at its first await.
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError as exc:
                if task.cancelling() > cancels:
                    # A new request arrived during the checkpoint. Let it cancel the rollback itself.
                    task.uncancel()
                    task.cancel(*exc.args)
                else:
                    cancellation = exc
        logger.debug("Connect failed, disconnecting %d connected clients", len(self._connected), extra=fields())
        await Disconnect(self._container, self._connected).run()
        if cancellation is not None and task is not None and task.cancelling() > prior_cancels:
            # Report the failure first, then deliver the new cancellation on the caller's next await.
            task.uncancel()
            task.cancel(*cancellation.args)

    def _snapshot(self) -> dict[type[Client], Client]:
        container = self._container
        with container._lock:
            if container.connected:
                raise already_connected("connect()")

            # Read settings before starting so invalid configuration leaves the registry idle.
            self._timeout = container._settings().connect_timeout
            self._names = names(container)
            clients = container._lifecycle_clients()
            container._state = "connecting"
            return clients

    def _failure(self, name: str, duration: float, exc: BaseException) -> Exception | None:
        # A self-cancelled client must fail the group, or its consumers wait forever.
        if isinstance(exc, asyncio.CancelledError) and self._cancellation.group_is_cancelling():
            return None
        raised = f"{name}.connect() raised {described(exc)}"
        logger.exception("%s", raised, extra=client_fields(name, duration))
        if isinstance(exc, (SystemExit, KeyboardInterrupt, ConnectError, InitializeDependencyError)):
            return _ClientStartupError(exc)
        if isinstance(exc, (Exception, asyncio.CancelledError)):
            return ConnectError(raised)
        return None


def _log_summary(total: int, duration: float) -> None:
    quantity = count(total, "client")
    logger.info("Connected %s in %.2fs", quantity, duration, extra=fields(duration=duration))


def _timeout_failure(name: str, duration: float, timeout: float) -> ConnectTimeoutError:
    seconds = format(timeout, "g")
    message = f"{name} did not connect within {seconds}s (CONNECT_TIMEOUT_SECONDS)"
    logger.exception("%s", message, extra=client_fields(name, duration))
    return ConnectTimeoutError(message)


def _startup_error(exc: BaseException) -> BaseException | None:
    if not isinstance(exc, BaseExceptionGroup):
        return None
    # Preserve an existing startup error or interrupt ahead of a newly wrapped client failure.
    for failure in exc.exceptions:
        if isinstance(failure, _ClientStartupError):
            return failure.error
    first = exc.exceptions[0]
    if isinstance(exc, ExceptionGroup) and isinstance(first, ConnectError):
        # Every failure is logged already, the first one stops the application
        return first
    return None


def already_connected(call: str) -> RuntimeError:
    return RuntimeError(f"{call}: already connected, call disconnect() first")


def described(exc: BaseException) -> str:
    """
    The type of `exc` and its message, if it has one: `OSError: unreachable`, a bare `CancelledError`.
    """
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
