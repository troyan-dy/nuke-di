"""
How the container's errors and records name the clients, and the path that led to one.
"""

import logging
from typing import TYPE_CHECKING

from nuke_di.errors import ConnectError, InitializeDependencyError, InvalidSignatureError
from nuke_di.logs import CONTAINER_LOGGER, fields
from nuke_di.naming import display_names, sname
from nuke_di.types import NotSingletonClient

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)


def names(container: "Dependencies", *more: type) -> dict[type, str]:
    """
    The display name of every class the container holds or is resolving, and of `more`.

    Built on demand, for an error or a Replacement: the clients are scanned once per call, which a resolve()
    of every client cannot afford.
    """
    resolved = map(type, container.connect_clients)
    return display_names({*container.clients, *resolved, *container._resolving, *more})


def path(container: "Dependencies", *more: type[NotSingletonClient]) -> str:
    classes = [*container._resolving, *more]
    named = names(container, *classes)
    root = [] if container._injecting is None else [sname(container._injecting)]
    return " -> ".join([*root, *(named[client_class] for client_class in classes)])


def path_suffix(container: "Dependencies", *more: type[NotSingletonClient]) -> str:
    # A root alone is no path: `more` is the client that left `_resolving` before it was built
    if container._injecting is None and not container._resolving:
        return ""
    return f" (resolving {path(container, *more)})"


def hints_error(container: "Dependencies", name: str, exc: NameError) -> InvalidSignatureError:
    suffix = path_suffix(container)
    return InvalidSignatureError(
        f'Cannot evaluate the type hints of "{name}": {exc}; a type hint must name something the module '
        f"defines or imports at runtime, not a class local to a function or imported under TYPE_CHECKING"
        f"{suffix}"
    )


def signature_error(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
    name: str,
    reason: str,
) -> InvalidSignatureError:
    owner = names(container, client_class)[client_class]
    suffix = path_suffix(container)
    return InvalidSignatureError(f'Argument "{name}" of "{owner}.__init__" {reason}{suffix}')


def state_error(call: str) -> ConnectError:
    """
    `call` builds the tree, and the container is connected: the message names the call, the state and the way out.
    """
    return ConnectError(
        f"{call}: the container is already connected; resolve, inject, mock and override only work before "
        f"connect(), flush() after disconnect()"
    )


def init_error(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
    exc: Exception,
) -> InitializeDependencyError:
    """
    The error of an `__init__` that raised `exc`, logged with the path that led to it.
    """
    # `client_class` left the path when its arguments were resolved, so it is named at the end of it here
    name = names(container, client_class)[client_class]
    # The path before the cause: a cause such as a pydantic error spans lines
    suffix = path_suffix(container, client_class)
    where = f"{name}.__init__ raised {type(exc).__name__}{suffix}"
    logger.exception("%s: %s", where, exc, extra=fields(client=name))
    return InitializeDependencyError(f"{where}: {exc}")
