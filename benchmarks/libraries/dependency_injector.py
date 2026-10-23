"""
dependency-injector: a provider per class on a container, its arguments named by hand, and a `Resource` per client
for the lifecycle.
"""

from collections.abc import Callable
from dataclasses import fields
from functools import partial
from typing import Any, Final

from dependency_injector import containers, providers
from suite.trees import Tree

from libraries.library import Library, Stop
from libraries.lifecycle import init_arguments, lifecycle, tree_of
from nuke_di import Client


def root_provider(tree: Tree) -> Any:
    """
    A `Singleton` provider per class on a `DynamicContainer`, wired by the names of the `__init__` arguments.
    """
    container = containers.DynamicContainer()
    made: dict[str, Any] = {}
    for client_class in tree.clients:
        # The library reads no annotation: the user names the provider of every argument
        provider = providers.Singleton(client_class, **_wired(client_class, made))
        setattr(container, client_class.__name__, provider)
        made[client_class.__name__] = provider
    return made[tree.root.__name__]


def _wired(
    client_class: type[Client],
    made: dict[str, Any],
) -> dict[str, Any]:
    """
    The provider of every argument of `client_class` by name. The type of a field is the class, or its name when
    the annotations are strings, and the providers are keyed by name.
    """
    wired = {}
    for field in fields(client_class):  # type: ignore[arg-type]
        hint = field.type
        name = hint if isinstance(hint, str) else str(hint.__name__)
        wired[field.name] = made[name]
    return wired


def cold(tree: Tree) -> object:
    return root_provider(tree)()


def warm(tree: Tree) -> Callable[[], object]:
    root = root_provider(tree)
    root()
    return root  # type: ignore[no-any-return]


async def start(root: type[Client]) -> Stop:
    container = containers.DynamicContainer()
    made: dict[type[Client], Any] = {}
    for client_class in tree_of(root):
        resource = _resource(client_class, made)
        setattr(container, client_class.__name__, resource)
        made[client_class] = resource
    await container.init_resources()  # type: ignore[misc]
    return partial(_shutdown, container)


def _resource(client_class: type[Client], made: dict[type[Client], Any]) -> Any:
    # A `Resource` per client, its arguments named by hand as every provider of the library
    hints = init_arguments(client_class)
    arguments = {name: made[hint] for name, hint in hints.items()}
    return providers.Resource(lifecycle(client_class), **arguments)


async def _shutdown(container: containers.DynamicContainer) -> None:
    await container.shutdown_resources()  # type: ignore[misc]


DEPENDENCY_INJECTOR: Final = Library("dependency-injector", "dependency-injector", cold, warm, start=start)
