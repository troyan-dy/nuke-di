"""
What benchmarks/compare.py knows of a library: how it registers and resolves a tree, and starts and stops it.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeAlias

from suite.trees import Tree

from nuke_di import Client

# Slow connections: `start(root)` connects every client of the tree through the library's async lifecycle and
# returns the shutdown
Stop: TypeAlias = Callable[[], Awaitable[None]]
Start: TypeAlias = Callable[[type[Client]], Awaitable[Stop]]


@dataclass(frozen=True)
class Library:
    """
    Registering and resolving: `cold(tree)` builds a container and gets the root; `warm(tree)` builds one, gets
    the root and returns a function that gets it again.
    """

    name: str
    package: str
    cold: Callable[[Tree], object]
    warm: Callable[[Tree], Callable[[], object]]
    # Decorates the classes of a tree the way the library wants them, once, as a user does at import
    prepare: Callable[[Tree], None] = lambda tree: None
    # None: the library has no async lifecycle
    start: Start | None = None
    # The startup with the root's arguments got by `asyncio.gather()` by hand, for the lazy containers
    start_gathered: Start | None = None
