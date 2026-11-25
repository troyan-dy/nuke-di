"""
The Replacements of `mock()` and `override()`: the checks before one is registered, and the end of an `override()`
block.
"""

from typing import TYPE_CHECKING, Any, TypeAlias

from nuke_di._container.messages import names, state_error
from nuke_di._container.registry import Registration
from nuke_di.types import Client

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

# The class a Replacement stands in for, and the Replacement
_Replacement: TypeAlias = tuple[type[Client], Client]


def check_mockable(
    container: "Dependencies",
    client_class: type[Client],
    new: Client | None,
) -> Client | None:
    """
    Refuse a `mock()` that would not take effect; the Replacement registered for `client_class` already, if any.
    """
    name = names(container, client_class)[client_class]
    if container.connected:
        raise state_error(f"mock({name})")

    entry = container._clients.get(client_class)
    if entry is None:
        return None
    if entry.dependencies is not None:
        raise RuntimeError(f"{name} is already resolved, call mock() before resolve() or inject()")
    if new is not None and new is not entry.client:
        raise RuntimeError(f"{name} already has a replacement")
    return entry.client


def check_overridable(container: "Dependencies", client_class: type[Client]) -> str:
    """
    Refuse an `override()` block whose exit would drop clients or a Replacement it did not register; the name of
    `client_class`.
    """
    name = names(container, client_class)[client_class]
    if container.connected:
        raise state_error(f"override({name})")
    resolved = container._lifecycle_clients()
    if resolved:
        named = names(container)
        listed = ", ".join(sorted(named[key] for key in resolved))
        raise RuntimeError(f"override({name}) needs a container without resolved clients, found: {listed}")
    if client_class in container._clients:
        raise RuntimeError(f"{name} already has a replacement")
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
        raise RuntimeError(f"override({name}) exited while the container is connected")


def register_replacement(
    container: "Dependencies",
    client_class: type[Client],
    replacement: Client,
) -> None:
    container._clients[client_class] = Registration(replacement, None)


def autospec(client_class: type[Client]) -> Any:
    """
    An autospec mock of an instance of `client_class`.
    """
    # unittest costs every process a few milliseconds at import, and only tests mock
    from unittest.mock import create_autospec

    # A Replacement stands in for an instance: calling it is a TypeError, not another mock
    return create_autospec(client_class, instance=True)
