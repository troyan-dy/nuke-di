"""
One disconnect() of a container, or the rollback of a connect() that failed: every client after its consumers.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from nuke_di._container.messages import names
from nuke_di._container.order import (
    LifecycleCancellation,
    dependencies_among,
    in_dependency_order,
    within,
)
from nuke_di.logs import CONTAINER_LOGGER, client_fields, fields
from nuke_di.naming import count
from nuke_di.types import Client

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)


async def disconnect_all(container: "Dependencies") -> None:
    if not container.connected:
        raise RuntimeError("disconnect(): already disconnected")

    await Disconnect(container, container.connect_clients).run()


class Disconnect:
    """
    The run of one disconnect: the clients disconnect in the reverse of the connect order, under one deadline.
    """

    def __init__(self, container: "Dependencies", to_disconnect: list[Client]) -> None:
        self._container = container
        self._cancellation = LifecycleCancellation()
        self._to_disconnect = to_disconnect
        settings = container.settings
        self._timeout = settings.disconnect_timeout
        # Clients whose disconnect() ended, for the progress in the records
        self._disconnected = 0
        # Clients whose turn came after the deadline, in disconnect order
        self._skipped: list[str] = []
        self._total = 0
        self._deadline: float = 0
        self._names: dict[type, str] = {}

    async def run(self) -> None:
        container = self._container
        with container._lock:
            container.connected = False
            clients = list(self._to_disconnect)
            # Read with the flag: once it is down, another thread may flush() the dependencies away
            dependencies = dependencies_among(container, clients)
            self._names = names(container)
        self._total = len(clients)
        # One budget for the whole disconnect, whatever the depth of the tree
        self._deadline = asyncio.get_running_loop().time() + container.settings.disconnect_deadline
        consumers = consumers_of(clients, dependencies)

        # A client disconnects once its consumers have, however their disconnect() ended. Only a cancellation of
        # disconnect() itself ends it early: `client()` logs and swallows what a client raises
        try:
            await in_dependency_order(clients, consumers, self.client, self._cancellation.prior_requests)
        except BaseException:
            self._end()
            raise
        self._end()

    async def client(self, client: Client) -> None:
        name = self._names[type(client)]
        remaining = self._deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            self._skipped.append(name)
            return

        debug = logger.isEnabledFor(logging.DEBUG)
        if debug:
            logger.debug(
                "Disconnecting client %s (%d/%d disconnected)",
                name,
                self._disconnected,
                self._total,
                extra=client_fields(name),
            )
        started = time.perf_counter()
        try:
            await within(min(remaining, self._timeout), client.disconnect())
        except asyncio.CancelledError:
            # A client may raise its own CancelledError. The clients it uses still need to disconnect.
            if self._cancellation.group_is_cancelling():
                raise
            logger.exception("%s.disconnect() raised CancelledError", name, extra=client_fields(name))
        except TimeoutError:
            duration = time.perf_counter() - started
            self._log_timeout(name, duration, cut_off=remaining < self._timeout)
        except Exception as exc:
            logger.exception(
                "%s.disconnect() raised %s: %s",
                name,
                type(exc).__name__,
                exc,
                extra=client_fields(name, time.perf_counter() - started),
            )
        else:
            self._log_success(name, started)
        finally:
            self._disconnected += 1

    def _log_success(self, name: str, started: float) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            duration = time.perf_counter() - started
            logger.debug(
                "Disconnected client %s in %.3fs (%d/%d disconnected)",
                name,
                duration,
                self._disconnected + 1,
                self._total,
                extra=client_fields(name, duration),
            )

    def _log_timeout(self, name: str, duration: float, *, cut_off: bool) -> None:
        if cut_off:
            logger.exception(
                "%s did not disconnect before DISCONNECT_DEADLINE_SECONDS ran out (%gs)",
                name,
                self._container.settings.disconnect_deadline,
                extra=client_fields(name, duration),
            )
        else:
            logger.exception(
                "%s did not disconnect within %gs (DISCONNECT_TIMEOUT_SECONDS)",
                name,
                self._timeout,
                extra=client_fields(name, duration),
            )

    def _end(self) -> None:
        # A cancellation after the deadline still reports the clients it skipped
        if self._skipped:
            logger.error(
                "Skipped disconnecting %s after DISCONNECT_DEADLINE_SECONDS (%gs): %s",
                count(len(self._skipped), "client"),
                self._container.settings.disconnect_deadline,
                ", ".join(self._skipped),
                extra=fields(),
            )
        # Cancelled or not, the container keeps no half-disconnected client for the next connect() to reuse
        self._container.flush()


def consumers_of(
    clients: list[Client],
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
