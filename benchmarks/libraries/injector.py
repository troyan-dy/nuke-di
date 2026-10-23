"""
injector: `__init__` decorated with `@inject`, a singleton binding per class; no async lifecycle.
"""

from collections.abc import Callable
from functools import partial
from typing import Final

from injector import Binder, Injector, inject, singleton
from suite.trees import Tree

from libraries.library import Library
from nuke_di import Client


def prepare(tree: Tree) -> None:
    for client_class in tree.clients:
        client_class.__init__ = inject(client_class.__init__)  # type: ignore[method-assign]


def _configure(clients: list[type[Client]], binder: Binder) -> None:
    for client_class in clients:
        binder.bind(client_class, to=client_class, scope=singleton)


def container(tree: Tree) -> Injector:
    return Injector([partial(_configure, tree.clients)])


def cold(tree: Tree) -> object:
    return container(tree).get(tree.root)


def warm(tree: Tree) -> Callable[[], object]:
    made = container(tree)
    made.get(tree.root)
    return partial(made.get, tree.root)


INJECTOR: Final = Library("injector", "injector", cold, warm, prepare)
