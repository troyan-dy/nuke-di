"""
The Replacements of `mock()` and `override()`: the checks before one is registered, and the end of an `override()`
block.
"""

from typing import TYPE_CHECKING, Any, TypeAlias

from nuke_di._container.messages import names, state_error
from nuke_di.errors import ConnectError
from nuke_di.types import NotSingletonClient

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

# The class a Replacement stands in for, and the Replacement
_Replacement: TypeAlias = tuple[type[NotSingletonClient], NotSingletonClient]


def check_mockable(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
    new: NotSingletonClient | None,
) -> NotSingletonClient | None:
    """
    Refuse a `mock()` that would not take effect; the Replacement registered for `client_class` already, if any.
    """
    name = names(container, client_class)[client_class]
    if container.connected:
        raise state_error(f"mock({name})")

    current = container._replacements.get(client_class)
    if current is not None:
        if new is not None and new is not current:
            raise ConnectError(f"{name} already has a replacement")
        return current
    # Consumers resolved before would keep the real client while the caller holds the Replacement
    if is_resolved(container, client_class):
        raise ConnectError(f"{name} is already resolved, call mock() before resolve() or inject()")
    return None


def check_overridable(container: "Dependencies", client_class: type[NotSingletonClient]) -> str:
    """
    Refuse an `override()` block whose exit would drop clients or a Replacement it did not register; the name of
    `client_class`.
    """
    name = names(container, client_class)[client_class]
    if container.connected:
        raise state_error(f"override({name})")
    if container.connect_clients:
        # Flushing them on exit would silently drop what the caller resolved before the block
        named = names(container)
        resolved = sorted(named[type(client)] for client in container.connect_clients)
        listed = ", ".join(resolved)
        raise ConnectError(f"override({name}) needs a container without resolved clients, found: {listed}")
    if client_class in container._replacements:
        raise ConnectError(f"{name} already has a replacement")
    return name


def close_override(container: "Dependencies", entry: _Replacement) -> None:
    with container._lock:
        container._overrides = [other for other in container._overrides if other is not entry]
        # A connected container is flushed by its disconnect()
        if not container.connected:
            container.flush()


def exit_override(container: "Dependencies", entry: _Replacement, name: str) -> None:
    """
    Close the `override()` block of `entry` that ended without an exception, which it does with the container
    disconnected.
    """
    close_override(container, entry)
    if container.connected:
        raise ConnectError(f"override({name}) exited while the container is connected")


def register_replacement(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
    replacement: NotSingletonClient,
) -> None:
    container.clients[client_class] = replacement
    container._replacements[client_class] = replacement


def is_resolved(container: "Dependencies", client_class: type[NotSingletonClient]) -> bool:
    # A NotSingletonClient is never cached in `clients`, only its instances are in `connect_clients`
    return client_class in container.clients or client_class in map(type, container.connect_clients)


def autospec(client_class: type[NotSingletonClient]) -> Any:
    """
    An autospec mock of an instance of `client_class`.
    """
    # unittest costs every process a few milliseconds at import, and only tests mock
    from unittest.mock import create_autospec

    # A Replacement stands in for an instance: calling it is a TypeError, not another mock
    return create_autospec(client_class, instance=True)
