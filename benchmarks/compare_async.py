"""
The scenarios of benchmarks/compare.py that run an event loop: the FastAPI `request`, and `connect`, the startup
and the shutdown of slow connections.

Slow connections: clients whose connect() and disconnect() sleep, started and stopped through the async lifecycle
of every library, so the figure is how the library schedules them, not what it costs. The application tree of
benchmarks/run.py needs 68 ms to start and 16 ms to stop along its longest chain.
"""

import asyncio
from collections.abc import Iterator
from functools import partial
from typing import Final

from compare_trees import PRODUCT_PAGE, product_page
from libraries.apps import APPS
from libraries.compared import LIBRARIES
from libraries.library import Library, Start, Stop
from run import request_samples
from suite.application import APPLICATION, Api
from suite.measure import Measurement, collect, timed

from nuke_di import Client

REQUEST: Final = "one request, a client in the handler"
STARTUP: Final = "startup: connect() of every client"
SHUTDOWN: Final = "shutdown: disconnect() of every client"
STARTUP_GATHERED: Final = "startup, the root's arguments gathered by hand"
_NO_LIFECYCLE: Final = "no async lifecycle"


def request_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    for name, app in APPS:
        samples = asyncio.run(request_samples(app, "/", repeat))
        yield Measurement(REQUEST, "FastAPI", None, samples, library=name)


async def start_and_stop(start: Start, root: type[Client]) -> tuple[float, float]:
    """
    The wall time of the startup and of the shutdown.
    """
    stops: list[Stop] = []
    startup = await timed(_start, start, root, stops)
    shutdown = await timed(stops[0])
    return startup, shutdown


async def _start(start: Start, root: type[Client], stops: list[Stop]) -> None:
    stops.append(await start(root))


def samples_of(
    start: Start,
    root: type[Client],
    repeat: int,
) -> list[tuple[float, float]]:
    return collect(partial(_run, start, root), repeat)


def _run(start: Start, root: type[Client]) -> tuple[float, float]:
    return asyncio.run(start_and_stop(start, root))


def connect_scenario(sizes: list[int], repeat: int) -> Iterator[Measurement]:
    # The sleeps are the figure, so the trees are fixed and nothing is per client
    for shape, root in ((APPLICATION, Api), (PRODUCT_PAGE, product_page())):
        for library in LIBRARIES:
            yield from _library_figures(library, shape, root, repeat)


def _library_figures(library: Library, shape: str, root: type[Client], repeat: int) -> Iterator[Measurement]:
    figure = partial(Measurement, shape=shape, size=None, per_client=False, library=library.name)
    if library.start is None:
        # injector builds objects synchronously and has nothing that awaits a connect()
        yield figure(STARTUP, samples=[], error=_NO_LIFECYCLE)
        yield figure(SHUTDOWN, samples=[], error=_NO_LIFECYCLE)
        return
    samples = samples_of(library.start, root, repeat)
    yield figure(STARTUP, samples=[up for up, _ in samples])
    yield figure(SHUTDOWN, samples=[down for _, down in samples])
    if library.start_gathered is not None:
        gathered = samples_of(library.start_gathered, root, repeat)
        yield figure(STARTUP_GATHERED, samples=[up for up, _ in gathered])
