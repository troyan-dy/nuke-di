"""
nuke-di against other dependency injection libraries, on the trees of benchmarks/run.py.

    uv run --group compare python benchmarks/compare.py [--size N]... [--repeat K] [--only SCENARIO]... [--json PATH]

Every library gets the same classes, whose `__init__` takes the dependencies by type hint, and does the
same work: `cold` builds a container, registers the classes and gets the root of the tree, which
constructs every client, on classes made for every sample, as the cold row of benchmarks/run.py; `warm`
gets the root again from that container, the singleton; `request` is
one FastAPI request to a handler that takes a client through the library's integration. The figures of
the baseline are in docs/benchmarks.md.

The libraries are the `compare` dependency group: `uv sync --group compare`.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, fields
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import Any

import wireup
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from dishka import FromDishka, Provider, Scope, make_async_container, make_container
from dishka.integrations.fastapi import DishkaRoute, setup_dishka
from fastapi import Depends, FastAPI
from injector import Binder, Injector, singleton
from injector import inject as injector_inject
from run import (
    REPEAT,
    SHAPES,
    SIZES,
    Result,
    Tree,
    collect,
    environment,
    fmt,
    measure,
    request_samples,
    shape_label,
    summary,
    table,
    to_json,
)
from wireup import Injected
from wireup.integration.fastapi import setup as wireup_setup

from nuke_di import Client, Dependencies
from nuke_di.fastapi import setup as nuke_di_setup

# Registering and resolving: `cold(tree)` builds a container and gets the root; `warm(tree)` builds one,
# gets the root and returns a function that gets it again

COLD_START = "cold: container, registration, root"


@dataclass(frozen=True)
class Library:
    name: str
    package: str
    cold: Callable[[Tree], object]
    warm: Callable[[Tree], Callable[[], object]]
    # Decorates the classes of a tree the way the library wants them, once, as a user does at import
    prepare: Callable[[Tree], None] = lambda tree: None


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


def allow_recursion(n: int) -> None:
    # dishka, wireup and injector recurse once per level of the tree, and dependency-injector does on 3.11, which the
    # default limit stops before a chain of 1000; nuke-di does not recurse, the limit is raised for the others
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * n + 1000))


LIBRARIES = [
    Library("nuke-di", "nuke-di", nuke_di_cold, nuke_di_warm),
    Library("dishka", "dishka", dishka_cold, dishka_warm),
    Library("wireup", "wireup", wireup_cold, wireup_warm, wireup_prepare),
    Library("dependency-injector", "dependency-injector", dependency_injector_cold, dependency_injector_warm),
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


SCENARIOS: dict[str, Callable[[list[int], int], Iterator[Result]]] = {
    "cold": cold_scenario,
    "warm": warm_scenario,
    "request": request_scenario,
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


def pivot(results: list[Result], sizes: list[int]) -> str:
    """
    One row per figure, one column per library: the best in bold, the others with their ratio to it.
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
            elif median == best:
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
