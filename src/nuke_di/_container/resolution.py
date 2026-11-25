"""
Resolution with the container's lock held: of one client class, and of the clients of an injected function.
"""

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, cast

from nuke_di._container import binding, hints
from nuke_di._container.messages import state_error
from nuke_di._container.signatures import injected_arguments
from nuke_di._container.tree import resolve_tree
from nuke_di.logs import CONTAINER_LOGGER, fields
from nuke_di.naming import sname
from nuke_di.types import Client, ClientT

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)

ReturnT = TypeVar("ReturnT")


def resolve_locked(container: "Dependencies", client_class: type[ClientT]) -> ClientT:
    """
    Build `client_class` with the lock held: the caller holds it for the whole tree.
    """
    # Checked again under the lock: another thread may have built it while this one waited
    entry = container._clients.get(client_class)
    if entry is not None and container._state != "disconnecting":
        return cast(ClientT, entry.client)
    if container.connected:
        raise hints.resolve_refused(client_class, container._state)

    # Not always 0: a client's `__init__` that resolves on its own runs inside its consumer's frame, and only the
    # frames opened from here are this call's to close
    opened = len(container._resolving)
    try:
        return cast(ClientT, resolve_tree(container, client_class))
    except BaseException:
        # The frames a failure leaves open are off the path, as a recursion's `finally` would leave them: the next
        # resolve sees no stale cycle and names a path of its own. In place: the open frames hold the same list.
        resolving = container._resolving
        while len(resolving) > opened:
            container._resolving_set.discard(resolving.pop())
        raise


def inject_function(container: "Dependencies", func: Callable[..., ReturnT]) -> Callable[..., ReturnT]:
    """
    `func` with the clients of its signature bound, resolved with the lock held.
    """
    if container.connected:
        raise state_error(f"inject({sname(func)})")
    name = sname(func)
    logger.debug('Parsing signature of func "%s"', name, extra=fields())
    lifetime = container._binding_lifetime
    arguments = injected_arguments(container, func)

    resolved = _resolve_arguments(container, func, arguments)
    bound = partial(func, **resolved)
    return binding.guarded_binding(bound, lifetime) if resolved else bound


def _resolve_arguments(
    container: "Dependencies",
    func: Callable[..., Any],
    arguments: dict[str, type[Client]],
) -> dict[str, Client]:
    # A client may inject() on its own while this one resolves, so the outer root is restored after it
    outer = container._injecting
    container._injecting = func
    signature: dict[str, Client] = {}
    try:
        for key, client_class in arguments.items():
            signature[key] = resolve_locked(container, client_class)
    except BaseException:
        container._injecting = outer
        raise
    container._injecting = outer

    return signature
