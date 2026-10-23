"""
The `resolve` and `not_singleton` scenarios: resolve() of a tree, cold, on a second container and from the cache.
"""

import itertools
from collections.abc import Callable, Iterator
from functools import partial
from typing import Final

from nuke_di import Client, Dependencies, NotSingletonClient
from suite.measure import Measurement, collect, measure
from suite.shapes import SHAPES
from suite.trees import Tree, built, client, shape_label

COLD: Final = "resolve(), cold"
SEEN: Final = "resolve(), second container, classes seen before"
# Real type hints, then the strings that `from __future__ import annotations` makes of them
_HINTS: Final = (False, True)


def cold_resolve(build: Callable[[], Tree]) -> float:
    """
    `resolve()` of a root on a fresh container, the classes never resolved before: `build` makes them for
    every sample, outside the timing, so nothing a process keeps per class (#29) can serve them.
    """
    with built(build) as tree:
        return measure(Dependencies().resolve, 1, tree.root)


def seen_resolve(root: type[Client]) -> float:
    """
    `resolve()` of a root on a fresh container, the classes resolved before in this process: what every
    test of a session pays after the first one.
    """
    return measure(Dependencies().resolve, 1, root)


def warm_resolve(deps: Dependencies, root: type[Client]) -> float:
    return measure(deps.resolve, 1000, root)


def resolve_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    for strings in _HINTS:
        for (shape, build), size in itertools.product(SHAPES.items(), sizes):
            label = shape_label(shape, strings)
            yield from _resolve_figures(label, partial(build, size, strings), size, repeat)


def _resolve_figures(
    label: str,
    build: Callable[[], Tree],
    size: int,
    repeat: int,
) -> Iterator[Measurement]:
    samples = collect(partial(cold_resolve, build), repeat)
    yield Measurement(COLD, label, size, samples)

    # The warm-up of collect() is the first container, the samples are the second one and on
    tree = build()
    seen = collect(partial(seen_resolve, tree.root), repeat)
    yield Measurement(SEEN, label, size, seen)

    if tree.module is None:
        # The cache hit does not read the hints, so it has no strings variant
        deps = Dependencies()
        deps.resolve(tree.root)
        warm = collect(partial(warm_resolve, deps, tree.root), repeat)
        yield Measurement("resolve(), warm", label, size, warm, per_client=False)
    tree.discard()


def not_singleton_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    bases: dict[str, type] = {
        "N consumers of a Client": Client,
        "N consumers of a NotSingletonClient": NotSingletonClient,
    }
    for shape, base in bases.items():
        for size in sizes:
            samples = collect(partial(cold_resolve, partial(consumers, base, size)), repeat)
            yield Measurement(COLD, shape, size, samples)


def consumers(base: type, size: int) -> Tree:
    """
    One root over `size` consumers of one `Session`, a `Client` or a `NotSingletonClient`.
    """
    session = client("Session", base=base)
    middle = [client(f"Consumer{number}", [session]) for number in range(size)]
    root = client("Root", middle)
    return Tree(root, [session, *middle, root])
