"""
The benchmark suite of benchmarks/ keeps running: CI runs it the same way, as a smoke test.
"""

import asyncio
import json
import platform
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from nuke_di import Client, Dependencies
from tests.sequences import single

ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = ROOT / "benchmarks"
RUN = BENCHMARKS / "run.py"
COMPARE = BENCHMARKS / "compare.py"
# One sample of the smallest trees
QUICK = ("--size", "10", "--repeat", "1")
# The clients of the application sleep for 0.1 s in all: its wall time and its critical path are above this
SLOW_CONNECT = 0.05
# The startup of the product page: 0.33 s along the longest chain, 3.37 s one after another
ALONG_THE_CHAIN = 1
ONE_AFTER_ANOTHER = 3
GATHERED = 2
# dependency-injector stops layer by layer, 0.65 s, where the longest chain is 0.36 s
LAYERED_ABOVE = 0.15
PRODUCT_PAGE = "product page: 21 clients, connect() of 10–300 ms"  # noqa: RUF001
COLD = "resolve(), cold"
SEEN = "resolve(), second container, classes seen before"

Get = Callable[[], Awaitable[Any]]
# lifecycle() of benchmarks/libraries/lifecycle.py: the async generator of a client class
Lifecycle = Callable[[type[Client]], Callable[..., Any]]


@dataclass(frozen=True)
class Ran:
    """
    A successful benchmark script run: the lines of its stdout and the JSON it wrote.
    """

    lines: list[str]
    report: dict[str, Any]

    @property
    def rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = self.report["results"]
        return rows

    def startswith(self, prefix: str) -> list[str]:
        return [line for line in self.lines if line.startswith(prefix)]

    def where(self, **wanted: object) -> list[dict[str, Any]]:
        """
        The rows of the JSON with the values of `wanted`.
        """
        return [row for row in self.rows if wanted.items() <= row.items()]

    @property
    def application(self) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["shape"].startswith("application: ")]


def run(*args: str, script: Path = RUN) -> subprocess.CompletedProcess[str]:
    # The interpreter of the tests, with fixed arguments
    command = [sys.executable, str(script), *args]
    return subprocess.run(command, capture_output=True, text=True, cwd=ROOT, check=False)  # noqa: S603


def ran(out: Path, *args: str, script: Path = RUN) -> Ran:
    """
    `run(*args, "--json", out)`, and what it printed and wrote; a failed run fails with its stderr.
    """
    process = run(*args, "--json", str(out), script=script)
    assert process.returncode == 0, process.stderr
    return Ran(process.stdout.splitlines(), json.loads(out.read_text()))


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> Lifecycle:
    """
    lifecycle() of benchmarks/libraries/lifecycle.py, imported as the scripts there import each other.
    """
    monkeypatch.syspath_prepend(str(BENCHMARKS))
    return import_module("libraries.lifecycle").lifecycle  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def resolve_run(tmp_path_factory: pytest.TempPathFactory) -> Ran:
    out = tmp_path_factory.mktemp("resolve") / "benchmarks.json"
    return ran(out, *QUICK, "--only", "resolve", "--only", "memory")


@pytest.fixture(scope="module")
def connect_run(tmp_path_factory: pytest.TempPathFactory) -> Ran:
    out = tmp_path_factory.mktemp("connect") / "connect.json"
    return ran(out, *QUICK, "--only", "connect")


def test_prints_a_markdown_table(resolve_run: Ran) -> None:
    heading = resolve_run.lines[0]
    runtime = f"{platform.python_implementation()} {platform.python_version()}"
    columns = resolve_run.lines[2:4]

    assert heading.startswith("nuke-di ") and runtime in heading
    assert columns[0].startswith("| Scenario ") and columns[1].startswith("|---")


def test_prints_a_row_per_figure(resolve_run: Ran) -> None:
    cold = resolve_run.startswith(f"| {COLD} ")

    assert any("| mixed " in row and "| 10 |" in row for row in cold)
    assert any("| mixed, strings " in row for row in cold)
    assert resolve_run.startswith(f"| {SEEN} ")
    assert resolve_run.startswith("| resolve(), tracemalloc peak ")


def test_writes_the_environment(resolve_run: Ran) -> None:
    expected = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sizes": [10],
        "repeat": 1,
    }

    assert {key: resolve_run.report[key] for key in expected} == expected
    assert len(resolve_run.report["commit"]) == 40


def test_writes_the_figures(resolve_run: Ran) -> None:
    cold = single(resolve_run.where(scenario=COLD, shape="deep"))
    peak = single(resolve_run.where(unit="B"))
    scenarios = {row["scenario"] for row in resolve_run.rows}
    expected = {"n": 10, "unit": "s"}

    assert scenarios == {COLD, SEEN, "resolve(), warm", "resolve(), tracemalloc peak"}
    assert expected.items() <= cold.items()
    assert len(cold["samples"]) == 1
    assert cold["per_client"] == cold["median"] / 10
    assert peak["median"] > 0


@pytest.mark.parametrize("shape", ["wide", "deep", "mixed"])
def test_writes_the_strings_variant_of_every_tree(resolve_run: Ran, shape: str) -> None:
    strings = f"{shape}, strings"
    shapes = {(row["scenario"], row["shape"]) for row in resolve_run.rows}

    assert (COLD, strings) in shapes
    assert (SEEN, strings) in shapes
    # The cache hit has no variant
    assert ("resolve(), warm", strings) not in shapes


def test_connects_an_application_shaped_tree(connect_run: Ran) -> None:
    application = connect_run.application

    assert [row["scenario"] for row in application] == [
        "connect() + disconnect(), wall time",
        "connect() + disconnect(), ideal: the critical path",
        "connect() + disconnect(), above the critical path",
    ]
    # The sleeps of the clients are the figure, so no per-client value
    assert connect_run.where(n=8, per_client=None) == application
    assert all(len(row["samples"]) == 1 for row in application)


def test_measures_the_wall_time_against_the_critical_path(connect_run: Ran) -> None:
    wall, ideal, above = connect_run.application
    difference = wall["samples"][0] - ideal["samples"][0]

    assert min(wall["median"], ideal["median"]) > SLOW_CONNECT
    assert above["median"] == difference


def test_connects_wide_trees_per_client(connect_run: Ran) -> None:
    wide = [row for row in connect_run.rows if row["shape"] == "wide: N independent clients"]

    assert any(row["per_client"] is not None for row in wide)


@pytest.fixture(scope="module")
def compare_run(tmp_path_factory: pytest.TempPathFactory) -> Ran:
    # The libraries are the `compare` dependency group, a default group of `uv sync`
    pytest.importorskip("dishka")
    out = tmp_path_factory.mktemp("compare") / "compare.json"
    return ran(out, *QUICK, "--only", "cold", "--only", "warm", script=COMPARE)


def test_compares_libraries_in_a_table(compare_run: Ran) -> None:
    versions = compare_run.lines[1]
    columns = compare_run.lines[3:5]
    warm = compare_run.startswith("| dependency-injector | warm: the root again")

    assert versions.startswith("nuke-di ") and "dishka " in versions and "wireup " in versions
    assert columns[0].startswith("| Library ") and columns[1].startswith("|---")
    assert any("| deep " in line for line in warm)


def test_sums_the_comparison_up(compare_run: Ran) -> None:
    # The summary after the table: the best per figure in bold, the others with their ratio to it
    cached = single(compare_run.startswith("| A cached root "))

    assert compare_run.startswith("| Lower is better ")
    assert "**" in cached and "×)" in cached  # noqa: RUF001
    assert compare_run.startswith("| Cold start: the same 10 clients with string annotations ")


def test_writes_the_comparison(compare_run: Ran) -> None:
    libraries = {"nuke-di", "dishka", "wireup", "dependency-injector", "injector"}
    scenarios = {row["scenario"] for row in compare_run.rows}

    assert set(compare_run.report["libraries"]) == libraries
    assert {row["library"] for row in compare_run.rows} == libraries
    assert scenarios == {"cold: container, registration, root", "warm: the root again"}


def test_every_library_reads_string_annotations(compare_run: Ran) -> None:
    # Of a tree in a registered module
    rows = [row for row in compare_run.rows if row["shape"].endswith(", strings")]
    strings = {(row["library"], row["shape"]) for row in rows}
    libraries = compare_run.report["libraries"]
    shapes = ("wide", "deep", "mixed")

    assert all(row.get("error") is None for row in compare_run.rows)
    assert strings == {(library, f"{shape}, strings") for library in libraries for shape in shapes}


@pytest.fixture(scope="module")
def slow_connections(tmp_path_factory: pytest.TempPathFactory) -> Ran:
    pytest.importorskip("dishka")
    out = tmp_path_factory.mktemp("slow") / "connect.json"
    return ran(out, "--repeat", "1", "--only", "connect", "--summary", script=COMPARE)


def product_page(slow: Ran, scenario: str) -> dict[str, Any]:
    """
    The row of every library in `scenario` on the product page.
    """
    rows = [row for row in slow.rows if row["shape"] == PRODUCT_PAGE]
    return {row["library"]: row for row in rows if row["scenario"] == scenario}


def test_sums_the_slow_connections_up(slow_connections: Ran) -> None:
    assert slow_connections.startswith("| Startup: 8 clients, connect() of 1–60 ms ")  # noqa: RUF001
    assert slow_connections.startswith("| Shutdown: the same 8 clients ")


def test_starts_the_product_page_along_its_longest_chain(slow_connections: Ran) -> None:
    startup = product_page(slow_connections, "startup: connect() of every client")
    concurrent = (startup["nuke-di"]["median"], startup["dependency-injector"]["median"])
    sequential = (startup["dishka"]["median"], startup["wireup"]["median"])

    assert max(concurrent) < ALONG_THE_CHAIN
    assert min(sequential) > ONE_AFTER_ANOTHER
    assert startup["injector"]["error"] == "no async lifecycle"


def test_stops_the_product_page_along_its_longest_chain(slow_connections: Ran) -> None:
    shutdown = product_page(slow_connections, "shutdown: disconnect() of every client")
    layered = shutdown["dependency-injector"]["median"]

    assert layered > shutdown["nuke-di"]["median"] + LAYERED_ABOVE


def test_gathers_the_features_by_hand(slow_connections: Ran) -> None:
    # wireup connects concurrently what the application gets concurrently by hand, dishka with its lock off
    gathered = product_page(slow_connections, "startup, the root's arguments gathered by hand")
    medians = (gathered["wireup"]["median"], gathered["dishka"]["median"])

    assert max(medians) < GATHERED


@asynccontextmanager
async def closing(close: Callable[[], Awaitable[None]]) -> AsyncIterator[None]:
    """
    `await close()` after the block.
    """
    try:
        yield
    finally:
        await close()


@asynccontextmanager
async def lazily(container: Any, client_class: type[Client]) -> AsyncIterator[Get]:
    """
    The `get()` of `client_class` from the async `container`, closed after the block.
    """
    async with closing(container.close):
        yield lambda: container.get(client_class)


def dishka_container(dishka: ModuleType, factories: Iterable[Callable[..., Any]], **options: Any) -> Any:
    provider = dishka.Provider(scope=dishka.Scope.APP)
    for factory in factories:
        provider.provide(factory)
    return dishka.make_async_container(provider, **options)


def wireup_container(wireup: ModuleType, factory: Callable[..., Any]) -> Any:
    return wireup.create_async_container(injectables=[wireup.injectable(factory)])


async def other_containers(stack: AsyncExitStack, lifecycle: Lifecycle, client_class: type[Client]) -> list[Get]:
    """
    The first `get()` of `client_class` from dishka, wireup and dependency-injector, each with a lifecycle of its
    own, closed with `stack`.
    """
    dishka = pytest.importorskip("dishka")
    wireup = pytest.importorskip("wireup")
    containers = pytest.importorskip("dependency_injector.containers")
    providers = pytest.importorskip("dependency_injector.providers")
    dependency_injector = containers.DynamicContainer()
    dependency_injector.database = providers.Resource(lifecycle(client_class))
    await stack.enter_async_context(closing(dependency_injector.shutdown_resources))
    return [
        await stack.enter_async_context(lazily(dishka_container(dishka, [lifecycle(client_class)]), client_class)),
        await stack.enter_async_context(lazily(wireup_container(wireup, lifecycle(client_class)), client_class)),
        dependency_injector.database,
    ]


async def test_the_other_containers_connect_on_the_first_get(lifecycle: Lifecycle) -> None:
    # What docs/benchmarks.md says of laziness: dishka, wireup and dependency-injector connect nothing when the
    # container is created, only when a client is first asked for; nuke-di connects in `connect()`
    connected: list[str] = []

    class Database(Client):
        async def connect(self) -> None:
            connected.append("connect")

    async with AsyncExitStack() as stack:
        for get in await other_containers(stack, lifecycle, Database):
            assert not connected
            await get()
            assert connected == ["connect"]
            connected.clear()

    deps = Dependencies()
    deps.resolve(Database)
    async with deps:
        assert connected == ["connect"]


async def test_dishka_without_its_lock_builds_a_shared_client_twice(lifecycle: Lifecycle) -> None:
    # What docs/benchmarks.md says of the gathered startup of dishka: its `get()` calls run concurrently only with
    # `lock_factory=None`, and then a client that two of them need is built and connected once per call
    connected: list[int] = []

    class Database(Client):
        async def connect(self) -> None:
            connected.append(id(self))
            await asyncio.sleep(0.01)

    class Users(Client):
        def __init__(self, db: Database) -> None:
            self.db = db

    class Orders(Client):
        def __init__(self, db: Database) -> None:
            self.db = db

    container = dishka_container(
        pytest.importorskip("dishka"),
        map(lifecycle, (Database, Users, Orders)),
        lock_factory=None,
    )

    async with closing(container.close):
        gets = map(container.get, (Users, Orders))
        users, orders = await asyncio.gather(*gets)

    assert (users.db is orders.db, len(connected)) == (False, 2)


def test_draws_the_product_page(tmp_path: Path) -> None:
    pytest.importorskip("dishka")
    out = tmp_path / "product-page.svg"
    figures = ROOT / "docs" / "benchmarks" / "connect-py3.11.json"

    process = run(str(figures), str(out), script=BENCHMARKS / "product_page.py")

    assert process.returncode == 0, process.stderr
    svg = out.read_text()
    assert svg.startswith("<svg ") and "ProductPageApi" in svg and "prefers-color-scheme: dark" in svg
    # The picture of the README is the one the committed figures give
    assert svg == (ROOT / "docs" / "product-page.svg").read_text()


def test_rejects_an_unknown_scenario() -> None:
    process = run("--only", "nothing")

    assert process.returncode == 2
    assert "invalid choice: 'nothing'" in process.stderr
