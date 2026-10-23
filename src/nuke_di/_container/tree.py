"""
The cold resolution: a client and its dependencies built in one loop, for trees deeper than the recursion limit.
"""

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, TypeAlias

from nuke_di._container.messages import init_error, path
from nuke_di._container.signatures import client_arguments
from nuke_di.errors import CircularDependencyError
from nuke_di.logs import CONTAINER_LOGGER, fields
from nuke_di.naming import qualname
from nuke_di.types import NotSingletonClient, isclient

if TYPE_CHECKING:
    from nuke_di.core import Dependencies

logger = logging.getLogger(CONTAINER_LOGGER)

# An argument of an `__init__` and the class it asks for
_ClientArgument: TypeAlias = tuple[str, type[NotSingletonClient]]
# A client waiting for a dependency in `resolve_tree()`: its class, the arguments of its `__init__` to fill with
# clients, the clients found so far, the arguments left (an iterator: the index of the next one) and the argument the
# dependency fills. A plain tuple, for speed: on 3.14 a NamedTuple cost a chain 10-13% per client and methods that
# open and close a frame 8-10%, measured.
Frame: TypeAlias = tuple[
    type[NotSingletonClient],
    dict[str, type[NotSingletonClient]],
    dict[str, NotSingletonClient],
    Iterator[_ClientArgument],
    str,
]
# Exhausted: the cursor of every client without arguments
NO_ARGUMENTS: Iterator[_ClientArgument] = iter(())


def resolve_tree(container: "Dependencies", client_class: type[NotSingletonClient]) -> NotSingletonClient:
    """
    Build `client_class` and its dependencies, each before its consumer, on a stack of frames instead of the call
    stack.

    A call per client of a chain would stop it at the recursion limit, a few hundred clients long; the frames grow
    with the chain. A frame is a client whose arguments are being resolved, and `_resolving` is the class of every
    open frame, outermost first, so the cycle check and the path in errors are what a recursion had.
    """
    resolving, resolving_set = container._resolving, container._resolving_set
    # The open frames but the innermost, outermost first, each waiting for the client of its argument `key`
    waiting: list[Frame] = []
    while True:
        # Open a frame for `client_class`: on the path from here on, so a cycle back to it and a signature error
        # name it
        if client_class in resolving_set:
            raise CircularDependencyError(f"Circular dependency: {path(container, client_class)}")
        if logger.isEnabledFor(logging.DEBUG):
            name = qualname(client_class)
            logger.debug('Resolving dependency "%s"', name, extra=fields(client=name))
        resolving.append(client_class)
        resolving_set.add(client_class)
        arguments = client_arguments(container, client_class)
        init: dict[str, NotSingletonClient] = {}
        # Most clients of a tree take no client, and share one exhausted cursor
        pending = iter(arguments.items()) if arguments else NO_ARGUMENTS

        while True:
            # The innermost frame takes the clients that exist; a missing one is built first, in a frame of its own.
            # The `continue` of the else resumes this `while`, skipping the `break` that opens that frame.
            for key, dep in pending:  # noqa: WPS327
                # Not kept in a local: an `__init__` that flushes the container replaces the dict
                inst = container.clients.get(dep)
                if inst is None:
                    # Before the arguments after it, which is the order of a recursion
                    waiting.append((client_class, arguments, init, pending, key))
                    client_class = dep
                    break
                init[key] = inst
            else:
                # Every argument is in `init`: the client is built and its frame closed. Off the path before
                # `__init__`, so an `__init__` that resolves on its own sees the path of its consumers.
                resolving.pop()
                resolving_set.remove(client_class)
                try:
                    inst = client_class(**init)
                except Exception as exc:
                    raise init_error(container, client_class, exc) from exc

                dependencies = {arg: (arguments[arg], init[arg]) for arg in init}
                container._dependencies[id(inst)] = dependencies
                container.connect_clients.append(inst)
                # Published last: the lock-free lookup of resolve() hands out a singleton that the container
                # accounts for
                if isclient(client_class):
                    container.clients[client_class] = inst

                if not waiting:
                    return inst
                # The consumer resumes at its next argument
                client_class, arguments, init, pending, key = waiting.pop()
                init[key] = inst
                continue
            # A frame opens for the dependency the for loop broke at
            break
