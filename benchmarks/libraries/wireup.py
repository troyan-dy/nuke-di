"""
wireup: the classes marked injectable, a container that builds them lazily, and an async one with a lifecycle.
"""

from collections.abc import Callable
from functools import partial
from typing import Any, Final

import wireup
from suite.trees import Tree

from libraries.library import Library, Stop
from libraries.lifecycle import lifecycle, tree_of, warm_up_root
from nuke_di import Client


def prepare(tree: Tree) -> None:
    for client_class in tree.clients:
        wireup.injectable(client_class)


def container(tree: Tree) -> Any:
    return wireup.create_sync_container(injectables=list(tree.clients))


def cold(tree: Tree) -> object:
    return container(tree).get(tree.root)


def warm(tree: Tree) -> Callable[[], object]:
    made = container(tree)
    made.get(tree.root)
    return partial(made.get, tree.root)


async def start(root: type[Client], gather: bool = False) -> Stop:
    # Lazy as dishka
    injectables = [wireup.injectable(lifecycle(client_class)) for client_class in tree_of(root)]
    made = wireup.create_async_container(injectables=injectables)
    await warm_up_root(made, root, gather)
    return made.close


WIREUP: Final = Library(
    "wireup",
    "wireup",
    cold,
    warm,
    prepare,
    start=start,
    start_gathered=partial(start, gather=True),
)
