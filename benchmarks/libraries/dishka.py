"""
dishka: a provider of the classes, a container that builds them lazily, and an async one with a lifecycle.
"""

from collections.abc import Callable
from functools import partial
from typing import Any, Final

from dishka import Provider, Scope, make_async_container, make_container
from suite.trees import Tree

from libraries.library import Library, Stop
from libraries.lifecycle import lifecycle, tree_of, warm_up_root
from nuke_di import Client


def container(tree: Tree) -> Any:
    provider = Provider(scope=Scope.APP)
    provider.provide_all(*tree.clients)
    return make_container(provider)


def cold(tree: Tree) -> object:
    return container(tree).get(tree.root)


def warm(tree: Tree) -> Callable[[], object]:
    made = container(tree)
    made.get(tree.root)
    return partial(made.get, tree.root)


async def start(root: type[Client], gather: bool = False) -> Stop:
    provider = Provider(scope=Scope.APP)
    for client_class in tree_of(root):
        provider.provide(lifecycle(client_class))
    # The container connects nothing until a client is asked for. It serializes the `get()` calls with a lock, so
    # gathered ones run one after another unless the lock is off; without it, a client that two gathered branches
    # share is built and connected twice
    if gather:
        made = make_async_container(provider, lock_factory=None)
    else:
        made = make_async_container(provider)
    await warm_up_root(made, root, gather)
    return made.close


DISHKA: Final = Library(
    "dishka",
    "dishka",
    cold,
    warm,
    start=start,
    start_gathered=partial(start, gather=True),
)
