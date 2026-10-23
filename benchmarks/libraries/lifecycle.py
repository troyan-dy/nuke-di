"""
The async lifecycle of a client in the libraries without one of their own: what a user writes for each client to
connect it through dishka, wireup or dependency-injector.
"""

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Any, get_type_hints

from nuke_di import Client


def init_arguments(client_class: type[Client]) -> dict[str, type[Client]]:
    """
    The arguments of `client_class.__init__` and the client each one takes.
    """
    hints = get_type_hints(client_class.__init__).items()
    return {name: hint for name, hint in hints if name != "return"}


def tree_of(root: type[Client]) -> list[type[Client]]:
    """
    Every client under `root`, dependencies before their consumers.
    """
    found: dict[type[Client], None] = {}
    _visit(root, found)
    return list(found)


def _visit(client_class: type[Client], found: dict[type[Client], None]) -> None:
    if client_class not in found:
        for dependency in init_arguments(client_class).values():
            _visit(dependency, found)
        found[client_class] = None


def lifecycle(client_class: type[Client]) -> Callable[..., AsyncIterator[Client]]:
    """
    The async generator that dishka, wireup and dependency-injector take for a resource with a lifecycle: it
    builds the client, awaits `connect()`, yields it and awaits `disconnect()` at close. A user writes one per
    client; here its signature is the one of `client_class.__init__`, so the libraries that read it find the same
    dependencies by type hint.
    """

    async def factory(**kwargs: Any) -> AsyncIterator[Client]:
        instance = client_class(**kwargs)
        await instance.connect()
        yield instance
        await instance.disconnect()

    hints = init_arguments(client_class)
    returned = AsyncIterator[client_class]  # type: ignore[valid-type]
    factory.__signature__ = _signature(hints, returned)  # type: ignore[attr-defined]
    factory.__annotations__ = {**hints, "return": returned}
    name = f"make_{client_class.__name__}"
    factory.__name__ = name
    factory.__qualname__ = name
    return factory


def _signature(hints: dict[str, type[Client]], returned: object) -> inspect.Signature:
    """
    A signature of keyword-only arguments, the names and the type hints of `hints`.
    """
    argument = partial(inspect.Parameter, kind=inspect.Parameter.KEYWORD_ONLY)
    arguments = [argument(name, annotation=hint) for name, hint in hints.items()]
    return inspect.Signature(arguments, return_annotation=returned)


async def warm_up_root(container: Any, root: type[Client], gather: bool) -> None:
    """
    Get the root at startup, so that the lazy containers of dishka and wireup connect then and not in the first
    request; with `gather`, its arguments first with `asyncio.gather()`, as an application can write by hand to
    get its branches concurrently.
    """
    if gather:
        branches = init_arguments(root).values()
        await asyncio.gather(*(container.get(client_class) for client_class in branches))
    await container.get(root)
