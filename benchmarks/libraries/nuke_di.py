"""
nuke-di: a container that reads the type hints of `__init__` and connects in `connect()`.
"""

from collections.abc import Callable
from functools import partial
from typing import Final

from suite.trees import Tree

from libraries.library import Library, Stop
from nuke_di import Client, Dependencies


def cold(tree: Tree) -> object:
    return Dependencies().resolve(tree.root)


def warm(tree: Tree) -> Callable[[], object]:
    deps = Dependencies()
    deps.resolve(tree.root)
    return partial(deps.resolve, tree.root)


async def start(root: type[Client]) -> Stop:
    deps = Dependencies()
    deps.resolve(root)
    await deps.connect()
    return deps.disconnect


NUKE_DI: Final = Library("nuke-di", "nuke-di", cold, warm, start=start)
