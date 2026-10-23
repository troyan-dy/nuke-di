"""
One disconnect() of a container, or the rollback of a connect() that failed: every client after its consumers.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from nuke_di._container.order import (
    cancelled_from_outside,
    dependencies_among,
    in_dependency_order,
    make_limiter,
    measure,
    within,
)
from nuke_di.errors import ConnectError
from nuke_di.logs import CONTAINER_LOGGER, client_fields, fields
from nuke_di.naming import count
from nuke_di.timings import ClientTiming
from nuke_di.types import NotSingletonClient

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)


async def disconnect_all(container: "Dependencies") -> None:
    if not container.connected:
        raise ConnectError("disconnect(): already disconnected")

    await Disconnect(container, container.connect_clients).run()


class Disconnect:
    """
    The run of one disconnect: the clients disconnect in the reverse of the connect order, under one deadline.
    """

    def __init__(self, container: "Dependencies", to_disconnect: list[NotSingletonClient]) -> None:
        self._container = container
        self._to_disconnect = to_disconnect
        settings = container.settings
        self._limiter = make_limiter(settings)
        self._timeout = settings.disconnect_timeout
        # Clients whose disconnect() ended, for the progress in the records
        self._disconnected: list[NotSingletonClient] = []
        # Clients whose turn came after the deadline, in disconnect order
        self._skipped: list[NotSingletonClient] = []
        self._total = 0
        self._deadline: float = 0
        self._timings: dict[int, ClientTiming] = {}

    async def run(self) -> None:
        container = self._container
        with container._lock:
            container.connected = False
            clients = list(self._to_disconnect)
            # Read with the flag: once it is down, another thread may flush() the dependencies away
            dependencies = dependencies_among(container, clients)
        self._total = len(clients)
        self._timings = container._timings
        # One budget for the whole disconnect, whatever the depth of the tree
        self._deadline = asyncio.get_running_loop().time() + container.settings.disconnect_deadline

        # A client disconnects once its consumers have, however their disconnect() ended. Only a cancellation of
        # disconnect() itself ends it early: `client()` logs and swallows what a client raises
        try:
            await in_dependency_order(clients, consumers_of(clients, dependencies), self.client)
        except BaseException:
            self._end()
            raise
        self._end()

    async def client(self, client: NotSingletonClient) -> None:
        timing = self._timings[id(client)]
        debug = logger.isEnabledFor(logging.DEBUG)
        timeout = self._timeout
        # Whether the deadline of the whole disconnect, not the client's own timeout, is the bound that expires
        cut_off = False
        try:
            async with self._limiter:
                remaining = self._deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    # Never started: its timing keeps None, and returning releases its dependencies to be skipped too
                    self._skipped.append(client)
                    return
                cut_off = remaining < timeout
                if debug:
                    logger.debug(
                        "Disconnecting client %s (%d/%d disconnected)",
                        timing.name,
                        len(self._disconnected),
                        self._total,
                        extra=client_fields(timing),
                    )
                with measure(timing, "disconnect", done=self._disconnected, client=client):
                    # The bound that expires first: the deadline when `cut_off`
                    await within(min(remaining, timeout), client.disconnect())
        except asyncio.CancelledError:
            # A CancelledError of the client's own, re-raised from a task it awaited, ends its disconnect() alone: its
            # dependencies still have to be stopped. A cancellation of disconnect() itself goes on.
            if cancelled_from_outside():
                raise
            self._log_failure(timing, "%s.disconnect() raised CancelledError")
        except TimeoutError:
            self._log_timeout(timing, cut_off=cut_off)
        except Exception as exc:
            # The client failed, but the rest still have to be stopped
            self._log_failure(timing, "%s.disconnect() raised %s: %s", type(exc).__name__, exc)
        else:
            if debug:
                logger.debug(
                    "Disconnected client %s in %.3fs (%d/%d disconnected)",
                    timing.name,
                    timing.disconnect,
                    len(self._disconnected),
                    self._total,
                    extra=client_fields(timing, timing.disconnect),
                )

    def _log_timeout(self, timing: ClientTiming, *, cut_off: bool) -> None:
        if cut_off:
            self._log_failure(
                timing,
                "%s did not disconnect before DISCONNECT_DEADLINE_SECONDS ran out (%gs)",
                self._container.settings.disconnect_deadline,
            )
        else:
            self._log_failure(
                timing,
                "%s did not disconnect within %gs (DISCONNECT_TIMEOUT_SECONDS)",
                self._timeout,
            )

    def _log_failure(self, timing: ClientTiming, message: str, *args: object) -> None:
        """
        Log the exception being handled, with the traceback, as the failure of `timing`'s client to disconnect.
        """
        logger.exception(
            message,
            timing.name,
            *args,
            extra=client_fields(timing, timing.disconnect),
        )

    def _end(self) -> None:
        # A cancellation after the deadline still reports the clients it skipped
        if self._skipped:
            skipped = [self._timings[id(client)].name for client in self._skipped]
            logger.error(
                "Skipped disconnecting %s after DISCONNECT_DEADLINE_SECONDS (%gs): %s",
                count(len(skipped), "client"),
                self._container.settings.disconnect_deadline,
                ", ".join(skipped),
                extra=fields(),
            )
        # Cancelled or not, the container keeps no half-disconnected client for the next connect() to reuse
        self._container.flush()


def consumers_of(
    clients: list[NotSingletonClient],
    dependencies: dict[int, list[int]],
) -> dict[int, list[int]]:
    """
    The consumers of every client of `clients` among them, by `id()`, from the `dependencies` of each.
    """
    consumers: dict[int, list[int]] = {key: [] for key in map(id, clients)}
    for consumer, ids in dependencies.items():
        for dependency in ids:
            consumers[dependency].append(consumer)
    return consumers
