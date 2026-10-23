"""
The `connect` scenario: connect() and disconnect() of a container against the same coroutines without it.

The references without the container await one coroutine after another, which is what they time, so they live
in benchmarks/run.py and come in as arguments.
"""

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from functools import partial
from types import MappingProxyType
from typing import Any, Final, TypeAlias

from nuke_di import Client, Dependencies
from suite.application import APPLICATION, APPLICATION_SIZE, Api
from suite.measure import Measurement, collect, timed
from suite.shapes import deep
from suite.trees import client

# A run of the clients without the container: the seconds it took
_Seconds: TypeAlias = Coroutine[Any, Any, float]
Reference: TypeAlias = Callable[[list[Any]], _Seconds]


async def connect_and_disconnect(deps: Dependencies) -> None:
    await deps.connect()
    await deps.disconnect()


def connect_sample(
    roots: list[type[Client]],
    library: list[float],
    direct: list[float],
    reference: Reference,
) -> float:
    """
    One connect and disconnect of a container with `roots` resolved, then the same clients through
    `reference` without the container; the sample is the difference, the two figures go into `library`
    and `direct`.
    """
    deps = Dependencies()
    for root in roots:
        deps.resolve(root)
    clients = list(deps.connect_clients)
    library.append(asyncio.run(timed(connect_and_disconnect, deps)))
    direct.append(asyncio.run(reference(clients)))
    return library[-1] - direct[-1]


def independent(size: int) -> list[type[Client]]:
    return [client(f"Independent{number}") for number in range(size)]


def chain(size: int) -> list[type[Client]]:
    return [deep(size).root]


_TREES: Final = MappingProxyType({"wide: N independent clients": independent, "deep: a chain of N": chain})


def connect_scenario(
    sizes: list[int],
    repeat: int,
    *,
    ideal: Reference,
    critical_path: Reference,
) -> Iterator[Measurement]:
    """
    `ideal` awaits the coroutines of the clients one after another, `critical_path` schedules them by hand.
    """
    for shape, build in _TREES.items():
        for size in sizes:
            yield from _tree_figures(shape, build(size), size, repeat, ideal)
    yield from _application_figures(repeat, critical_path)


def _tree_figures(
    shape: str,
    roots: list[type[Client]],
    size: int,
    repeat: int,
    ideal: Reference,
) -> Iterator[Measurement]:
    library: list[float] = []
    direct: list[float] = []
    overhead = collect(partial(connect_sample, roots, library, direct, ideal), repeat)
    # The warm-up is the first figure of both lists
    yield Measurement("connect() + disconnect()", shape, size, library[1:])
    yield Measurement("connect() + disconnect(), ideal: coroutines awaited directly", shape, size, direct[1:])
    yield Measurement("connect() + disconnect(), overhead above the ideal", shape, size, overhead)


def _application_figures(repeat: int, critical_path: Reference) -> Iterator[Measurement]:
    # The sleeps are the figure here, not the scheduling, so nothing is per client
    library: list[float] = []
    direct: list[float] = []
    above = collect(partial(connect_sample, [Api], library, direct, critical_path), repeat)
    figure = partial(Measurement, shape=APPLICATION, size=APPLICATION_SIZE, per_client=False)
    yield figure("connect() + disconnect(), wall time", samples=library[1:])
    yield figure("connect() + disconnect(), ideal: the critical path", samples=direct[1:])
    yield figure("connect() + disconnect(), above the critical path", samples=above)
