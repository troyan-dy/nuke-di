"""
The `inject` and `overrides` scenarios: a function that takes clients, and a tree with a leaf replaced.
"""

from collections.abc import Iterator
from functools import partial
from typing import Final

from nuke_di import Client, Dependencies
from suite.measure import Measurement, measured
from suite.shapes import mixed

# The calls per sample of a call that takes nanoseconds
_CALLS: Final = 100_000


class Database(Client):
    """
    A no-op client of the injected function.
    """


class Cache(Client):
    """
    The other no-op client of the injected function.
    """


def handler(db: Database, cache: Cache) -> tuple[Database, Cache]:
    return db, cache


def inject_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    deps = Dependencies()
    injected = deps.inject(handler)
    db = deps.resolve(Database)
    cache = deps.resolve(Cache)

    figure = partial(Measurement, shape="2 clients", size=None)
    yield figure("inject(), clients resolved", samples=measured(repeat, deps.inject, 1000, handler))
    yield figure("call of the injected function", samples=measured(repeat, injected, _CALLS))
    yield figure("call of the plain function", samples=measured(repeat, handler, _CALLS, db, cache))


def mock_cycle(deps: Dependencies, leaf: type[Client], root: type[Client]) -> None:
    deps.flush()
    deps.mock(leaf)
    deps.resolve(root)


def override_cycle(deps: Dependencies, leaf: type[Client], fake: Client, root: type[Client]) -> None:
    with deps.override(leaf, fake):
        deps.resolve(root)


def overrides_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    for size in sizes:
        tree = mixed(size)
        leaf = tree.clients[0]
        shape = "mixed, a leaf replaced"

        samples = measured(repeat, mock_cycle, 1, Dependencies(), leaf, tree.root)
        # The autospec of mock() costs the same at every N
        yield Measurement("flush() + mock() + resolve()", shape, size, samples, per_client=False)

        # override() needs a container without resolved clients and flushes it on exit
        samples = measured(repeat, override_cycle, 1, Dependencies(), leaf, leaf(), tree.root)
        yield Measurement("override() block + resolve()", shape, size, samples)
