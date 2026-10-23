"""
The scenarios of benchmarks/compare.py that register and resolve: `cold` and `warm`.
"""

import itertools
from collections.abc import Callable, Iterator
from functools import partial
from typing import Final

from compare_trees import trees
from libraries.compared import LIBRARIES
from libraries.library import Library
from suite.measure import Measurement, collect, measure
from suite.shapes import SHAPES
from suite.trees import Tree, built, shape_label

COLD_START: Final = "cold: container, registration, root"
WARM: Final = "warm: the root again"
_RECURSION_ERROR: Final = "RecursionError"
# Real type hints, then the strings that `from __future__ import annotations` makes of them
_HINTS: Final = (False, True)


def attempt(measurement: Measurement, sample: Callable[[], float], repeat: int) -> Measurement:
    """
    `measurement` with its samples, or with the error the library raised on this tree.
    """
    try:
        measurement.samples = collect(sample, repeat)
    except RecursionError:
        measurement.error = _RECURSION_ERROR
    return measurement


def cold_sample(library: Library, build: Callable[[], Tree]) -> float:
    """
    One cold start of `library` on classes made for this sample: the same tree every sample would be served
    by what a library keeps per class after the first one (nuke-di's `__init__` cache, #29), which is the
    second container of a process and not a startup. Making and decorating the classes stays outside the timing.
    """
    with built(build) as tree:
        library.prepare(tree)
        return measure(library.cold, 1, tree)


def cold_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    # The string-annotation trees as well: most code bases have `from __future__ import annotations`
    for strings in _HINTS:
        for shape, size, library in itertools.product(SHAPES, sizes, LIBRARIES):
            yield _cold(library, shape, strings, size, repeat)


def _cold(library: Library, shape: str, strings: bool, size: int, repeat: int) -> Measurement:
    build = partial(SHAPES[shape], size, strings)
    label = shape_label(shape, strings)
    measurement = Measurement(COLD_START, label, size, [], library=library.name)
    return attempt(measurement, partial(cold_sample, library, build), repeat)


def warm_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    for shape, size, tree in trees(sizes):
        for library in LIBRARIES:
            yield _warm(library, shape, size, tree, repeat)


def _warm(library: Library, shape: str, size: int, tree: Tree, repeat: int) -> Measurement:
    measurement = Measurement(WARM, shape, size, [], per_client=False, library=library.name)
    try:
        get = library.warm(tree)
    except RecursionError:
        measurement.error = _RECURSION_ERROR
        return measurement
    return attempt(measurement, partial(measure, get, 1000), repeat)
