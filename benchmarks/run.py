"""
Benchmarks of nuke-di: what the library itself costs, measured on no-op clients.

    uv run python benchmarks/run.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH]

Prints a Markdown table (median and p95 of K repeats, and the figure per client where it makes sense);
`--json` writes the same numbers with the Python version, platform and commit. The numbers of every
supported Python version are recorded in docs/benchmarks.md.

Only the standard library and nuke-di; the `fastapi` scenario needs fastapi and httpx and is skipped
without them. Garbage collection is paused while a sample runs, as `timeit` does.
"""

import argparse
import asyncio
import gc
import importlib.util
import itertools
import json
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator, Sequence
from dataclasses import dataclass, make_dataclass
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

from nuke_di import Client, Dependencies, NotSingletonClient

ROOT = Path(__file__).resolve().parent.parent
SIZES = (10, 100, 1000)
REPEAT = 20

Scenario = Callable[[list[int], int], Iterator["Result"]]


@dataclass
class Result:
    """
    The samples of one measurement: seconds per operation, or bytes for a memory figure.
    """

    scenario: str
    shape: str
    n: int | None
    samples: list[float]
    unit: str = "s"
    # Whether `median / n` means something: not for a cache hit, which does not depend on the tree
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
        return statistics.quantiles(self.samples, n=20, method="inclusive")[18]

    @property
    def per_client_value(self) -> float | None:
        if self.n is None or not self.per_client or self.error is not None:
            return None
        return self.median / self.n


# Measuring


def measure(fn: Callable[..., object], loops: int = 1, *args: object) -> float:
    """
    Seconds per call of `fn(*args)` over `loops` calls, garbage collection paused.
    """
    gc.disable()
    try:
        started = time.perf_counter()
        for _ in range(loops):
            fn(*args)
        return (time.perf_counter() - started) / loops
    finally:
        gc.enable()


def collect(sample: Callable[[], float], repeat: int) -> list[float]:
    """
    `repeat` samples after one warm-up, which pays the one-off costs such as lazy imports.
    """
    sample()
    return [sample() for _ in range(repeat)]


# Trees of no-op clients


@dataclass
class Tree:
    root: type[Client]
    # Every client of the tree, dependencies before their consumers; the first one is a leaf
    clients: list[type[Client]]
    # The module of the classes when their type hints are strings, see client(); None for real type hints
    module: ModuleType | None = None

    def discard(self) -> None:
        """
        Unregister the module, so the trees built per sample do not pile up in `sys.modules`; the generated
        `__init__` keeps a reference to its globals, which is all `get_type_hints()` needs afterwards.
        """
        if self.module is not None:
            del sys.modules[self.module.__name__]


def client(
    name: str, deps: Sequence[type[NotSingletonClient]] = (), base: type = Client, module: ModuleType | None = None
) -> type[Client]:
    """
    A client class whose `__init__` takes `deps` by type hint, as a user would write it.

    With `module`, the type hints are the names of the dependencies as strings, which is what
    `from __future__ import annotations` makes of every annotation, and the class belongs to `module`:
    `dataclass` builds `__init__` with the globals of the class's module, which is where `get_type_hints()`
    evaluates the strings later, so the module holds every class of the tree under its name.
    """
    if module is None:
        fields = [(f"dep{number}", cls) for number, cls in enumerate(deps)]
        return cast(type[Client], make_dataclass(name, fields, bases=(base,), eq=False, repr=False))

    strings = [(f"dep{number}", cls.__name__) for number, cls in enumerate(deps)]
    if sys.version_info >= (3, 12):
        cls = make_dataclass(name, strings, bases=(base,), eq=False, repr=False, module=module.__name__)
    else:
        # 3.11 has no `module` argument and takes the module from the namespace
        cls = make_dataclass(
            name, strings, bases=(base,), eq=False, repr=False, namespace={"__module__": module.__name__}
        )
    setattr(module, name, cls)
    return cast(type[Client], cls)


_modules = itertools.count()


def new_module(strings: bool) -> ModuleType | None:
    """
    Where the classes of one tree are made: nowhere for real type hints; for string hints a module of their
    own, registered in `sys.modules` as the module of a real code base is, because `dataclass` finds the
    globals of `__init__` there, and wireup imports the module of a class to evaluate its hints.
    """
    if not strings:
        return None
    module = ModuleType(f"benchmarks.strings{next(_modules)}")
    sys.modules[module.__name__] = module
    return module


class Shape(Protocol):
    def __call__(self, n: int, strings: bool = False) -> Tree: ...


def wide(n: int, strings: bool = False) -> Tree:
    """
    One root that declares `n - 1` clients with no dependencies: two layers.
    """
    module = new_module(strings)
    leaves = [client(f"Wide{number}", module=module) for number in range(n - 1)]
    root = client("WideRoot", leaves, module=module)
    return Tree(root, [*leaves, root], module)


def deep(n: int, strings: bool = False) -> Tree:
    """
    A chain of `n` clients: `n` layers.
    """
    module = new_module(strings)
    clients: list[type[Client]] = []
    for number in range(n):
        clients.append(client(f"Deep{number}", clients[-1:], module=module))
    return Tree(clients[-1], clients, module)


def mixed(n: int, strings: bool = False) -> Tree:
    """
    A pyramid of `n` clients, 1, 2, 4, ... wide from the top; every client depends on two or three of the
    level below, so the levels share their dependencies like a diamond. About log2(n) layers.
    """
    widths = []
    total, width = 0, 1
    while total + width <= n:
        widths.append(width)
        total += width
        width *= 2
    if total < n:
        widths.append(n - total)

    module = new_module(strings)
    clients: list[type[Client]] = []
    below: list[type[Client]] = []
    for level, width in reversed(list(enumerate(widths))):
        current = []
        for number in range(width):
            deps = dict.fromkeys(below[(2 * number + offset) % len(below)] for offset in range(3)) if below else {}
            current.append(client(f"Mixed{level}_{number}", list(deps), module=module))
        clients += current
        below = current
    return Tree(below[0], clients, module)


SHAPES: dict[str, Shape] = {"wide": wide, "deep": deep, "mixed": mixed}


def shape_label(shape: str, strings: bool) -> str:
    """
    The Shape column of a tree: `wide, strings` for the string-annotation variant.
    """
    return f"{shape}, strings" if strings else shape


# Scenarios


COLD = "resolve(), cold"
SEEN = "resolve(), second container, classes seen before"


def cold_resolve(build: Callable[[], Tree]) -> float:
    """
    `resolve()` of a root on a fresh container, the classes never resolved before: `build` makes them for
    every sample, outside the timing, so nothing a process keeps per class (#29) can serve them.
    """
    tree = build()
    try:
        return measure(Dependencies().resolve, 1, tree.root)
    finally:
        tree.discard()


def seen_resolve(root: type[Client]) -> float:
    """
    `resolve()` of a root on a fresh container, the classes resolved before in this process: what every
    test of a session pays after the first one.
    """
    return measure(Dependencies().resolve, 1, root)


def warm_resolve(deps: Dependencies, root: type[Client]) -> float:
    return measure(deps.resolve, 1000, root)


def resolve_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    for strings in (False, True):
        for shape, build in SHAPES.items():
            for n in sizes:
                samples = collect(partial(cold_resolve, partial(build, n, strings)), repeat)
                yield Result(COLD, shape_label(shape, strings), n, samples)

                # The warm-up of collect() is the first container, the samples are the second one and on
                tree = build(n, strings)
                yield Result(SEEN, shape_label(shape, strings), n, collect(partial(seen_resolve, tree.root), repeat))

                if not strings:
                    # The cache hit does not read the hints, so it has no strings variant
                    deps = Dependencies()
                    deps.resolve(tree.root)
                    samples = collect(partial(warm_resolve, deps, tree.root), repeat)
                    yield Result("resolve(), warm", shape, n, samples, per_client=False)
                tree.discard()


async def timed(run: Callable[[], Awaitable[None]]) -> float:
    """
    Seconds of one `await run()`, garbage collection paused.
    """
    gc.disable()
    try:
        started = time.perf_counter()
        await run()
        return time.perf_counter() - started
    finally:
        gc.enable()


async def cycle(deps: Dependencies) -> float:
    async def run() -> None:
        await deps.connect()
        await deps.disconnect()

    return await timed(run)


async def ideal(clients: list[NotSingletonClient]) -> float:
    """
    What the same clients cost without the container: every coroutine awaited directly, in order.
    """

    async def run() -> None:
        for instance in clients:
            await instance.connect()
        for instance in reversed(clients):
            await instance.disconnect()

    return await timed(run)


Reference = Callable[[list[Any]], Coroutine[Any, Any, float]]


def connect_sample(
    roots: list[type[Client]], library: list[float], direct: list[float], reference: Reference = ideal
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
    library.append(asyncio.run(cycle(deps)))
    direct.append(asyncio.run(reference(clients)))
    return library[-1] - direct[-1]


def one_layer(n: int) -> list[type[Client]]:
    return [client(f"Layer{number}") for number in range(n)]


def chain(n: int) -> list[type[Client]]:
    return [deep(n).root]


# An application: clients whose connect() and disconnect() take the time a real connection does, in a tree
# with slack between its branches, so the wall time is what a startup and a shutdown take and the layers
# of the container, which wait for the slowest client of each, show against the critical path (#28)


class Sleeper(Client):
    """
    A client whose connect() and disconnect() sleep for a fixed time.
    """

    connect_seconds = 0.0
    disconnect_seconds = 0.0
    # The clients this one depends on, for the schedule without the container
    dependencies: tuple["Sleeper", ...] = ()

    async def connect(self) -> None:
        await asyncio.sleep(self.connect_seconds)

    async def disconnect(self) -> None:
        await asyncio.sleep(self.disconnect_seconds)


class Settings(Sleeper):
    connect_seconds = 0.001


class Postgres(Sleeper):
    connect_seconds = 0.030
    disconnect_seconds = 0.005

    def __init__(self, settings: Settings) -> None:
        self.dependencies = (settings,)


class Redis(Sleeper):
    connect_seconds = 0.020
    disconnect_seconds = 0.002

    def __init__(self, settings: Settings) -> None:
        self.dependencies = (settings,)


class Kafka(Sleeper):
    connect_seconds = 0.060
    disconnect_seconds = 0.010

    def __init__(self, settings: Settings) -> None:
        self.dependencies = (settings,)


class Repository(Sleeper):
    connect_seconds = 0.015
    disconnect_seconds = 0.001

    def __init__(self, postgres: Postgres) -> None:
        self.dependencies = (postgres,)


class Users(Sleeper):
    connect_seconds = 0.010
    disconnect_seconds = 0.001

    def __init__(self, repository: Repository, redis: Redis) -> None:
        self.dependencies = (repository, redis)


class Consumer(Sleeper):
    connect_seconds = 0.005
    disconnect_seconds = 0.005

    def __init__(self, kafka: Kafka) -> None:
        self.dependencies = (kafka,)


class Api(Sleeper):
    connect_seconds = 0.002
    disconnect_seconds = 0.001

    def __init__(self, users: Users, consumer: Consumer) -> None:
        self.dependencies = (users, consumer)


APPLICATION = "application: 8 clients, connect() of 1–60 ms"  # noqa: RUF001


async def after(
    tasks: dict[int, asyncio.Task[None]], others: Iterable[Sleeper], step: Callable[[], Awaitable[None]]
) -> None:
    """
    `step()` once the tasks of `others` are done.
    """
    for other in others:
        await tasks[id(other)]
    await step()


async def critical_path(clients: list[Sleeper]) -> float:
    """
    The same coroutines without the layers: every connect() starts as soon as the client's dependencies are
    connected and every disconnect() as soon as its consumers are disconnected, so the wall time is the
    longest chain of the tree, what a schedule by dependency rather than by layer would give.
    """
    consumers: dict[int, list[Sleeper]] = {id(client): [] for client in clients}
    for client in clients:
        for dependency in client.dependencies:
            consumers[id(dependency)].append(client)

    async def run() -> None:
        # A task body runs only when the loop gets control back, after every task is created, so each one
        # finds the tasks it waits for whatever the order
        connects: dict[int, asyncio.Task[None]] = {}
        for client in clients:
            connects[id(client)] = asyncio.create_task(after(connects, client.dependencies, client.connect))
        await asyncio.gather(*connects.values())
        disconnects: dict[int, asyncio.Task[None]] = {}
        for client in clients:
            disconnects[id(client)] = asyncio.create_task(after(disconnects, consumers[id(client)], client.disconnect))
        await asyncio.gather(*disconnects.values())

    return await timed(run)


def connect_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    for shape, build in {"wide: N in one layer": one_layer, "deep: N layers": chain}.items():
        for n in sizes:
            library: list[float] = []
            direct: list[float] = []
            overhead = collect(partial(connect_sample, build(n), library, direct), repeat)
            # The warm-up is the first figure of both lists
            yield Result("connect() + disconnect()", shape, n, library[1:])
            yield Result("connect() + disconnect(), ideal: coroutines awaited directly", shape, n, direct[1:])
            yield Result("connect() + disconnect(), overhead above the ideal", shape, n, overhead)

    # The sleeps are the figure here, not the scheduling, so nothing is per client
    library, direct = [], []
    lost = collect(partial(connect_sample, [Api], library, direct, critical_path), repeat)
    yield Result("connect() + disconnect(), wall time", APPLICATION, 8, library[1:], per_client=False)
    ideal_path = "connect() + disconnect(), ideal: the critical path, no layer barriers"
    yield Result(ideal_path, APPLICATION, 8, direct[1:], per_client=False)
    yield Result("connect() + disconnect(), lost at the layer barriers", APPLICATION, 8, lost, per_client=False)


class Database(Client):
    pass


class Cache(Client):
    pass


def handler(db: Database, cache: Cache) -> tuple[Database, Cache]:
    return db, cache


def inject_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    deps = Dependencies()
    injected = deps.inject(handler)
    db, cache = deps.resolve(Database), deps.resolve(Cache)

    shape = "2 clients"
    yield Result(
        "inject(), clients resolved", shape, None, collect(partial(measure, deps.inject, 1000, handler), repeat)
    )
    yield Result("call of the injected function", shape, None, collect(partial(measure, injected, 100_000), repeat))
    yield Result(
        "call of the plain function", shape, None, collect(partial(measure, handler, 100_000, db, cache), repeat)
    )


def not_singleton_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    bases: dict[str, type] = {
        "N consumers of a Client": Client,
        "N consumers of a NotSingletonClient": NotSingletonClient,
    }
    for shape, base in bases.items():
        for n in sizes:
            yield Result(COLD, shape, n, collect(partial(cold_resolve, partial(consumers, base, n)), repeat))


def consumers(base: type, n: int) -> Tree:
    """
    One root over `n` consumers of one `Session`, a `Client` or a `NotSingletonClient`.
    """
    session = client("Session", base=base)
    middle = [client(f"Consumer{number}", [session]) for number in range(n)]
    root = client("Root", middle)
    return Tree(root, [session, *middle, root])


def mock_cycle(deps: Dependencies, leaf: type[Client], root: type[Client]) -> None:
    deps.flush()
    deps.mock(leaf)
    deps.resolve(root)


def override_cycle(deps: Dependencies, leaf: type[Client], fake: Client, root: type[Client]) -> None:
    with deps.override(leaf, fake):
        deps.resolve(root)


def overrides_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    for n in sizes:
        tree = mixed(n)
        leaf = tree.clients[0]
        shape = "mixed, a leaf replaced"

        samples = collect(partial(measure, mock_cycle, 1, Dependencies(), leaf, tree.root), repeat)
        # The autospec of mock() costs the same at every N
        yield Result("flush() + mock() + resolve()", shape, n, samples, per_client=False)

        # override() needs a container without resolved clients and flushes it on exit
        samples = collect(partial(measure, override_cycle, 1, Dependencies(), leaf, leaf(), tree.root), repeat)
        yield Result("override() block + resolve()", shape, n, samples)


def fastapi_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    try:
        from fastapi import Depends, FastAPI

        from nuke_di.fastapi import setup
    except ImportError as exc:
        print(f"fastapi: skipped, {exc.name} is not installed", file=sys.stderr)
        return
    if importlib.util.find_spec("httpx") is None:
        print("fastapi: skipped, httpx is not installed", file=sys.stderr)
        return

    class PlainDatabase:
        pass

    # async, as the binding of nuke-di: a sync dependency would run in a threadpool
    async def get_db() -> PlainDatabase:
        return PlainDatabase()

    nuke_app = FastAPI()
    setup(nuke_app)
    plain_app = FastAPI()

    @nuke_app.get("/nuke")
    async def with_client(db: Database) -> dict[str, bool]:
        return {"ok": True}

    @plain_app.get("/depends")
    async def with_depends(db: PlainDatabase = Depends(get_db)) -> dict[str, bool]:  # noqa: B008
        return {"ok": True}

    @plain_app.get("/plain")
    async def without_dependencies() -> dict[str, bool]:
        return {"ok": True}

    rows = [
        ("a client through nuke-di", nuke_app, "/nuke"),
        ("a plain FastAPI Depends()", plain_app, "/depends"),
        ("no dependencies", plain_app, "/plain"),
    ]
    for shape, app, path in rows:
        yield Result("one request", shape, None, asyncio.run(request_samples(app, path, repeat)))


async def request_samples(app: Any, path: str, repeat: int, loops: int = 200) -> list[float]:
    """
    Seconds per `GET path` on the FastAPI `app` through the ASGI transport of httpx, `repeat` samples of
    `loops` requests each after a warm-up, with the lifespan of the app running as on a server.
    """
    import httpx

    async def sample(http: httpx.AsyncClient) -> float:
        gc.disable()
        try:
            started = time.perf_counter()
            for _ in range(loops):
                await http.get(path)
            return (time.perf_counter() - started) / loops
        finally:
            gc.enable()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as http:
            response = await http.get(path)
            assert response.status_code == 200, response.text
            return [await sample(http) for _ in range(repeat)]


def import_seconds(module: str) -> float:
    """
    The cumulative import time of `module` in a fresh interpreter, from `-X importtime`.
    """
    # The interpreter of this run, with fixed arguments
    process = subprocess.run(  # noqa: S603
        [sys.executable, "-X", "importtime", "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    for line in process.stderr.splitlines():
        # import time:  self [us] | cumulative | imported package
        columns = [column.strip() for column in line.split("|")]
        if len(columns) == 3 and columns[2] == module:
            return int(columns[1]) / 1_000_000
    raise RuntimeError(f"-X importtime did not report {module}:\n{process.stderr}")


def import_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    modules = ["nuke_di"]
    if importlib.util.find_spec("fastapi") is not None:
        modules += ["nuke_di.fastapi", "fastapi"]
    for module in modules:
        yield Result("import, fresh interpreter", module, None, collect(partial(import_seconds, module), repeat))


def peak_memory(root: type[Client]) -> float:
    tracemalloc.start()
    try:
        Dependencies().resolve(root)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return float(peak)


def memory_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    n = max(sizes)
    tree = mixed(n)
    yield Result("resolve(), tracemalloc peak", "mixed", n, collect(partial(peak_memory, tree.root), repeat), unit="B")


SCENARIOS: dict[str, Scenario] = {
    "resolve": resolve_scenario,
    "connect": connect_scenario,
    "inject": inject_scenario,
    "not_singleton": not_singleton_scenario,
    "overrides": overrides_scenario,
    "fastapi": fastapi_scenario,
    "import": import_scenario,
    "memory": memory_scenario,
}


# Reporting


def significant(value: float) -> str:
    """
    Three significant digits of a value in [1, 1000).
    """
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def fmt(value: float, unit: str) -> str:
    scales = {
        "s": (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)),
        "B": (("MB", 1e6), ("kB", 1e3), ("B", 1.0)),
    }[unit]
    for name, scale in scales:
        if abs(value) >= scale:
            return f"{significant(value / scale)} {name}"
    name, scale = scales[-1]
    return f"{significant(value / scale)} {name}"


def table(results: list[Result]) -> str:
    # The Library column only in a comparison
    libraries = any(result.library is not None for result in results)
    rows = [["Library"] * libraries + ["Scenario", "Shape", "N", "Median", "p95", "Per client"]]
    for result in results:
        per_client = result.per_client_value
        figures = (
            [result.error, "", ""]
            if result.error is not None
            else [
                fmt(result.median, result.unit),
                fmt(result.p95, result.unit),
                "" if per_client is None else fmt(per_client, result.unit),
            ]
        )
        rows.append(
            [
                *([result.library or ""] if libraries else []),
                result.scenario,
                result.shape,
                "" if result.n is None else str(result.n),
                *figures,
            ]
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    # Text columns left-aligned, numbers right-aligned
    numeric = [False] * libraries + [False, False, True, True, True, True]

    def line(row: list[str]) -> str:
        cells = [
            cell.rjust(width) if right else cell.ljust(width)
            for cell, width, right in zip(row, widths, numeric, strict=True)
        ]
        return "| " + " | ".join(cells) + " |"

    rules = [
        "-" * (width + 1) + ":" if right else "-" * (width + 2) for width, right in zip(widths, numeric, strict=True)
    ]
    return "\n".join([line(rows[0]), "|" + "|".join(rules) + "|", *map(line, rows[1:])])


def environment(sizes: list[int], repeat: int) -> dict[str, Any]:
    try:
        nuke_di = version("nuke-di")
    except PackageNotFoundError:
        nuke_di = None
    # git from PATH with fixed arguments, for the record of what was measured
    git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=False)  # noqa: S607
    return {
        "nuke_di": nuke_di,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "commit": git.stdout.strip() or None,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "sizes": sizes,
        "repeat": repeat,
    }


def summary(env: dict[str, Any]) -> str:
    commit = (env["commit"] or "unknown")[:7]
    sizes = ", ".join(map(str, env["sizes"]))
    return (
        f"nuke-di {env['nuke_di']} · {env['implementation']} {env['python']} · {env['platform']} · "
        f"commit {commit} · N = {sizes} · {env['repeat']} repeats"
    )


def to_json(env: dict[str, Any], results: list[Result]) -> str:
    rows = [
        {
            **({"library": result.library} if result.library is not None else {}),
            "scenario": result.scenario,
            "shape": result.shape,
            "n": result.n,
            "unit": result.unit,
            "median": None if result.error is not None else result.median,
            "p95": None if result.error is not None else result.p95,
            "min": None if result.error is not None else min(result.samples),
            "per_client": result.per_client_value,
            "samples": result.samples,
            **({"error": result.error} if result.error is not None else {}),
        }
        for result in results
    ]
    return json.dumps({**env, "results": rows}, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure what nuke-di costs; see docs/benchmarks.md.")
    parser.add_argument(
        "--size", type=int, action="append", metavar="N", help=f"tree size, repeatable; default {SIZES}"
    )
    parser.add_argument("--repeat", type=int, default=REPEAT, metavar="K", help=f"samples per figure, default {REPEAT}")
    parser.add_argument(
        "--only",
        action="append",
        choices=SCENARIOS,
        metavar="SCENARIO",
        help=f"run one scenario, repeatable: {', '.join(SCENARIOS)}",
    )
    parser.add_argument("--json", type=Path, metavar="PATH", help="also write the figures as JSON")
    args = parser.parse_args(argv)

    sizes: list[int] = sorted(set(args.size or SIZES))
    if args.repeat < 1 or any(n < 2 for n in sizes):
        parser.error("--repeat must be at least 1 and every --size at least 2")

    env = environment(sizes, args.repeat)
    print(summary(env), end="\n\n")
    results: list[Result] = []
    for name in args.only or SCENARIOS:
        print(f"{name}...", file=sys.stderr)
        results += SCENARIOS[name](sizes, args.repeat)
    print(table(results))

    if args.json is not None:
        args.json.write_text(to_json(env, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
