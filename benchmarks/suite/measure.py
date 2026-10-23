"""
Measuring: the samples of one figure, and the timers that take them with garbage collection paused, as `timeit`
does.
"""

import gc
import statistics
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Final, TypeVar

# The 95th percentile: the 19th of the 20 cut points that split the samples into twentieths
_PERCENTILES: Final = 20
_P95: Final = 18

SampleT = TypeVar("SampleT")


@dataclass
class Measurement:
    """
    The samples of one measurement: seconds per operation, or bytes for a memory figure.
    """

    scenario: str
    shape: str
    # The number of clients, the "n" of the JSON
    size: int | None
    samples: list[float]
    unit: str = "s"
    # Whether `median / size` means something: not for a cache hit, which does not depend on the tree
    per_client: bool = True
    # The library measured, in a comparison (benchmarks/compare.py)
    library: str | None = None
    # Why there are no samples, e.g. a RecursionError of the library on a deep tree
    error: str | None = None

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    @property
    def p95(self) -> float:
        if len(self.samples) < 2:
            return max(self.samples)
        return statistics.quantiles(self.samples, n=_PERCENTILES, method="inclusive")[_P95]

    @property
    def per_client_value(self) -> float | None:
        if self.size is None or not self.per_client or self.error is not None:
            return None
        return self.median / self.size


@contextmanager
def gc_paused() -> Iterator[None]:
    """
    Garbage collection off for the block, so a collection does not land in a sample.
    """
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def measure(fn: Callable[..., object], loops: int, *args: object) -> float:
    """
    Seconds per call of `fn(*args)` over `loops` calls, garbage collection paused.
    """
    with gc_paused():
        started = time.perf_counter()
        for _ in range(loops):
            fn(*args)
        return (time.perf_counter() - started) / loops


def collect(sample: Callable[[], SampleT], repeat: int) -> list[SampleT]:
    """
    `repeat` samples after one warm-up, which pays the one-off costs such as lazy imports.
    """
    sample()
    return [sample() for _ in range(repeat)]


def measured(
    repeat: int,
    fn: Callable[..., object],
    loops: int,
    *args: object,
) -> list[float]:
    """
    The samples of `measure(fn, loops, *args)`, as `collect()` takes them.
    """
    return collect(partial(measure, fn, loops, *args), repeat)


async def timed(run: Callable[..., Awaitable[object]], *args: object) -> float:
    """
    Seconds of one `await run(*args)`, garbage collection paused.
    """
    with gc_paused():
        started = time.perf_counter()
        await run(*args)
        return time.perf_counter() - started
