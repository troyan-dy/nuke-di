"""
nuke-di against other dependency injection libraries, on the trees of benchmarks/run.py.

    uv run --group compare python benchmarks/compare.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH]

Every library gets the same classes, whose `__init__` takes the dependencies by type hint, and does the
same work: `cold` builds a container, registers the classes and gets the root of the tree, which
constructs every client, on classes made for every sample, as the cold row of benchmarks/run.py; `warm`
gets the root again from that container, the singleton; `request` is one FastAPI request to a handler
that takes a client through the library's integration; `connect` starts and stops clients whose
`connect()` and `disconnect()` take the time a real connection does, through the async lifecycle of each
library. The figures of the baseline are in docs/benchmarks.md.

The libraries are the `compare` dependency group: `uv sync --group compare`.
"""

import argparse
import asyncio
import inspect
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, fields
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import Any, get_type_hints

import wireup
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from dishka import FromDishka, Provider, Scope, make_async_container, make_container
from dishka.integrations.fastapi import DishkaRoute, setup_dishka
from fastapi import Depends, FastAPI
from injector import Binder, Injector, singleton
from injector import inject as injector_inject
from run import (
    APPLICATION,
    REPEAT,
    SHAPES,
    SIZES,
    Api,
    Result,
    Sleeper,
    Tree,
    client,
    collect,
    environment,
    fmt,
    measure,
    request_samples,
    shape_label,
    summary,
    table,
    timed,
    to_json,
)
from wireup import Injected
from wireup.integration.fastapi import setup as wireup_setup

from nuke_di import Client, Dependencies
from nuke_di.fastapi import setup as nuke_di_setup

# Registering and resolving: `cold(tree)` builds a container and gets the root; `warm(tree)` builds one,
# gets the root and returns a function that gets it again

COLD_START = "cold: container, registration, root"

# Slow connections: `start(root)` connects every client of the tree through the library's async lifecycle and
# returns the shutdown
Stop = Callable[[], Awaitable[None]]
Start = Callable[[type[Client]], Awaitable[Stop]]


@dataclass(frozen=True)
class Library:
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


def nuke_di_cold(tree: Tree) -> object:
    return Dependencies().resolve(tree.root)


def nuke_di_warm(tree: Tree) -> Callable[[], object]:
    deps = Dependencies()
    deps.resolve(tree.root)
    return partial(deps.resolve, tree.root)


def dishka_container(tree: Tree) -> Any:
    provider = Provider(scope=Scope.APP)
    provider.provide_all(*tree.clients)
    return make_container(provider)


def dishka_cold(tree: Tree) -> object:
    return dishka_container(tree).get(tree.root)


def dishka_warm(tree: Tree) -> Callable[[], object]:
    container = dishka_container(tree)
    container.get(tree.root)
    return partial(container.get, tree.root)


def wireup_prepare(tree: Tree) -> None:
    for cls in tree.clients:
        wireup.injectable(cls)


def wireup_container(tree: Tree) -> Any:
    return wireup.create_sync_container(injectables=list(tree.clients))


def wireup_cold(tree: Tree) -> object:
    return wireup_container(tree).get(tree.root)


def wireup_warm(tree: Tree) -> Callable[[], object]:
    container = wireup_container(tree)
    container.get(tree.root)
    return partial(container.get, tree.root)


def dependency_injector_root(tree: Tree) -> Any:
    """
    A `Singleton` provider per class on a `DynamicContainer`, wired by the names of the `__init__` arguments.
    """
    container = containers.DynamicContainer()
    made: dict[str, Any] = {}
    for cls in tree.clients:
        # The library reads no annotation: the user names the provider of every argument. The type of a field
        # is the class, or its name when the annotations are strings, and the providers are keyed by name
        dependencies = {field.name: made[name(field.type)] for field in fields(cls)}  # type: ignore[arg-type]
        provider = providers.Singleton(cls, **dependencies)
        setattr(container, cls.__name__, provider)
        made[cls.__name__] = provider
    return made[tree.root.__name__]


def name(hint: Any) -> str:
    return hint if isinstance(hint, str) else str(hint.__name__)


def dependency_injector_cold(tree: Tree) -> object:
    return dependency_injector_root(tree)()


def dependency_injector_warm(tree: Tree) -> Callable[[], object]:
    root = dependency_injector_root(tree)
    root()
    return root  # type: ignore[no-any-return]


def injector_prepare(tree: Tree) -> None:
    for cls in tree.clients:
        cls.__init__ = injector_inject(cls.__init__)  # type: ignore[method-assign]


def injector_container(tree: Tree) -> Injector:
    def configure(binder: Binder) -> None:
        for cls in tree.clients:
            binder.bind(cls, to=cls, scope=singleton)

    return Injector([configure])


def injector_cold(tree: Tree) -> object:
    return injector_container(tree).get(tree.root)


def injector_warm(tree: Tree) -> Callable[[], object]:
    container = injector_container(tree)
    container.get(tree.root)
    return partial(container.get, tree.root)


# Slow connections: clients whose connect() and disconnect() sleep, started and stopped through the async
# lifecycle of every library, so the figure is how the library schedules them, not what it costs. The
# application tree of benchmarks/run.py needs 68 ms to start and 16 ms to stop along its longest chain


STARTUP = "startup: connect() of every client"
SHUTDOWN = "shutdown: disconnect() of every client"
STARTUP_GATHERED = "startup, the root's arguments gathered by hand"
PRODUCT_PAGE = "product page: 21 clients, connect() of 10–300 ms"  # noqa: RUF001

# The backend of an online shop's product page, a tree as wide as a real service's: the HTTP API needs four
# features, and every feature its own four connections, gRPC clients of other services, Kafka, Elasticsearch,
# Redis. Every client: (connect() seconds, disconnect() seconds, the clients its `__init__` takes). Connected
# one after another, the startup is the sum, 3.37 s; along the longest chain, PaymentsClient → Checkout →
# ProductPageApi or EventsProducer → Personalization → ProductPageApi, it is 0.33 s. To stop, the longest chain is
# ProductPageApi → Checkout → PaymentsClient, 0.36 s; layer by layer, every layer waiting for its slowest client,
# it is 0.65 s, since Checkout and EventsProducer stop slowly in different layers
PRODUCT_PAGE_TREE: dict[str, tuple[float, float, tuple[str, ...]]] = {
    "CatalogClient": (0.250, 0.010, ()),
    "PricesClient": (0.200, 0.010, ()),
    "StockClient": (0.200, 0.010, ()),
    "ReviewsClient": (0.150, 0.010, ()),
    "ProductCard": (0.050, 0.005, ("CatalogClient", "PricesClient", "StockClient", "ReviewsClient")),
    "PaymentsClient": (0.300, 0.010, ()),
    "DeliveryClient": (0.200, 0.010, ()),
    "LoyaltyClient": (0.150, 0.010, ()),
    "PromotionsClient": (0.200, 0.010, ()),
    # Finishes the orders in flight before it stops
    "Checkout": (0.020, 0.300, ("PaymentsClient", "DeliveryClient", "LoyaltyClient", "PromotionsClient")),
    "UsersClient": (0.200, 0.010, ()),
    "RecommendationsClient": (0.250, 0.010, ()),
    "FavoritesClient": (0.150, 0.010, ()),
    # Flushes the messages it has buffered
    "EventsProducer": (0.300, 0.300, ()),
    "Personalization": (
        0.020,
        0.005,
        ("UsersClient", "RecommendationsClient", "FavoritesClient", "EventsProducer"),
    ),
    "Elasticsearch": (0.250, 0.010, ()),
    "Redis": (0.100, 0.010, ()),
    "SynonymsClient": (0.150, 0.010, ()),
    "CategoriesClient": (0.200, 0.010, ()),
    "Search": (0.020, 0.005, ("Elasticsearch", "Redis", "SynonymsClient", "CategoriesClient")),
    "ProductPageApi": (0.010, 0.050, ("ProductCard", "Checkout", "Personalization", "Search")),
}


def product_page() -> type[Client]:
    """
    The classes of `PRODUCT_PAGE_TREE`, each a client whose `__init__` takes its dependencies by type hint;
    the root, ProductPageApi.
    """
    made: dict[str, type[Client]] = {}
    for name, (connect_seconds, disconnect_seconds, dependencies) in PRODUCT_PAGE_TREE.items():
        cls = client(name, [made[dependency] for dependency in dependencies], base=Sleeper)
        cls.connect_seconds = connect_seconds  # type: ignore[attr-defined]
        cls.disconnect_seconds = disconnect_seconds  # type: ignore[attr-defined]
        made[name] = cls
    return made["ProductPageApi"]


def init_arguments(cls: type[Client]) -> dict[str, type[Client]]:
    """
    The arguments of `cls.__init__` and the client each one takes.
    """
    return {name: hint for name, hint in get_type_hints(cls.__init__).items() if name != "return"}


def tree_of(root: type[Client]) -> list[type[Client]]:
    """
    Every client under `root`, dependencies before their consumers.
    """
    found: dict[type[Client], None] = {}

    def visit(cls: type[Client]) -> None:
        if cls not in found:
            for dependency in init_arguments(cls).values():
                visit(dependency)
            found[cls] = None

    visit(root)
    return list(found)


def lifecycle(cls: type[Client]) -> Callable[..., AsyncIterator[Client]]:
    """
    The async generator that dishka, wireup and dependency-injector take for a resource with a lifecycle: it
    builds the client, awaits `connect()`, yields it and awaits `disconnect()` at close. A user writes one per
    client; here its signature is the one of `cls.__init__`, so the libraries that read it find the same
    dependencies by type hint.
    """

    async def factory(**kwargs: Any) -> AsyncIterator[Client]:
        instance = cls(**kwargs)
        await instance.connect()
        yield instance
        await instance.disconnect()

    hints = init_arguments(cls)
    factory.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=hint) for name, hint in hints.items()],
        return_annotation=AsyncIterator[cls],  # type: ignore[valid-type]
    )
    factory.__annotations__ = {**hints, "return": AsyncIterator[cls]}  # type: ignore[valid-type]
    factory.__name__ = factory.__qualname__ = f"make_{cls.__name__}"
    return factory


# One startup and one shutdown: `start(root)` connects every client of the tree and returns the shutdown


async def get_root(container: Any, root: type[Client], gather: bool) -> None:
    """
    Get the root at startup, so that the lazy containers of dishka and wireup connect then and not in the first
    request; with `gather`, its arguments first with `asyncio.gather()`, as an application can write by hand to
    get its branches concurrently.
    """
    if gather:
        await asyncio.gather(*(container.get(cls) for cls in init_arguments(root).values()))
    await container.get(root)


async def nuke_di_start(root: type[Client]) -> Stop:
    deps = Dependencies()
    deps.resolve(root)
    await deps.connect()
    return deps.disconnect


async def dishka_start(root: type[Client], gather: bool = False) -> Stop:
    provider = Provider(scope=Scope.APP)
    for cls in tree_of(root):
        provider.provide(lifecycle(cls))
    # The container connects nothing until a client is asked for
    container = make_async_container(provider)
    await get_root(container, root, gather)
    return container.close


async def wireup_start(root: type[Client], gather: bool = False) -> Stop:
    # Lazy as dishka
    container = wireup.create_async_container(injectables=[wireup.injectable(lifecycle(cls)) for cls in tree_of(root)])
    await get_root(container, root, gather)
    return container.close


async def dependency_injector_start(root: type[Client]) -> Stop:
    container = containers.DynamicContainer()
    made: dict[type[Client], Any] = {}
    for cls in tree_of(root):
        # A `Resource` per client, its arguments named by hand as every provider of the library
        resource = providers.Resource(
            lifecycle(cls), **{name: made[hint] for name, hint in init_arguments(cls).items()}
        )
        setattr(container, cls.__name__, resource)
        made[cls] = resource
    await container.init_resources()  # type: ignore[misc]

    async def stop() -> None:
        await container.shutdown_resources()  # type: ignore[misc]

    return stop


def allow_recursion(n: int) -> None:
    # dishka, wireup and injector recurse once per level of the tree, and dependency-injector does on 3.11, which the
    # default limit stops before a chain of 1000; nuke-di does not recurse, the limit is raised for the others
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * n + 1000))


LIBRARIES = [
    Library("nuke-di", "nuke-di", nuke_di_cold, nuke_di_warm, start=nuke_di_start),
    Library(
        "dishka",
        "dishka",
        dishka_cold,
        dishka_warm,
        start=dishka_start,
        start_gathered=partial(dishka_start, gather=True),
    ),
    Library(
        "wireup",
        "wireup",
        wireup_cold,
        wireup_warm,
        wireup_prepare,
        start=wireup_start,
        start_gathered=partial(wireup_start, gather=True),
    ),
    Library(
        "dependency-injector",
        "dependency-injector",
        dependency_injector_cold,
        dependency_injector_warm,
        start=dependency_injector_start,
    ),
    Library("injector", "injector", injector_cold, injector_warm, injector_prepare),
]


def attempt(result: Result, sample: Callable[[], float], repeat: int) -> Result:
    """
    `result` with its samples, or with the error the library raised on this tree.
    """
    try:
        result.samples = collect(sample, repeat)
    except RecursionError:
        result.error = "RecursionError"
    return result


def trees(sizes: list[int]) -> Iterator[tuple[str, int, Tree]]:
    """
    One tree per shape and size, decorated for every library, for the scenarios that reuse its classes.
    """
    for shape, build in SHAPES.items():
        for n in sizes:
            tree = build(n)
            for library in LIBRARIES:
                library.prepare(tree)
            yield shape, n, tree


def cold_sample(library: Library, build: Callable[[], Tree]) -> float:
    """
    One cold start of `library` on classes made for this sample: the same tree every sample would be served
    by what a library keeps per class after the first one (nuke-di's `__init__` cache, #29), which is the
    second container of a process and not a startup. Making and decorating the classes stays outside the timing.
    """
    tree = build()
    library.prepare(tree)
    try:
        return measure(library.cold, 1, tree)
    finally:
        tree.discard()


def cold_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    # The string-annotation trees as well: most code bases have `from __future__ import annotations`
    for strings in (False, True):
        for shape, build in SHAPES.items():
            for n in sizes:
                for library in LIBRARIES:
                    sample = partial(cold_sample, library, partial(build, n, strings))
                    result = Result(COLD_START, shape_label(shape, strings), n, [], library=library.name)
                    yield attempt(result, sample, repeat)


def warm_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    for shape, n, tree in trees(sizes):
        for library in LIBRARIES:
            result = Result("warm: the root again", shape, n, [], per_client=False, library=library.name)
            try:
                get = library.warm(tree)
            except RecursionError:
                result.error = "RecursionError"
                yield result
                continue
            yield attempt(result, partial(measure, get, 1000), repeat)


# FastAPI: one handler per library, each taking one client through the library's integration


class Database(Client):
    pass


nuke_di_app = FastAPI()
nuke_di_setup(nuke_di_app)


@nuke_di_app.get("/")
async def nuke_di_handler(db: Database) -> dict[str, bool]:
    return {"ok": True}


dishka_app = FastAPI()
dishka_app.router.route_class = DishkaRoute


@dishka_app.get("/")
async def dishka_handler(db: FromDishka[Database]) -> dict[str, bool]:
    return {"ok": True}


dishka_provider = Provider(scope=Scope.APP)
dishka_provider.provide(Database)
setup_dishka(make_async_container(dishka_provider), dishka_app)

wireup_app = FastAPI()


@wireup_app.get("/")
async def wireup_handler(db: Injected[Database]) -> dict[str, bool]:
    return {"ok": True}


wireup_setup(wireup.create_async_container(injectables=[wireup.injectable(Database)]), wireup_app)


class DependencyInjectorContainer(containers.DeclarativeContainer):
    db = providers.Singleton(Database)


dependency_injector_app = FastAPI()


@dependency_injector_app.get("/")
@inject
async def dependency_injector_handler(
    db: Database = Depends(Provide[DependencyInjectorContainer.db]),  # noqa: B008
) -> dict[str, bool]:
    return {"ok": True}


DependencyInjectorContainer().wire(modules=[sys.modules[__name__]])

APPS = [
    ("nuke-di", nuke_di_app),
    ("dishka", dishka_app),
    ("wireup", wireup_app),
    ("dependency-injector", dependency_injector_app),
]


def request_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    for name, app in APPS:
        samples = asyncio.run(request_samples(app, "/", repeat))
        yield Result("one request, a client in the handler", "FastAPI", None, samples, library=name)


async def start_and_stop(start: Start, root: type[Client]) -> tuple[float, float]:
    """
    The wall time of the startup and of the shutdown.
    """
    stop: Stop | None = None

    async def up() -> None:
        nonlocal stop
        stop = await start(root)

    startup = await timed(up)
    assert stop is not None
    return startup, await timed(stop)


def samples_of(start: Start, root: type[Client], repeat: int) -> list[tuple[float, float]]:
    # One untimed warm-up, as `collect()` does
    return [asyncio.run(start_and_stop(start, root)) for _ in range(repeat + 1)][1:]


def connect_scenario(sizes: list[int], repeat: int) -> Iterator[Result]:
    # The sleeps are the figure, so the trees are fixed and nothing is per client
    for shape, root in ((APPLICATION, Api), (PRODUCT_PAGE, product_page())):
        for library in LIBRARIES:
            figure = partial(Result, shape=shape, n=None, per_client=False, library=library.name)
            if library.start is None:
                # injector builds objects synchronously and has nothing that awaits a connect()
                yield figure(STARTUP, samples=[], error="no async lifecycle")
                yield figure(SHUTDOWN, samples=[], error="no async lifecycle")
                continue
            samples = samples_of(library.start, root, repeat)
            yield figure(STARTUP, samples=[up for up, _ in samples])
            yield figure(SHUTDOWN, samples=[down for _, down in samples])
            if library.start_gathered is not None:
                gathered = samples_of(library.start_gathered, root, repeat)
                yield figure(STARTUP_GATHERED, samples=[up for up, _ in gathered])


SCENARIOS: dict[str, Callable[[list[int], int], Iterator[Result]]] = {
    "cold": cold_scenario,
    "warm": warm_scenario,
    "request": request_scenario,
    "connect": connect_scenario,
}


# The summary: three figures that answer "which one is faster", one column per library


@dataclass(frozen=True)
class Figure:
    label: str
    scenario: str
    shape: str
    # None: not a tree figure; "N": the summary size
    n: int | str | None
    # Whether benchmarks/chart.py draws it: the chart keeps to the three figures that answer "which one is faster"
    chart: bool = True


FIGURES = [
    Figure("Cold start: a container and a tree of N clients", COLD_START, "mixed", "N"),
    Figure("Cold start: the same N clients with string annotations", COLD_START, "mixed, strings", "N", chart=False),
    Figure("A cached root", "warm: the root again", "mixed", "N"),
    Figure("A FastAPI request with a client", "one request, a client in the handler", "FastAPI", None),
    Figure("Startup: 8 clients, connect() of 1–60 ms", STARTUP, APPLICATION, None, chart=False),  # noqa: RUF001
    Figure("Shutdown: the same 8 clients", SHUTDOWN, APPLICATION, None, chart=False),
    Figure("Startup: the product page, 21 clients", STARTUP, PRODUCT_PAGE, None, chart=False),
    Figure("Shutdown: the product page", SHUTDOWN, PRODUCT_PAGE, None, chart=False),
]


def summary_size(sizes: list[int]) -> int:
    # The size of an ordinary application if measured, the largest otherwise
    return 100 if 100 in sizes else max(sizes)


def summary_figures(
    results: list[Result], sizes: list[int], wanted_figures: Sequence[Figure] = FIGURES
) -> list[tuple[str, str, dict[str, float | None]]]:
    """
    For every figure: its label, its unit and the median per library, `None` where the library failed.
    """
    n = summary_size(sizes)
    figures = []
    for figure in wanted_figures:
        wanted = n if figure.n == "N" else figure.n
        label = figure.label.replace(" N ", f" {n} ")
        medians: dict[str, float | None] = {}
        unit = "s"
        for result in results:
            if result.scenario == figure.scenario and result.shape == figure.shape and result.n == wanted:
                assert result.library is not None
                medians[result.library] = None if result.error is not None else result.median
                unit = result.unit
        if medians:
            figures.append((label, unit, medians))
    return figures


# Figures this close to the best are as good as the best
TIE = 1.01


def pivot(results: list[Result], sizes: list[int]) -> str:
    """
    One row per figure, one column per library: the best in bold, and every figure within 1% of it, a difference
    inside the noise of a run; the others with their ratio to the best.
    """
    libraries = [library.name for library in LIBRARIES]
    rows = [["Lower is better", *libraries]]
    for label, unit, medians in summary_figures(results, sizes):
        measured = [median for median in medians.values() if median is not None]
        best = min(measured) if measured else None
        cells = []
        for library in libraries:
            median = medians.get(library)
            if median is None:
                cells.append("—")
            elif best is not None and median <= best * TIE:
                cells.append(f"**{fmt(median, unit)}**")
            else:
                assert best is not None
                cells.append(f"{fmt(median, unit)} ({median / best:.1f}×)")  # noqa: RUF001
        rows.append([label, *cells])
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = [
        "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + " |" for row in rows
    ]
    rule = (
        "|"
        + "|".join("-" * (width + 2) if column == 0 else "-" * (width + 1) + ":" for column, width in enumerate(widths))
        + "|"
    )
    return "\n".join([lines[0], rule, *lines[1:]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nuke-di against other DI libraries; see docs/benchmarks.md.")
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
    parser.add_argument(
        "--summary", action="store_true", help="print only the summary: the best per figure and the ratios"
    )
    args = parser.parse_args(argv)

    sizes: list[int] = sorted(set(args.size or SIZES))
    if args.repeat < 1 or any(n < 2 for n in sizes):
        parser.error("--repeat must be at least 1 and every --size at least 2")
    allow_recursion(max(sizes))

    env = environment(sizes, args.repeat)
    env["libraries"] = {library.name: version(library.package) for library in LIBRARIES}
    print(summary(env))
    print(" · ".join(f"{name} {found}" for name, found in env["libraries"].items()), end="\n\n")
    results: list[Result] = []
    for name in args.only or SCENARIOS:
        print(f"{name}...", file=sys.stderr)
        results += SCENARIOS[name](sizes, args.repeat)
    if not args.summary:
        print(table(results), end="\n\n")
    print(pivot(results, sizes))

    if args.json is not None:
        args.json.write_text(to_json(env, results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
