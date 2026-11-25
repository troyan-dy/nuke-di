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
from nuke_di.types import Client

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)


class ClientConnectError(Exception):
    """
    Carries an error out of a connect task.

    `ConnectError` is a `SystemExit`, and a `SystemExit` raised inside a task escapes the event loop.
    """

    def __init__(self, error: BaseException) -> None:
        super().__init__(str(error))
        self.error = error


class ClientInterruptError(ClientConnectError):
    """
    Carries a client's exit or interrupt without letting it escape the event loop from a child task.
    """


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
        self._connected: list[Client] = []
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
        self._timings = _start_timings(container, clients)
        self._total = len(clients)
        task = asyncio.current_task()
        prior_cancels = 0 if task is None else task.cancelling()

        try:
            return await self._connect(clients, prior_cancels)
        except BaseException as exc:
            await self._rollback(exc, task, prior_cancels)
            error = _reported_error(exc)
            if error is None:
                raise

        # Keep the client's exception chain without the task group's wrapper.
        raise error

    async def client(self, client: Client) -> None:
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
        except BaseException as exc:
            failure = self._failure(timing, exc)
            if failure is None:
                raise
            raise failure from exc

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

    async def _connect(self, clients: list[Client], prior_cancels: int) -> float:
        # Deliver a pending cancellation before starting clients. An already handled request only leaves a count.
        if prior_cancels:
            await asyncio.sleep(0)
        started = time.perf_counter()
        # A client connects once its dependencies have. The first failure cancels clients still connecting or waiting.
        await in_dependency_order(clients, dependencies_among(self._container, clients), self.client)
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

    def _failure(self, timing: ClientTiming, exc: BaseException) -> ClientConnectError | None:
        """
        Log the failed connect with its traceback and prepare the error to report after rollback.
        """
        if isinstance(exc, asyncio.CancelledError):
            # Only a client's own cancellation fails the connect. Its consumers would otherwise wait forever.
            if cancelled_from_outside():
                return None
            timing.connect_outcome = "failed"
        raised = f"{timing.name}.connect() raised {described(exc)}"
        logger.exception("%s", raised, extra=client_fields(timing, timing.connect))
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            return ClientInterruptError(exc)
        if not isinstance(exc, (Exception, asyncio.CancelledError)):
            return None
        error = ConnectError(raised)
        error.__cause__ = exc
        return ClientConnectError(error)


def _start_timings(container: "Dependencies", clients: list[Client]) -> dict[int, ClientTiming]:
    # Computed once: the timings, the logs and the errors of this connect() all name a client the same way
    named = names(container)
    timings = {}
    for client in clients:
        timings[id(client)] = ClientTiming(named[type(client)])
    container._timings = timings
    container.timings = list(timings.values())
    return timings


def _reported_error(exc: BaseException) -> BaseException | None:
    if not isinstance(exc, BaseExceptionGroup):
        return None
    # TaskGroup gives exits and interrupts priority over ordinary failures.
    for failure in exc.exceptions:
        if isinstance(failure, ClientInterruptError):
            return failure.error
    first = exc.exceptions[0]
    if isinstance(exc, ExceptionGroup) and isinstance(first, ClientConnectError):
        # Every failure is logged already, the first one stops the application
        return first.error
    return None


def already_connected(call: str) -> ConnectError:
    return ConnectError(f"{call}: already connected, call disconnect() first")


def described(exc: BaseException) -> str:
    """
    The type of `exc` and its message, if it has one: `OSError: unreachable`, a bare `CancelledError`.
    """
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
