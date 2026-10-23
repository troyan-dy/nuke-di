"""
One connect() of a container: every client after its dependencies, a failure rolling back the clients connected.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, cast

from nuke_di._container.disconnect import Disconnect
from nuke_di._container.messages import names
from nuke_di._container.order import (
    cancelled_from_outside,
    dependencies_among,
    in_dependency_order,
    make_limiter,
    measure,
    within,
)
from nuke_di.errors import ConnectError, ConnectTimeoutError
from nuke_di.logs import CONTAINER_LOGGER, client_fields, fields
from nuke_di.timings import ClientTiming
from nuke_di.types import NotSingletonClient

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)


class ClientConnectError(Exception):
    """
    Carries a `ConnectError` out of a connect task.

    `ConnectError` is a `SystemExit`, and a `SystemExit` raised inside a task escapes the event loop.
    """

    def __init__(self, error: ConnectError) -> None:
        super().__init__(str(error))
        self.error = error


class Connect:
    """
    The run of one connect: the clients connect in dependency order, and the first failure disconnects the ones
    that did.
    """

    def __init__(self, container: "Dependencies") -> None:
        self._container = container
        settings = container.settings
        self._limiter = make_limiter(settings)
        self._timeout = settings.connect_timeout
        # Clients whose connect() finished, so a failure knows what to roll back
        self._connected: list[NotSingletonClient] = []
        self._total = 0
        self._timings: dict[int, ClientTiming] = {}

    async def run(self) -> float:
        """
        Connect the clients of the container; how long it took.
        """
        container = self._container
        with container._lock:
            if container.connected:
                raise already_connected("connect()")

            # Flipped and snapshotted together: a thread inside resolve() finishes before or fails after, never
            # leaves a client that this connect() does not see
            container.connected = True
            clients = list(container.connect_clients)
        self._start_timings(clients)
        self._total = len(clients)
        started = time.perf_counter()

        try:
            # A client connects once its dependencies have; the first failure cancels every client still connecting
            # and every client still waiting
            await in_dependency_order(clients, dependencies_among(container, clients), self.client)

        except ExceptionGroup as eg:
            await self._rollback()
            # Every failure is logged already, the first one stops the application
            error = cast(ClientConnectError, eg.exceptions[0]).error
            raise error from error.__cause__

        except asyncio.CancelledError:
            await self._rollback()
            raise

        return time.perf_counter() - started

    async def client(self, client: NotSingletonClient) -> None:
        timing = self._timings[id(client)]
        # The record's fields are built per client: skipped when nobody listens
        debug = logger.isEnabledFor(logging.DEBUG)
        try:
            async with self._limiter:
                if debug:
                    logger.debug(
                        "Connecting client %s (%d/%d connected)",
                        timing.name,
                        len(self._connected),
                        self._total,
                        extra=client_fields(timing),
                    )
                with measure(timing, "connect"):
                    await within(self._timeout, client.connect())
        except TimeoutError as exc:
            raise self._timeout_failure(timing, exc) from exc
        except (Exception, asyncio.CancelledError) as exc:
            # A cancellation of connect() itself, or by the failure of another client, goes on. One of the client's
            # own fails the connect like an exception, its consumers would wait for it forever
            if isinstance(exc, asyncio.CancelledError) and cancelled_from_outside():
                raise
            raise self._failure(timing, exc) from exc

        # Past the try, which every failure leaves by raising
        self._connected.append(client)
        if debug:
            logger.debug(
                "Connected client %s in %.3fs (%d/%d connected)",
                timing.name,
                timing.connect,
                len(self._connected),
                self._total,
                extra=client_fields(timing, timing.connect),
            )

    def _start_timings(self, clients: list[NotSingletonClient]) -> None:
        """
        Give the container a timing per client of this connect(), in connect order.
        """
        # Computed once: the timings, the logs and the errors of this connect() all name a client the same way
        named = names(self._container)
        for client in clients:
            self._timings[id(client)] = ClientTiming(named[type(client)])
        self._container._timings = self._timings
        self._container.timings = list(self._timings.values())

    async def _rollback(self) -> None:
        logger.debug("Connect failed, disconnecting %d connected clients", len(self._connected), extra=fields())
        await Disconnect(self._container, self._connected).run()

    def _timeout_failure(self, timing: ClientTiming, exc: TimeoutError) -> ClientConnectError:
        """
        Log the timeout being handled, with the traceback, and carry its ConnectTimeoutError out of the task.
        """
        logger.exception(
            "%s did not connect within %gs (CONNECT_TIMEOUT_SECONDS)",
            timing.name,
            self._timeout,
            extra=client_fields(timing, timing.connect),
        )
        seconds = format(self._timeout, "g")
        error = ConnectTimeoutError(f"{timing.name} did not connect within {seconds}s (CONNECT_TIMEOUT_SECONDS)")
        error.__cause__ = exc
        return ClientConnectError(error)

    def _failure(self, timing: ClientTiming, exc: BaseException) -> ClientConnectError:
        """
        Log the exception being handled, with the traceback, and carry its ConnectError out of the task.
        """
        if isinstance(exc, asyncio.CancelledError):
            # The timing tells the culprit apart from the clients cancelled because of it
            timing.connect_outcome = "failed"
        raised = f"{timing.name}.connect() raised {described(exc)}"
        logger.exception("%s", raised, extra=client_fields(timing, timing.connect))
        error = ConnectError(raised)
        error.__cause__ = exc
        return ClientConnectError(error)


def already_connected(call: str) -> ConnectError:
    return ConnectError(f"{call}: already connected, call disconnect() first")


def described(exc: BaseException) -> str:
    """
    The type of `exc` and its message, if it has one: `OSError: unreachable`, a bare `CancelledError`.
    """
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
